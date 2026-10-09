import type { Edge, Explanation, SimStartRequest, SimTick } from "../types/contract.gen";
import { interpolateVehicle } from "../map/geometry";

export type Mode = SimStartRequest["mode"];
export const MODE_NAMES: Record<Mode, string> = { fixed: "Fixed timers", webster: "Webster", max_pressure: "Max-pressure", ai: "AI control" };
export type Run = { id: string; mode: Mode; connected: boolean };
export type FeedEntry = Explanation & { key: string; mode: Mode };
export type ChartPoint = { t: number; values: { id: string; wait: number; fallback: boolean; sinceT: number }[] };

/** Both views sample this one clock. Never extrapolate past the slower session.
 * Keep bracketing frames for interpolation. Bound unreleased buffers; if the
 * faster session exceeds the limit, fail visibly rather than misaligning time.
 */
export class Playback {
  private buffers = new Map<string, SimTick[]>();
  private edges: Map<string, Edge>;
  private time = 0;
  private wall = 0;
  private stopped = false;
  private events = new Map<string, FeedEntry>();
  private modes: Map<string, Mode>;
  constructor(runs: Run[], edges: Edge[], private speed: number) {
    this.modes = new Map(runs.map(r => [r.id, r.mode]));
    this.edges = new Map(edges.map(e => [e.id, e]));
    for (const run of runs) this.buffers.set(run.id, []);
  }
  ingest(tick: SimTick) {
    const buffer = this.buffers.get(tick.session_id);
    if (!buffer || (buffer.length && buffer[buffer.length - 1].t >= tick.t)) return;
    if (buffer.length >= 4096) throw new Error("Compare buffer filled while one session was stalled. Stop and restart both sessions.");
    buffer.push(tick);
    for (const e of tick.explanations) {
      const key = `${tick.session_id}:${e.t}:${e.intersection_id}:${e.text}`;
      this.events.set(key, { ...e, key, mode: this.modes.get(tick.session_id)! });
    }
    // Keep at most 200 released explanations plus pending ones.
    const released = [...this.events.values()].filter(e => e.t <= this.time).sort((a, b) => b.t - a.t);
    for (const e of released.slice(200)) this.events.delete(e.key);
  }
  freeze() { this.stopped = true; }
  advance(now: number) {
    const buffers = [...this.buffers.values()];
    if (!buffers.length || buffers.some(b => !b.length)) { this.wall = now; return; }
    const earliest = Math.max(...buffers.map(b => b[0].t));
    const latest = Math.min(...buffers.map(b => b[b.length - 1].t));
    if (latest < earliest) { this.wall = now; return; }
    if (!this.stopped) {
      const elapsed = this.wall ? Math.max(0, now - this.wall) / 1000 : 0;
      this.time = Math.min(latest, Math.max(earliest, this.time + elapsed * this.speed));
    }
    this.wall = now;
    for (const buffer of buffers) while (buffer.length > 2 && buffer[1].t <= this.time) buffer.shift();
  }
  get t() { return this.time; }
  get feed() { return [...this.events.values()].filter(e => e.t <= this.time).sort((a, b) => b.t - a.t).slice(0, 60); }
  sample(id: string): SimTick | null {
    const buffer = this.buffers.get(id);
    if (!buffer?.length || buffer[0].t > this.time) return null;
    let lower = buffer[0], upper = lower;
    for (const tick of buffer) {
      if (tick.t <= this.time) lower = tick;
      if (tick.t >= this.time) { upper = tick; break; }
      upper = tick;
    }
    const f = upper.t > lower.t ? (this.time - lower.t) / (upper.t - lower.t) : 0;
    const nextCars = new Map(upper.vehicles.map(v => [v.id, v]));
    const metrics = { ...lower.metrics, t: this.time };
    for (const key of ["avg_wait_s", "trip_delay_s", "avg_queue", "throughput_per_min"] as const)
      metrics[key] += (upper.metrics[key] - lower.metrics[key]) * f;
    return { ...lower, t: this.time, metrics, vehicles: lower.vehicles.map(v => interpolateVehicle(v, nextCars.get(v.id), f, this.edges)) };
  }
}
