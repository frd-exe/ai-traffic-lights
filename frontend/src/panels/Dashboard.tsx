import type { SimTick } from "../types/contract.gen";
import { MODE_NAMES, type ChartPoint, type Run } from "../api/playback";

const labels = [
  ["avg_wait_s", "Avg wait", "s"], ["trip_delay_s", "Trip delay", "s"], ["avg_queue", "Avg queue", "cars"],
  ["throughput_per_min", "Throughput", "/ min"], ["blocked_spawns", "Blocked spawns", ""], ["deadlocks", "Deadlocks", ""],
] as const;
export function MetricCards({ tick }: { tick: SimTick | null }) {
  return <div className="metric-grid">{labels.map(([key, label, unit]) => <div className="metric" key={key}>
    <small>{label}</small><strong>{tick ? tick.metrics[key].toFixed(key === "blocked_spawns" || key === "deadlocks" ? 0 : 1) : "—"}<span>{unit}</span></strong>
  </div>)}</div>;
}

export default function Dashboard({ runs, points }: { runs: Run[]; points: ChartPoint[] }) {
  const width = 760, height = 152, left = 36, right = 18, top = 15, bottom = 30;
  const start = points[0]?.t ?? 0;
  const end = Math.max(start + 30, points.at(-1)?.t ?? 30);
  const maxWait = Math.max(10, ...points.flatMap(p => p.values.map(v => v.wait))) * 1.15;
  const x = (t: number) => left + (t - start) / (end - start) * (width - left - right);
  const y = (wait: number) => height - bottom - wait / maxWait * (height - top - bottom);
  const paths: { id: string; path: string; fallback: boolean; color: string }[] = [];
  runs.forEach((run, index) => {
    let path = "", fallback = false, previous: ChartPoint | null = null;
    for (const point of points) {
      const value = point.values.find(v => v.id === run.id);
      if (!value) continue;
      if (path && value.fallback !== fallback) {
        paths.push({ id: `${run.id}:${paths.length}`, path, fallback, color: index ? "#4ddbc2" : "#b2bdcf" });
        const priorValue = previous!.values.find(v => v.id === run.id)!;
        const since = value.sinceT;
        const boundary = Math.max(previous!.t, Math.min(point.t, since));
        const f = point.t > previous!.t ? (boundary - previous!.t) / (point.t - previous!.t) : 0;
        const boundaryY = priorValue.wait + (value.wait - priorValue.wait) * f;
        // End solid segment and begin dashed segment exactly at the reported switch.
        paths[paths.length - 1].path += ` L${x(boundary)},${y(boundaryY)}`;
        path = `M${x(boundary)},${y(boundaryY)}`;
      }
      path += `${path ? " L" : "M"}${x(point.t)},${y(value.wait)}`;
      fallback = value.fallback; previous = point;
    }
    if (path) paths.push({ id: `${run.id}:last`, path, fallback, color: index ? "#4ddbc2" : "#b2bdcf" });
  });
  return <section className="chart-card" aria-label="Simulated average wait chart">
    <div className="section-heading"><h2>Average wait over time <span>SIMULATED</span></h2>
      <div className="chart-legend">{runs.map((r, i) => <span key={r.id}><i style={{ background: i ? "#4ddbc2" : "#b2bdcf" }} />{MODE_NAMES[r.mode]}</span>)}<span>┄ Adaptive fallback</span></div></div>
    <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label="Average stopped time in seconds, plotted against shared simulation time">
      {[0, 0.5, 1].map(f => <g key={f}><line x1={left} x2={width - right} y1={y(maxWait * f)} y2={y(maxWait * f)} stroke="#293544" strokeDasharray="3 5" /><text x={left - 8} y={y(maxWait * f) + 4} textAnchor="end">{(maxWait * f).toFixed(0)}</text></g>)}
      {[0, 0.25, 0.5, 0.75, 1].map(f => <text key={f} x={x(start + (end - start) * f)} y={height - 10} textAnchor="middle">{(start + (end - start) * f).toFixed(0)}s</text>)}
      {paths.map(p => <path key={p.id} d={p.path} fill="none" stroke={p.color} strokeWidth="2.5" strokeDasharray={p.fallback ? "5 5" : undefined} />)}
      {!points.length && <text x={width / 2} y={height / 2} textAnchor="middle">Start a simulation to see live results</text>}
    </svg>
  </section>;
}
