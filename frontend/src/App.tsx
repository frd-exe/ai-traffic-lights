import { useCallback, useEffect, useMemo, useState } from "react";
import { api, type Scenario } from "./api";
import { MODE_NAMES, type Mode } from "./api/playback";
import { useSimulation } from "./api/useSimulation";
import MapPane from "./map/MapPane";
import { controllerBanner, dailyCapFallback } from "./status";
import Dashboard, { MetricCards } from "./panels/Dashboard";
import ExplanationFeed from "./panels/ExplanationFeed";
import { ControllerBadge } from "./panels/StatusPanel";
import SetupPanel, { type Level } from "./panels/SetupPanel";
import type { AreaResponse } from "./types/contract.gen";

// Split view: Fixed (left, baseline) vs AI (right), same seed, demand profile and signals.
const SPLIT: Mode[] = ["fixed", "ai"];
const SURGE_FACTOR = 2;
const MAX_MULTIPLIER = 3;
const HELP_KEY = "signalflow.howto";

export default function App() {
  const [area, setArea] = useState<AreaResponse | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [level, setLevel] = useState<Level>("rush");
  const [multiplier, setMultiplier] = useState(1);
  const [seed, setSeed] = useState(42);
  const [speed, setSpeed] = useState(2);
  const [scenario, setScenario] = useState<Scenario>(() => {
    const q = new URLSearchParams(location.search).get("scenario");
    return q === "ai_limit" || q === "ai_replay" ? q : "none";
  });
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [isMock, setIsMock] = useState<boolean | null>(null);
  const [showHelp, setShowHelp] = useState(() => {
    try { return localStorage.getItem(HELP_KEY) !== "dismissed"; } catch { return true; }
  });
  const dismissHelp = () => {
    setShowHelp(false);
    try { localStorage.setItem(HELP_KEY, "dismissed"); } catch { /* storage unavailable: dismiss for this visit only */ }
  };
  const sim = useSimulation(setError);
  const recommendation = useMemo(() => new Set(area?.recommended_ids ?? []), [area]);
  const toggle = useCallback((id: string) => setSelected(prev => {
    const next = new Set(prev); if (next.has(id)) next.delete(id); else next.add(id); return next;
  }), []);

  const act = useCallback(async (label: string, action: () => Promise<void>) => {
    setBusy(label); setError(null);
    try { await action(); } catch (failure) { setError(failure instanceof Error ? failure.message : String(failure)); }
    finally { setBusy(null); }
  }, []);

  // Default: the synthetic demo city with the simulation-based top 3 junctions preselected.
  useEffect(() => {
    let alive = true;
    void api.health().then(r => { if (alive) setIsMock(r.mock ?? false); }).catch(() => undefined);
    api.demoArea().then(demo => {
      if (!alive) return;
      setArea(demo); setSelected(new Set(demo.recommended_ids ?? []));
    }).catch(failure => { if (alive) setError(`Could not load the demo city: ${failure instanceof Error ? failure.message : failure}`); });
    return () => { alive = false; };
  }, []);

  const start = () => act("Starting Fixed vs AI", async () => {
    if (!area) return;
    if (!Number.isSafeInteger(seed) || seed < 0) throw new Error("Seed must be a non-negative integer.");
    // One frozen profile shared by both sessions => identical demand.
    const demand = await api.resolveDemand({ area_id: area.area_id, level, multiplier });
    await sim.start(area, demand.demand_profile, selected, SPLIT, seed, speed, scenario);
    setNotice(null);
  });
  const stop = () => act("Stopping", sim.stop);
  const applyDemand = () => act("Changing demand", async () => {
    const atT = await sim.changeDemand({ level, multiplier });
    setNotice(`Demand set to ${level} ×${multiplier.toFixed(1)} in both sessions from t = ${atT} s.`);
  });
  const surge = () => act("Surge", async () => {
    const next = Math.min(MAX_MULTIPLIER, multiplier * SURGE_FACTOR);
    const atT = await sim.changeDemand({ level, multiplier: next });
    setMultiplier(next);
    setNotice(`Surge: demand ×${next.toFixed(1)} in both sessions from t = ${atT} s.`);
  });

  const ticks = sim.runs.map(r => sim.display[r.id]).filter(t => t !== null && t !== undefined);
  const capFallback = ticks.some(t => dailyCapFallback(t.controller_status));
  const displaySelection = sim.running ? sim.activeSelection : selected;
  const viewRuns = sim.runs.length ? sim.runs : SPLIT.map(mode => ({ id: `preview-${mode}`, mode, connected: false }));
  const commonTime = ticks[0]?.t ?? 0;
  const fixedTick = ticks.find(t => t.metrics.mode === "fixed"), aiTick = ticks.find(t => t.metrics.mode === "ai");
  const gain = fixedTick && aiTick && fixedTick.metrics.avg_wait_s > 0 ? (1 - aiTick.metrics.avg_wait_s / fixedTick.metrics.avg_wait_s) * 100 : null;
  return <div className="app-shell">
    <header className="topbar"><a className="brand" href="#"><span className="brand-mark"><i /><i /><i /></span><span>Signal<span className="brand-accent">Flow</span><small>AI TRAFFIC LAB</small></span></a>
      <span className="city-label" data-testid="city-label">Synthetic demo city</span>
      <span className="environment">{isMock ? "MOCK BACKEND · SIMULATED" : isMock === false ? "SIMULATED TRAFFIC" : "CONNECTING TO BACKEND"}</span></header>
    <div className="workspace">
      <SetupPanel area={area} selected={selected} toggle={toggle} resetSelection={() => setSelected(new Set(area?.recommended_ids ?? []))}
        level={level} setLevel={setLevel} multiplier={multiplier} setMultiplier={setMultiplier}
        seed={seed} setSeed={setSeed} speed={speed} setSpeed={setSpeed} scenario={scenario} setScenario={setScenario}
        isMock={isMock} busy={busy} running={sim.running} start={start} stop={stop} applyDemand={applyDemand} surge={surge} />
      <main className="simulation-space">
        <div className="workspace-heading"><div><span className="eyebrow">FIXED TIMERS VS AI · SAME SEED, SAME DEMAND</span><h1>Synthetic demo city</h1></div>
          <div className="clock"><span className={`status-dot ${sim.running ? "green" : ""}`} />{sim.running ? "LIVE" : sim.runs.length ? "STOPPED" : "READY"}<strong data-testid="shared-time">t = {commonTime.toFixed(1)}s</strong></div></div>
        {showHelp && <div className="howto" role="note" data-testid="howto"><div><strong>How to use</strong><ol>
          <li>Pick <b>Rush</b> as the demand level (left panel).</li>
          <li>Press <b>▶ Start</b> to run Fixed timers vs AI side by side.</li>
          <li>Press <b>⚡ Surge</b> and watch the metrics below each map.</li></ol></div>
          <button aria-label="Dismiss how to use" onClick={dismissHelp}>×</button></div>}
        {error && <div className="error-banner" role="alert"><span>{error}</span><button aria-label="Dismiss error" onClick={() => setError(null)}>×</button></div>}
        {busy && <div className="operation-status" role="status"><span className="spinner" />{busy}…</div>}
        <div className="banners" aria-live="polite">
          {sim.runs.map(r => {
            const status = sim.display[r.id]?.controller_status, banner = status ? controllerBanner(status) : null;
            return banner ? <div key={r.id} className={`banner ${banner.level}`} data-testid="ai-banner"><span>!</span><div>{banner.text}<small>{MODE_NAMES[r.mode]} · {status?.calls_today} / {status?.daily_cap} calls today</small></div></div> : null;
          })}
          {notice && <div className="banner info" data-testid="demand-notice"><span>⚡</span><div>{notice}</div></div>}
          {capFallback && <button className="reset-button" disabled={!!busy} onClick={() => void act("Re-enabling AI", async () => {
            const result = await api.aiReset(); if (!result.reset) throw new Error(result.message);
          })}>Re-enable AI</button>}
        </div>
        <div className="map-grid split">
          {viewRuns.map((run, index) => <section className="session-card" key={run.id} data-testid={`session-${index}`}>
            <div className="session-heading"><div><span className={`session-color color-${index}`} /><h2>{MODE_NAMES[run.mode]}</h2><small>{index === 0 ? "BASELINE" : "AI"}</small></div>
              <ControllerBadge status={sim.display[run.id]?.controller_status} /></div>
            <MapPane area={area} bbox={null} selected={displaySelection} recommended={recommendation} profile={null}
              onToggle={sim.running ? () => undefined : toggle} getTick={() => sim.getTick(run.id)} />
            <div className="session-footer"><span>{sim.running ? (run.connected ? "● Connected" : "Connection closed") : "○ Not running"}</span>
              <span data-testid={`session-time-${index}`}>Simulated t = {(sim.display[run.id]?.t ?? 0).toFixed(1)}s</span><span>{sim.display[run.id]?.vehicles.length ?? 0} cars</span></div>
            <MetricCards tick={sim.display[run.id] ?? null} />
          </section>)}
        </div>
        <p className="metrics-note" data-testid="metrics-note">ⓘ Gains are modest on balanced demand: fixed timers only lose a lot when traffic is uneven or changing.</p>
        <div className="map-legend"><span><i className="legend-ring" />Signalised junction</span><span><i className="legend-recommended" />Recommended by simulation</span><span className="speed-key">Cars: stopped <i /> moving</span></div>
        {sim.runs.length > 0 && <div className="demo-summary"><span className="eyebrow">LIVE SIMULATED RESULT</span><div><strong>Fixed {fixedTick ? fixedTick.metrics.avg_wait_s.toFixed(1) : "—"}s</strong><span>→</span><strong>AI {aiTick ? aiTick.metrics.avg_wait_s.toFixed(1) : "—"}s</strong><span data-testid="gain">{gain === null ? "Collecting data…" : `${Math.abs(gain).toFixed(1)}% ${gain >= 0 ? "less" : "more"} waiting`}</span></div></div>}
        <Dashboard runs={sim.runs} points={sim.chart} />
        <ExplanationFeed entries={sim.feed} />
        <footer className="workspace-footer"><span>Everything here is simulated: a synthetic grid, simulated demand and a simplified traffic model. Not a real-world measurement.</span><span>Same demand · same seed · aligned simulation time</span></footer>
      </main>
    </div>
    {sim.toast && <div className="toast" role="alert" data-testid="fallback-toast">{sim.toast}</div>}
  </div>;
}
