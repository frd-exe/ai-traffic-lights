import { useCallback, useEffect, useRef, useState } from "react";
import type { AreaResponse, DemandProfile, SimDemandRequest, SimStartRequest, SimTick } from "../types/contract.gen";
import { api, openSimSocket, type Scenario } from ".";
import { Playback, type ChartPoint, type FeedEntry, type Mode, type Run } from "./playback";
import { controllerBanner } from "../status";

export function useSimulation(onError: (message: string) => void) {
  const [runs, setRuns] = useState<Run[]>([]);
  const [running, setRunning] = useState(false);
  const [display, setDisplay] = useState<Record<string, SimTick | null>>({});
  const [chart, setChart] = useState<ChartPoint[]>([]);
  const [feed, setFeed] = useState<FeedEntry[]>([]);
  const [toast, setToast] = useState<string | null>(null);
  const [activeProfile, setActiveProfile] = useState<DemandProfile | null>(null);
  const [activeSelection, setActiveSelection] = useState<Set<string>>(new Set());
  const playback = useRef<Playback | null>(null);
  const sessionIds = useRef<string[]>([]);
  const closers = useRef<(() => void)[]>([]);
  const notified = useRef(new Set<string>());
  const metadata = useRef<Run[]>([]);
  const latestT = useRef<Record<string, number>>({}); // newest received sim t per session (not the buffered display t)
  const errors = useRef(onError);
  useEffect(() => { errors.current = onError; }, [onError]);

  const stop = useCallback(async () => {
    playback.current?.freeze();
    closers.current.forEach(close => close()); closers.current = [];
    const results = await Promise.allSettled(sessionIds.current.map(id => api.stop(id)));
    const failures = results.filter(r => r.status === "rejected");
    if (failures.length) throw new Error("Could not stop every backend session. Check the connection and press Stop again.");
    sessionIds.current = [];
    setRunning(false);
    setRuns(rs => rs.map(r => ({ ...r, connected: false })));
  }, []);

  const start = useCallback(async (area: AreaResponse, profile: DemandProfile, selected: Set<string>,
    modes: Mode[], seed: number, speed: number, scenario: Scenario) => {
    await stop();
    const started: Run[] = [];
    try {
      const shared = { area_id: area.area_id, demand_profile_id: profile.id, seed,
        selected_intersections: [...selected].sort(), speed };
      for (const mode of modes) {
        const result = await api.start({ ...shared, mode } satisfies SimStartRequest, scenario);
        started.push({ id: result.session_id, mode, connected: true });
        sessionIds.current.push(result.session_id);
      }
      metadata.current = started;
      playback.current = new Playback(started, area.network.edges, speed);
      notified.current.clear();
      setRuns(started); setRunning(true); setDisplay({}); setChart([]); setFeed([]); setToast(null);
      setActiveProfile(profile); setActiveSelection(new Set(selected));
      latestT.current = {};
      for (const run of started) closers.current.push(openSimSocket(run.id, scenario, tick => {
        latestT.current[run.id] = Math.max(latestT.current[run.id] ?? 0, tick.t);
        try { playback.current?.ingest(tick); } catch (error) { playback.current?.freeze(); errors.current(String(error)); }
      }, message => { playback.current?.freeze(); errors.current(message); }, () => {
        playback.current?.freeze();
        setRuns(rs => rs.map(r => r.id === run.id ? { ...r, connected: false } : r));
      }));
    } catch (error) {
      // Roll back successful starts when the other side could not start.
      if (started.length) { setRuns(started); setRunning(true); }
      await stop(); throw error;
    }
  }, [stop]);

  useEffect(() => {
    let raf = 0, lastUi = 0, lastPoint = -Infinity;
    const frame = (now: number) => {
      const timeline = playback.current;
      timeline?.advance(now);
      if (timeline && now - lastUi >= 100) {
        lastUi = now;
        const ticks = Object.fromEntries(metadata.current.map(r => [r.id, timeline.sample(r.id)]));
        setDisplay(ticks);
        const events = timeline.feed;
        for (const run of metadata.current) {
          const status = ticks[run.id]?.controller_status;
          if (!status) continue;
          const banner = controllerBanner(status);
          const key = `${run.id}:${status.state}:${status.since_t}`;
          if (banner && !notified.current.has(key)) {
            notified.current.add(key); setToast(banner.text);
          }
          if (banner && !events.some(e => e.mode === run.mode && e.intersection_id === null && Math.abs(e.t - status.since_t) < 0.01))
            events.push({ key, mode: run.mode, t: status.since_t, intersection_id: null, text: banner.text });
        }
        setFeed(events.sort((a, b) => b.t - a.t).slice(0, 60));
        if (timeline.t < lastPoint) lastPoint = -Infinity;
        if (Object.values(ticks).every(t => t !== null) && timeline.t - lastPoint >= 1) {
          lastPoint = timeline.t;
          const point: ChartPoint = { t: timeline.t, values: metadata.current.map(r => ({ id: r.id,
            wait: ticks[r.id]!.metrics.avg_wait_s, sinceT: ticks[r.id]!.controller_status.since_t,
            fallback: ["ai_limit_reached", "ai_unavailable"].includes(ticks[r.id]!.controller_status.state) })) };
          setChart(points => [...points, point].slice(-900));
        }
      }
      raf = requestAnimationFrame(frame);
    };
    raf = requestAnimationFrame(frame);
    return () => cancelAnimationFrame(raf);
  }, []);
  useEffect(() => {
    if (!toast) return;
    const timer = setTimeout(() => setToast(null), 7000);
    return () => clearTimeout(timer);
  }, [toast]);
  useEffect(() => () => {
    closers.current.forEach(close => close());
    for (const id of sessionIds.current) void api.stop(id).catch(() => undefined);
  }, []);
  const getTick = useCallback((id: string) => playback.current?.sample(id) ?? null, []);
  /** Live demand change for EVERY running session with the SAME at_t (fair split comparison).
   *  at_t = newest sim time received from any session, rounded up, plus a small lead, so it is in
   *  the future for both backends (the backend rejects an at_t in the past). */
  const changeDemand = useCallback(async (change: Omit<SimDemandRequest, "session_id" | "at_t">, leadS = 3) => {
    const ids = sessionIds.current;
    if (!ids.length) throw new Error("Start a simulation first.");
    const atT = Math.ceil(Math.max(0, ...ids.map(id => latestT.current[id] ?? 0))) + leadS;
    await Promise.all(ids.map(id => api.changeDemand({ session_id: id, at_t: atT, ...change })));
    return atT;
  }, []);
  const clear = useCallback(() => {
    playback.current = null; metadata.current = []; notified.current.clear();
    setRuns([]); setDisplay({}); setChart([]); setFeed([]); setToast(null);
    setActiveProfile(null); setActiveSelection(new Set());
  }, []);
  return { runs, running, display, chart, feed, toast, activeProfile, activeSelection, start, stop, getTick, clear, changeDemand };
}
