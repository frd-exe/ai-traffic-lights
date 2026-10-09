import type { Dispatch, SetStateAction } from "react";
import type { AreaResponse, DemandProfile } from "../types/contract.gen";
import type { Scenario } from "../api";

type Setter<T> = Dispatch<SetStateAction<T>>;
export type Level = NonNullable<DemandProfile["level"]>;
type Props = {
  area: AreaResponse | null; selected: Set<string>; toggle: (id: string) => void; resetSelection: () => void;
  level: Level; setLevel: Setter<Level>; multiplier: number; setMultiplier: Setter<number>;
  seed: number; setSeed: Setter<number>; speed: number; setSpeed: Setter<number>;
  scenario: Scenario; setScenario: Setter<Scenario>; isMock: boolean | null;
  busy: string | null; running: boolean; start: () => Promise<void>; stop: () => Promise<void>;
  applyDemand: () => Promise<void>; surge: () => Promise<void>;
};
// Per-entry flows from backend/contract/constants.py LEVEL_FLOW_VEH_PER_H (contract 0.3).
const LEVELS: [Level, string][] = [["low", "Low · 120 veh/h per entry"], ["medium", "Medium · 200 veh/h per entry"],
  ["high", "High · 280 veh/h per entry"], ["rush", "Rush · 380 veh/h per entry"]];

export default function SetupPanel(p: Props) {
  const disabled = !!p.busy;
  const recommended = new Set(p.area?.recommended_ids ?? []);
  return <aside className="setup-panel" aria-label="Simulation setup">
    <section><div className="step-title"><span>01</span><h2>Synthetic demo city</h2></div>
      <p className="hint">A 3 × 3 grid of 150 m blocks with one primary street. Simulated, not a real place.</p>
      {p.area ? <p className="hint">{p.area.network.edges.length} road links · {p.area.intersections.length} junctions ·
        {" "}{p.selected.size} signalised</p> : <p className="empty-note">Loading the demo city…</p>}
      {p.area?.intersections.length ? <details className="junction-list"><summary>Signalised junctions (top 3 preselected by simulation)</summary><div>
        {p.area.intersections.map((i, rank) => <label key={i.id} title={`Structural score ${i.structural_score}${i.sim_gain_s == null ? "" : ` · simulated gain ${i.sim_gain_s}s`}`}>
          <input type="checkbox" checked={p.selected.has(i.id)} disabled={disabled || p.running} onChange={() => p.toggle(i.id)} />
          <span>#{rank + 1} Junction <small>Score {i.structural_score.toFixed(2)}{i.sim_gain_s != null && ` · sim. gain ${i.sim_gain_s.toFixed(1)}s`}</small></span>
          <i className={recommended.has(i.id) ? "recommended-mark" : ""} /></label>)}
      </div></details> : null}
      {p.area && <button className="full-button" disabled={disabled || p.running} onClick={p.resetSelection}>Use recommended 3 <span>↺</span></button>}
    </section>
    <section><div className="step-title"><span>02</span><h2>Traffic demand</h2></div>
      <p className="hint">Simulated demand: a level per entry road times a multiplier.</p>
      <label>Demand level<select aria-label="Demand level" value={p.level} disabled={disabled}
        onChange={e => p.setLevel(e.target.value as Level)}>{LEVELS.map(([v, t]) => <option key={v} value={v}>{t}</option>)}</select></label>
      <label className="slider-label" htmlFor="multiplier">Multiplier <strong>×{p.multiplier.toFixed(1)}</strong></label>
      <input id="multiplier" aria-label="Demand multiplier" type="range" min="0.2" max="3" step="0.1" value={p.multiplier}
        disabled={disabled} onChange={e => p.setMultiplier(Number(e.target.value))} />
      <div className="button-pair">
        <button disabled={!p.running || disabled} onClick={() => void p.applyDemand()} title="Apply level and multiplier to both running sessions at the same simulation time">Apply live</button>
        <button className="surge-button" disabled={!p.running || disabled} onClick={() => void p.surge()} title="Double the demand (×2 multiplier, max ×3) in both sessions at the same simulation time">⚡ Surge</button>
      </div>
      <p className="hint">Changes reach both split sessions at the same simulation time.</p>
    </section>
    <section><div className="step-title"><span>03</span><h2>Fixed vs AI</h2></div>
      <p className="hint">Left: fixed 30 s timers. Right: AI (Gemini + max-pressure). Same seed, same demand, same signals.</p>
      <div className="field-pair"><label>Seed<input type="number" aria-label="Seed" min="0" step="1" value={Number.isFinite(p.seed) ? p.seed : ""} disabled={disabled || p.running}
        onChange={e => p.setSeed(e.target.value === "" ? NaN : Number(e.target.value))} /></label>
        <label>Playback speed<select aria-label="Playback speed" value={p.speed} disabled={disabled || p.running} onChange={e => p.setSpeed(Number(e.target.value))}>
          <option value="1">×1</option><option value="2">×2</option><option value="4">×4</option></select></label></div>
      <div className="button-pair"><button className="primary-button" disabled={!p.area || disabled || p.running || !p.selected.size} onClick={() => void p.start()}>▶ Start</button>
        <button disabled={!p.running || disabled} onClick={() => void p.stop()}>■ Stop</button></div>
    </section>
    {p.isMock && <details className="mock-settings" open={p.scenario !== "none"}><summary>Mock scenarios</summary><label>Scenario<select aria-label="Mock scenario" value={p.scenario} disabled={disabled || p.running}
      onChange={e => p.setScenario(e.target.value as Scenario)}><option value="none">Normal</option><option value="ai_limit">AI limit reached</option><option value="ai_replay">Replayed AI plans</option></select></label></details>}
    <div className="setup-footer"><span className="status-dot green" />ENGINE ENFORCES SIGNAL SAFETY<small>3s yellow · 2s all red · 7s minimum green · 60s max red</small></div>
  </aside>;
}
