import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { Map as MapLibreMap } from "maplibre-gl";
import { api, type Scenario } from "./api";
import { MODE_NAMES, type Mode } from "./api/playback";
import { useSimulation } from "./api/useSimulation";
import MapPane from "./map/MapPane";
import { bboxSize, DEMO_BBOX } from "./map/geometry";
import { controllerBanner, dailyCapFallback, dataBanner } from "./status";
import Dashboard, { MetricCards } from "./panels/Dashboard";
import ExplanationFeed from "./panels/ExplanationFeed";
import SearchBox from "./panels/SearchBox";
import { focusResult } from "./map/search";
import { ControllerBadge } from "./panels/StatusPanel";
import SetupPanel from "./panels/SetupPanel";
import type { AreaResponse, BBox, DemandProfile, DemandResolveRequest, DemandResolveResponse } from "./types/contract.gen";

export default function App() {
  const primaryMap = useRef<MapLibreMap | null>(null);
  const secondaryMap = useRef<MapLibreMap | null>(null);
  const [area, setArea] = useState<AreaResponse | null>(null);
  const [bbox, setBbox] = useState<BBox>(DEMO_BBOX);
  const [drawing, setDrawing] = useState(false);
  const [ignoreSignals, setIgnoreSignals] = useState(false);
  const [topN, setTopN] = useState(6);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [source, setSource] = useState<DemandResolveRequest["source"]>("google_live");
  const [level, setLevel] = useState<NonNullable<DemandProfile["level"]>>("medium");
  const [resolved, setResolved] = useState<DemandResolveResponse | null>(null);
  const [mode, setMode] = useState<Mode>("ai");
  const [compare, setCompare] = useState(true);
  const [comparisonMode, setComparisonMode] = useState<Mode>("fixed");
  const [seed, setSeed] = useState(42);
  const [speed, setSpeed] = useState(1);
  const [scenario, setScenario] = useState<Scenario>(() => {
    const q = new URLSearchParams(location.search).get("scenario");
    return q === "ai_limit" || q === "google_down" || q === "ai_replay" ? q : "none";
  });
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isMock, setIsMock] = useState<boolean | null>(null);
  const [demo, setDemo] = useState(false);
  const sim = useSimulation(setError);
  const recommendation = useMemo(() => new Set(area?.intersections.slice(0, topN).map(i => i.id) ?? []), [area, topN]);
  const [width, height] = bboxSize(bbox);
  const boxValid = width > 0 && height > 0 && width <= 1500 && height <= 1500 &&
    bbox.west >= -180 && bbox.east <= 180 && bbox.south >= -90 && bbox.north <= 90;
  const chooseBox = useCallback((box: BBox) => { setBbox(box); setDrawing(false); }, []);
  const toggle = useCallback((id: string) => setSelected(prev => {
    const next = new Set(prev); if (next.has(id)) next.delete(id); else next.add(id); return next;
  }), []);
  const readyPrimary = useCallback((map: MapLibreMap) => { primaryMap.current = map; }, []);
  const readySecondary = useCallback((map: MapLibreMap) => { secondaryMap.current = map; }, []);
  useEffect(() => { void api.health().then(result => setIsMock(result.mock ?? false)).catch(() => setIsMock(null)); }, []);
  useEffect(() => {
    const key = (event: KeyboardEvent) => { if (event.key === "Escape") setDrawing(false); };
    window.addEventListener("keydown", key); return () => window.removeEventListener("keydown", key);
  }, []);
  const act = async (label: string, action: () => Promise<void>) => {
    if (busy) return;
    setBusy(label); setError(null);
    try { await action(); } catch (failure) { setError(failure instanceof Error ? failure.message : String(failure)); }
    finally { setBusy(null); }
  };
  const analyze = () => act("Analyzing area", async () => {
    if (!boxValid) throw new Error("Choose an area no larger than 1.5 km × 1.5 km.");
    const result = await api.area({ bbox, ignore_osm_signals: ignoreSignals });
    sim.clear();
    setArea(result); setResolved(null); setDemo(false);
    const recommendedCount = Math.min(topN, result.intersections.length);
    setTopN(recommendedCount);
    setSelected(new Set(result.intersections.slice(0, recommendedCount).map(i => i.id)));
  });
  const resolve = () => act("Resolving traffic data", async () => {
    if (!area) return;
    setResolved(await api.resolveDemand({ area_id: area.area_id, source, level }, scenario));
  });
  const start = () => act("Starting simulation", async () => {
    if (!area || !resolved) return;
    if (!Number.isSafeInteger(seed) || seed < 0) throw new Error("Seed must be a non-negative integer.");
    if (compare && mode === comparisonMode) throw new Error("Choose two different controllers to compare.");
    setDemo(false);
    await sim.start(area, resolved.demand_profile, selected, compare ? [comparisonMode, mode] : [mode], seed, speed, scenario);
  });
  const stop = () => act("Stopping simulation", sim.stop);
  const demoStart = () => act("Preparing demo", async () => {
    await sim.stop();
    const result = await api.area({ bbox: DEMO_BBOX, ignore_osm_signals: false });
    const signals = new Set(result.intersections.slice(0, 6).map(i => i.id));
    const demand = await api.resolveDemand({ area_id: result.area_id, source: "google_snapshot", level: "rush" }, scenario);
    setArea(result); setBbox(DEMO_BBOX); setTopN(Math.min(6, result.intersections.length)); setSelected(signals); setResolved(demand);
    setDrawing(false); setSource("google_snapshot"); setLevel("rush"); setCompare(true); setComparisonMode("fixed"); setMode("ai"); setDemo(true);
    await sim.start(result, demand.demand_profile, signals, ["fixed", "ai"], seed, speed, scenario);
  });
  const centerBox = () => {
    const center = primaryMap.current?.getCenter() ?? { lng: 2.165, lat: 41.39 };
    const lat = 600 / 111320, lon = lat / Math.cos(center.lat * Math.PI / 180);
    chooseBox({ west: center.lng - lon, east: center.lng + lon, south: center.lat - lat, north: center.lat + lat });
  };
  const setRecommendations = (n: number) => {
    setTopN(n); setSelected(new Set(area?.intersections.slice(0, n).map(i => i.id) ?? []));
  };
  const ticks = sim.runs.map(r => sim.display[r.id]).filter(t => t !== null && t !== undefined);
  const dataStatus = sim.running ? ticks[0]?.data_status ?? resolved?.data_status : resolved?.data_status ?? ticks[0]?.data_status;
  const googleBanner = dataStatus ? dataBanner(dataStatus) : null;
  const capFallback = ticks.some(t => dailyCapFallback(t.controller_status));
  const displaySelection = sim.running ? sim.activeSelection : selected;
  const displayProfile = sim.running ? sim.activeProfile : resolved?.demand_profile ?? null;
  const viewRuns = sim.runs.length ? sim.runs : [{ id: "preview", mode, connected: false }];
  const commonTime = ticks[0]?.t ?? 0;
  const fixedTick = ticks.find(t => t.metrics.mode === "fixed"), aiTick = ticks.find(t => t.metrics.mode === "ai");
  const gain = fixedTick && aiTick && fixedTick.metrics.avg_wait_s > 0 ? (1 - aiTick.metrics.avg_wait_s / fixedTick.metrics.avg_wait_s) * 100 : null;
  return <div className="app-shell">
    <header className="topbar"><a className="brand" href="#"><span className="brand-mark"><i /><i /><i /></span><span>Signal<span className="brand-accent">Flow</span><small>AI TRAFFIC LAB</small></span></a>
      <SearchBox onResult={result => { focusResult(primaryMap.current, result); focusResult(secondaryMap.current, result); }} onError={setError} />
      <span className="environment">{isMock ? "MOCK BACKEND · SIMULATED" : isMock === false ? "SIMULATION WORKSPACE" : "CONNECTING TO BACKEND"}</span>
      <button className="demo-button" onClick={() => void demoStart()} disabled={!!busy}>▷ Demo mode</button></header>
    <div className="workspace">
      <SetupPanel area={area} bbox={bbox} boxValid={boxValid} dimensions={[width, height]} drawing={drawing} setDrawing={setDrawing}
        setBbox={setBbox} centerBox={centerBox} ignoreSignals={ignoreSignals} setIgnoreSignals={setIgnoreSignals} analyze={analyze}
        topN={topN} setTopN={setRecommendations} selected={selected} toggle={toggle} source={source} setSource={setSource}
        level={level} setLevel={setLevel} resolved={resolved} resolve={resolve} mode={mode} setMode={setMode}
        compare={compare} setCompare={setCompare} comparisonMode={comparisonMode} setComparisonMode={setComparisonMode}
        seed={seed} setSeed={setSeed} speed={speed} setSpeed={setSpeed} scenario={scenario} setScenario={setScenario}
        isMock={isMock} busy={busy} running={sim.running} start={start} stop={stop} />
      <main className="simulation-space">
        <div className="workspace-heading"><div><span className="eyebrow">TRAFFIC SIMULATION</span><h1>{area ? "Every junction. A better flow." : "A city in motion."}</h1></div>
          <div className="clock"><span className={`status-dot ${sim.running ? "green" : ""}`} />{sim.running ? "LIVE" : sim.runs.length ? "STOPPED" : "READY"}<strong data-testid="shared-time">t = {commonTime.toFixed(1)}s</strong></div></div>
        {error && <div className="error-banner" role="alert"><span>{error}</span><button aria-label="Dismiss error" onClick={() => setError(null)}>×</button></div>}
        {busy && <div className="operation-status" role="status"><span className="spinner" />{busy}…</div>}
        <div className="banners" aria-live="polite">
          {googleBanner && <div className={`banner ${googleBanner.level}`} data-testid="google-banner"><span>◈</span><div>{googleBanner.text}<small>{dataStatus?.message}</small></div></div>}
          {sim.runs.map(r => {
            const status = sim.display[r.id]?.controller_status, banner = status ? controllerBanner(status) : null;
            return banner ? <div key={r.id} className={`banner ${banner.level}`} data-testid="ai-banner"><span>!</span><div>{banner.text}<small>{MODE_NAMES[r.mode]} · {status?.calls_today} / {status?.daily_cap} calls today</small></div></div> : null;
          })}
          {capFallback && <button className="reset-button" disabled={!!busy} onClick={() => void act("Re-enabling AI", async () => {
            const result = await api.aiReset(); if (!result.reset) throw new Error(result.message);
            setError(isMock ? "Daily counter reset. The mock scenario stays in fallback; stop and start with Normal to resume AI." : null);
          })}>Re-enable AI</button>}
        </div>
        <div className={`map-grid ${viewRuns.length > 1 ? "split" : ""}`}>
          {viewRuns.map((run, index) => <section className="session-card" key={run.id} data-testid={`session-${index}`}>
            <div className="session-heading"><div><span className={`session-color color-${index}`} /><h2>{MODE_NAMES[run.mode]}</h2>{viewRuns.length > 1 && <small>{index === 0 ? "BASELINE" : "COMPARISON"}</small>}</div>
              <ControllerBadge status={sim.display[run.id]?.controller_status} /></div>
            <MapPane area={area} bbox={bbox} selected={displaySelection} recommended={recommendation} profile={displayProfile}
              drawing={drawing && index === 0} onBox={chooseBox} onToggle={toggle} onReady={index === 0 ? readyPrimary : readySecondary}
              getTick={() => sim.getTick(run.id)} />
            {!area && <div className="map-welcome"><strong>Choose where to make a difference.</strong><span>Select a small area, analyze its roads, then start a simulation.</span></div>}
            <div className="session-footer"><span>{sim.running ? (run.connected ? "● Connected" : "Connection closed") : "○ Not running"}</span>
              <span data-testid={`session-time-${index}`}>Simulated t = {(sim.display[run.id]?.t ?? 0).toFixed(1)}s</span><span>{sim.display[run.id]?.vehicles.length ?? 0} cars</span></div>
            <MetricCards tick={sim.display[run.id] ?? null} />
          </section>)}
        </div>
        <div className="map-legend"><span><i className="legend-ring" />Selected signal</span><span><i className="legend-recommended" />Recommended</span><span className="speed-key">Cars: stopped <i /> moving</span><span>Entry roads: 1× <i className="ratio-key" /> 2× congestion</span></div>
        {demo && <div className="demo-summary"><span className="eyebrow">DEMO · LIVE SIMULATED RESULTS</span><div><strong>Fixed {fixedTick ? fixedTick.metrics.avg_wait_s.toFixed(1) : "—"}s</strong><span>→</span><strong>AI {aiTick ? aiTick.metrics.avg_wait_s.toFixed(1) : "—"}s</strong><span>{gain === null ? "Collecting data…" : `${Math.abs(gain).toFixed(1)}% ${gain >= 0 ? "less" : "more"} waiting`}</span></div>
          {!resolved?.demand_profile.level && <small>Rush requested. This snapshot uses recorded entry scales; the backend does not apply level overrides to Google data.</small>}</div>}
        <Dashboard runs={sim.runs} points={sim.chart} />
        <ExplanationFeed entries={sim.feed} />
        <footer className="workspace-footer"><span>All results are simulated. Mock results illustrate behavior and are not measured AI improvements.</span><span>{sim.runs.length > 1 ? "Same demand · same seed · aligned simulation time" : "Headless engine · live visualization"}</span></footer>
      </main>
    </div>
    {sim.toast && <div className="toast" role="alert" data-testid="fallback-toast">{sim.toast}</div>}
  </div>;
}
