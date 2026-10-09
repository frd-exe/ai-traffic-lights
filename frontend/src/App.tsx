import { useCallback, useEffect, useRef, useState } from "react";
import type { Map as MapLibreMap } from "maplibre-gl";
import MapView from "./MapView";
import { bboxOf } from "./geo";
import { api, openSimSocket, type Scenario } from "./api";
import { controllerBanner, dataBanner } from "./status";
import type { AreaResponse, Explanation, SimStartRequest, SimTick } from "./types/contract.gen";

type Mode = SimStartRequest["mode"];
type Level = "low" | "medium" | "high" | "rush";
type Run = { sessionId: string; mode: Mode; tick: SimTick | null };

const MODES: Mode[] = ["ai", "max_pressure", "webster", "fixed"];
const LEVELS: Level[] = ["low", "medium", "high", "rush"];
const SCENARIOS: Scenario[] = ["none", "ai_limit", "ai_replay"];
const DEMAND_LEAD_S = 2; // a mid-run change applies this many sim-seconds ahead, identically for all sessions
const fmt = (x: number | undefined) => (x === undefined ? "-" : x.toFixed(1));

export default function App() {
  const mapRef = useRef<MapLibreMap | null>(null);
  const [area, setArea] = useState<AreaResponse | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [mode, setMode] = useState<Mode>("ai");
  const [compare, setCompare] = useState(true);
  const [level, setLevel] = useState<Level>("medium");
  const [multiplier, setMultiplier] = useState(1);
  const [scenario, setScenario] = useState<Scenario>("none");
  const [runs, setRuns] = useState<Run[]>([]);
  const [feed, setFeed] = useState<Explanation[]>([]);
  const [toast, setToast] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const closers = useRef<(() => void)[]>([]);
  const toasted = useRef<Set<string>>(new Set());

  const loadArea = async (demo: boolean) => {
    setError(null);
    try {
      const map = mapRef.current;
      const bbox = map ? bboxOf(map) : { west: 2.16, south: 41.385, east: 2.17, north: 41.395 };
      const res = demo ? await api.demoArea() : await api.area({ bbox });
      setArea(res);
      setSelected(new Set(res.recommended_ids ?? []));
    } catch (e) {
      setError(String(e));
    }
  };

  const toggle = useCallback((id: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  const onTick = useCallback((tick: SimTick) => {
    setRuns((rs) => rs.map((r) => (r.sessionId === tick.session_id ? { ...r, tick } : r)));
    if (tick.explanations.length) setFeed((f) => [...tick.explanations.map((e) => ({ ...e })), ...f].slice(0, 50));
    const cs = tick.controller_status;
    const key = `${tick.session_id}:${cs.state}`;
    if ((cs.state === "ai_limit_reached" || cs.state === "ai_unavailable") && !toasted.current.has(key)) {
      toasted.current.add(key);
      setToast(controllerBanner(cs)?.text ?? cs.message);
      console.warn(`[controller] ${cs.message}`);
    }
  }, []);

  const stopAll = useCallback(async () => {
    closers.current.forEach((c) => c());
    closers.current = [];
    const ids = runs.map((r) => r.sessionId);
    await Promise.allSettled(ids.map((id) => api.stop(id)));
  }, [runs]);

  const start = async () => {
    if (!area) return;
    setError(null);
    await stopAll();
    setFeed([]);
    toasted.current.clear();
    try {
      // Resolve demand ONCE; every compared session shares the frozen profile + seed.
      const demand = await api.resolveDemand({ area_id: area.area_id, level, multiplier });
      const modes: Mode[] = compare && mode !== "fixed" ? [mode, "fixed"] : [mode];
      const started: Run[] = [];
      for (const m of modes) {
        const { session_id } = await api.start(
          {
            area_id: area.area_id, mode: m, demand_profile_id: demand.demand_profile.id, seed: 42,
            selected_intersections: [...selected], speed: 1,
          },
          scenario,
        );
        started.push({ sessionId: session_id, mode: m, tick: null });
        closers.current.push(openSimSocket(session_id, scenario, onTick, setError));
      }
      setRuns(started);
    } catch (e) {
      setError(String(e));
    }
  };

  // Mid-run demand change: the same change at the same at_t for every session (fair comparison).
  const applyDemand = async () => {
    if (!runs.length) return;
    setError(null);
    const atT = Math.ceil(Math.max(...runs.map((r) => r.tick?.t ?? 0))) + DEMAND_LEAD_S;
    try {
      await Promise.all(runs.map((r) => api.changeDemand({ session_id: r.sessionId, level, multiplier, at_t: atT })));
    } catch (e) {
      setError(String(e));
    }
  };

  useEffect(() => {
    if (!toast) return;
    const id = setTimeout(() => setToast(null), 6000);
    return () => clearTimeout(id);
  }, [toast]);

  useEffect(() => () => closers.current.forEach((c) => c()), []);

  const primary = runs[0]?.tick ?? null;
  const banners = runs.flatMap((r) => {
    const t = r.tick;
    if (!t) return [];
    return [controllerBanner(t.controller_status), r === runs[0] ? dataBanner(t.data_status) : null]
      .filter((b) => b !== null)
      .map((b) => ({ ...b, key: `${r.sessionId}:${b.text}`, label: runs.length > 1 ? `[${r.mode}] ` : "" }));
  });

  return (
    <div className="app">
      <aside className="panel">
        <h1>AI Traffic Lights</h1>
        <div className="row">
          <button onClick={() => void loadArea(true)}>1. Demo city</button>
          <button onClick={() => void loadArea(false)}>or analyze view</button>
        </div>
        <p className="hint">
          {area
            ? `${area.source === "synthetic_grid" ? "Demo city (synthetic grid)" : "OpenStreetMap area"}: ` +
              `${area.intersections.length} junctions, ${selected.size} signalised (click to toggle)`
            : "Load the demo city, or pan the map and analyze the view."}
        </p>
        <label>
          Mode
          <select value={mode} onChange={(e) => setMode(e.target.value as Mode)}>
            {MODES.map((m) => <option key={m}>{m}</option>)}
          </select>
        </label>
        <label className="row">
          <input type="checkbox" checked={compare} onChange={(e) => setCompare(e.target.checked)} /> Compare vs fixed timers
        </label>
        <label>
          Simulated demand
          <select value={level} onChange={(e) => setLevel(e.target.value as Level)}>
            {LEVELS.map((l) => <option key={l}>{l}</option>)}
          </select>
        </label>
        <label>
          Multiplier ×{multiplier.toFixed(1)}
          <input type="range" min={0.2} max={3} step={0.1} value={multiplier}
            onChange={(e) => setMultiplier(Number(e.target.value))} />
        </label>
        <label>
          Mock scenario
          <select value={scenario} onChange={(e) => setScenario(e.target.value as Scenario)}>
            {SCENARIOS.map((s) => <option key={s}>{s}</option>)}
          </select>
        </label>
        <div className="row">
          <button onClick={start} disabled={!area}>2. Start</button>
          <button onClick={() => void applyDemand()} disabled={!runs.length}>Apply demand</button>
          <button onClick={() => void stopAll()} disabled={!runs.length}>Stop</button>
        </div>
        {error && <p className="error">{error}</p>}

        <h2>Metrics {primary && <small>t={primary.t.toFixed(0)} s</small>}</h2>
        <table>
          <thead>
            <tr><th />{runs.map((r) => <th key={r.sessionId}>{r.mode}</th>)}</tr>
          </thead>
          <tbody>
            {(["avg_wait_s", "trip_delay_s", "avg_queue", "throughput_per_min"] as const).map((k) => (
              <tr key={k}>
                <td>{k}</td>
                {runs.map((r) => <td key={r.sessionId}>{fmt(r.tick?.metrics[k])}</td>)}
              </tr>
            ))}
            <tr>
              <td>controller</td>
              {runs.map((r) => <td key={r.sessionId}>{r.tick?.metrics.effective_controller ?? "-"}</td>)}
            </tr>
          </tbody>
        </table>
        <p className="hint">Demand: {primary?.data_status.message ?? "Simulated demand"}</p>

        <h2>AI explanations</h2>
        <ul className="feed">
          {feed.map((e, i) => (
            <li key={`${e.t}-${i}`} className={e.intersection_id ? "" : "session"}>
              <b>t={e.t.toFixed(0)}</b> {e.text}
            </li>
          ))}
        </ul>
      </aside>
      <main className="main">
        <div className="banners">
          {banners.map((b) => (
            <div key={b.key} className={`banner ${b.level}`} role="status">{b.label}{b.text}</div>
          ))}
        </div>
        <MapView
          area={area}
          selected={selected}
          vehicles={primary?.vehicles ?? []}
          signals={primary?.signals ?? []}
          onToggle={toggle}
          mapRef={mapRef}
        />
        {toast && <div className="toast" role="alert">{toast}</div>}
      </main>
    </div>
  );
}
