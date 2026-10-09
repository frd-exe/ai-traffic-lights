import type { Dispatch, SetStateAction } from "react";
import type { AreaResponse, BBox, DemandProfile, DemandResolveRequest, DemandResolveResponse } from "../types/contract.gen";
import type { Scenario } from "../api";
import { MODE_NAMES, type Mode } from "../api/playback";
import { DataBadge } from "./StatusPanel";

type Setter<T> = Dispatch<SetStateAction<T>>;
type Props = {
  area: AreaResponse | null; bbox: BBox; boxValid: boolean; dimensions: [number, number]; drawing: boolean; setDrawing: Setter<boolean>;
  setBbox: Setter<BBox>; centerBox: () => void; ignoreSignals: boolean; setIgnoreSignals: Setter<boolean>; analyze: () => Promise<void>;
  topN: number; setTopN: (n: number) => void; selected: Set<string>; toggle: (id: string) => void;
  source: DemandResolveRequest["source"]; setSource: Setter<DemandResolveRequest["source"]>;
  level: NonNullable<DemandProfile["level"]>; setLevel: Setter<NonNullable<DemandProfile["level"]>>;
  resolved: DemandResolveResponse | null; resolve: () => Promise<void>; mode: Mode; setMode: Setter<Mode>;
  compare: boolean; setCompare: Setter<boolean>; comparisonMode: Mode; setComparisonMode: Setter<Mode>;
  seed: number; setSeed: Setter<number>; speed: number; setSpeed: Setter<number>; scenario: Scenario; setScenario: Setter<Scenario>;
  isMock: boolean | null; busy: string | null; running: boolean; start: () => Promise<void>; stop: () => Promise<void>;
};
const modeOptions = Object.entries(MODE_NAMES).map(([value, text]) => <option key={value} value={value}>{text}</option>);

export default function SetupPanel(p: Props) {
  const disabled = !!p.busy;
  return <aside className="setup-panel" aria-label="Simulation setup">
    <section><div className="step-title"><span>01</span><h2>Choose an area</h2></div><p className="hint">A small part of a real city. Up to 1.5 km × 1.5 km.</p>
      <div className="button-pair"><button className={p.drawing ? "selected-button" : ""} disabled={disabled || p.running} onClick={() => p.setDrawing(!p.drawing)}>⌗ {p.drawing ? "Cancel selection" : "Select area"}</button><button title="Select a 1.2 km square around the map center" disabled={disabled || p.running} onClick={p.centerBox}>Use center</button></div>
      <p className={`area-size ${p.boxValid ? "" : "size-warning"}`} role={p.boxValid ? undefined : "alert"}>{(p.dimensions[0] / 1000).toFixed(2)} km × {(p.dimensions[1] / 1000).toFixed(2)} km{!p.boxValid && " · Select a smaller valid area"}</p>
      <details className="coordinates"><summary>Coordinates · keyboard selection</summary><div className="coordinate-grid">
        {(["west", "south", "east", "north"] as const).map(key => <label key={key}>{key}<input aria-label={`Area ${key}`} type="number" step="0.0001" value={p.bbox[key]} disabled={disabled || p.running} onChange={e => p.setBbox(box => ({ ...box, [key]: Number(e.target.value) }))} /></label>)}
      </div></details>
      <label className="check-row"><input type="checkbox" checked={p.ignoreSignals} disabled={disabled || p.running} onChange={e => p.setIgnoreSignals(e.target.checked)} />Ignore existing OSM signals</label>
      <button className="full-button" onClick={() => void p.analyze()} disabled={disabled || p.running || !p.boxValid}>Analyze area <span>→</span></button>
      {p.area && <p className="hint">{p.area.network.edges.length} roads · {p.area.intersections.length} ranked junctions</p>}
    </section>
    <section><div className="step-title"><span>02</span><h2>Place the signals</h2></div><label className="slider-label" htmlFor="top-n">Recommended locations <strong>{p.topN}</strong></label>
      <input id="top-n" type="range" min="0" max={p.area?.intersections.length ?? 12} value={p.topN} onChange={e => p.setTopN(Number(e.target.value))} disabled={!p.area || disabled} />
      <p className="hint">{p.selected.size} selected{p.running ? " for the next run" : " · click a junction to customize"}</p>
      {p.area?.intersections.length ? <details className="junction-list"><summary>Review ranked junctions</summary><div>{p.area.intersections.map((i, rank) => <label key={i.id} title={`Structural score ${i.structural_score}${i.sim_gain_s == null ? "" : ` · simulated gain ${i.sim_gain_s}s`}`}>
        <input type="checkbox" checked={p.selected.has(i.id)} disabled={disabled} onChange={() => p.toggle(i.id)} /><span>#{rank + 1} Junction <small>Score {i.structural_score.toFixed(2)}{i.sim_gain_s != null && ` · sim. gain ${i.sim_gain_s.toFixed(1)}s`}</small></span><i className={rank < p.topN ? "recommended-mark" : ""} />
      </label>)}</div></details> : <p className="empty-note">Analyze an area to find signal locations.</p>}
    </section>
    <section><div className="step-title"><span>03</span><h2>Traffic demand</h2></div>
      <label>Data source<select aria-label="Data source" value={p.source} disabled={disabled} onChange={e => p.setSource(e.target.value as Props["source"])}><option value="google_live">Google live</option><option value="google_snapshot">Google snapshot</option><option value="baseline">Manual level</option></select></label>
      <label>Demand level<select aria-label="Demand level" value={p.level} disabled={disabled || p.source !== "baseline"} onChange={e => p.setLevel(e.target.value as Props["level"])}><option value="low">Low · 150 veh/h per entry</option><option value="medium">Medium · 300 veh/h per entry</option><option value="high">High · 500 veh/h per entry</option><option value="rush">Rush · 700 veh/h per entry</option></select></label>
      <button className="full-button" disabled={!p.area || disabled} onClick={() => void p.resolve()}>Resolve data <span>↻</span></button>
      {p.resolved ? <><DataBadge status={p.resolved.data_status} /><p className="hint profile-note">Resolved profile: {p.resolved.demand_profile.id}<br />New resolves apply to the next Start. Running demand stays frozen.</p></> : <p className="empty-note">Resolve a demand profile before starting.</p>}
    </section>
    <section><div className="step-title"><span>04</span><h2>Run the experiment</h2></div>
      <label>Controller<select value={p.mode} disabled={disabled || p.running} onChange={e => p.setMode(e.target.value as Mode)}>{modeOptions}</select></label>
      <label className="check-row"><input type="checkbox" checked={p.compare} disabled={disabled || p.running} onChange={e => p.setCompare(e.target.checked)} />Split compare</label>
      {p.compare && <label>Compare against<select value={p.comparisonMode} disabled={disabled || p.running} onChange={e => p.setComparisonMode(e.target.value as Mode)}>{modeOptions}</select></label>}
      <div className="field-pair"><label>Seed<input type="number" aria-label="Seed" min="0" step="1" value={Number.isFinite(p.seed) ? p.seed : ""} disabled={disabled || p.running} onChange={e => p.setSeed(e.target.value === "" ? NaN : Number(e.target.value))} /></label><label>Playback speed<select aria-label="Playback speed" value={p.speed} disabled={disabled || p.running} onChange={e => p.setSpeed(Number(e.target.value))}><option value="1">×1</option><option value="2">×2</option><option value="4">×4</option></select></label></div>
      <div className="button-pair"><button className="primary-button" disabled={!p.area || !p.resolved || disabled || p.running} onClick={() => void p.start()}>▶ Start</button><button disabled={!p.running || disabled} onClick={() => void p.stop()}>■ Stop</button></div>
      {p.compare && <p className="hint">Same seed and demand. Both views share simulation time.</p>}
    </section>
    {p.isMock && <details className="mock-settings" open={p.scenario !== "none"}><summary>Mock scenarios</summary><label>Scenario<select aria-label="Mock scenario" value={p.scenario} disabled={disabled || p.running} onChange={e => p.setScenario(e.target.value as Scenario)}><option value="none">Normal</option><option value="ai_limit">AI limit reached</option><option value="google_down">Google unavailable</option><option value="ai_replay">Replayed AI plans</option></select></label></details>}
    <div className="setup-footer"><span className="status-dot green" />ENGINE ENFORCES SIGNAL SAFETY<small>3s yellow · 2s all red · 7s minimum green</small></div>
  </aside>;
}
