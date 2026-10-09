import { expect, test } from "@playwright/test";
import { Playback } from "../src/api/playback";
import { interpolateVehicle, pointOnEdge } from "../src/map/geometry";
import { controllerBanner, dailyCapFallback } from "../src/status";
import type { ControllerStatus, Edge, SimTick, Vehicle } from "../src/types/contract.gen";

const edge: Edge = { id: "curve", from_node: "a", to_node: "b", lanes: 1, length_m: 200, speed_limit: 10,
  road_class: "residential", geometry: [[0, 0], [0, 0.001], [0.001, 0.001]] };
const active: ControllerStatus = { state: "ai_active", effective_controller: "gemini+max_pressure", message: "AI active",
  since_t: 0, calls_last_min: 1, calls_today: 1, daily_cap: 500 };
const vehicle: Vehicle = { id: "v", edge_id: edge.id, offset_m: 0, lat: 0, lon: 0, speed: 5, heading: 0 };
function tick(id: string, t: number, fallback = false): SimTick {
  return { type: "tick", session_id: id, t, vehicles: [{ ...vehicle, offset_m: t * 10 }], signals: [],
    metrics: { mode: "ai", effective_controller: fallback ? "fixed" : "gemini+max_pressure", trip_delay_s: t, avg_wait_s: t,
      avg_queue: 0, throughput_per_min: 0, blocked_spawns: 0, deadlocks: 0, t },
    controller_status: fallback ? { ...active, state: "ai_limit_reached", effective_controller: "fixed", since_t: 10,
      calls_today: 500 } : active,
    data_status: { state: "google_live", message: "Live", calls_today: 1, daily_cap: 200, last_update: null },
    explanations: [{ t, intersection_id: null, text: `At ${t}` }] };
}

test("buffers the faster session and interpolates both metrics at exactly shared t", () => {
  const playback = new Playback([{ id: "a", mode: "fixed", connected: true }, { id: "b", mode: "ai", connected: true }], [edge], 1);
  playback.ingest(tick("a", 0)); playback.ingest(tick("a", 20, true)); playback.ingest(tick("b", 0)); playback.ingest(tick("b", 8));
  playback.advance(1000); playback.advance(5000);
  expect(playback.t).toBe(4);
  expect(playback.sample("a")!.t).toBe(playback.sample("b")!.t);
  expect(playback.sample("a")!.metrics.avg_wait_s).toBe(4);
  expect(playback.sample("b")!.metrics.avg_wait_s).toBe(4);
  expect(playback.sample("a")!.controller_status.state).toBe("ai_active");
  playback.advance(20000);
  expect(playback.t).toBe(8);
  expect(playback.feed.every(e => e.t <= 8)).toBeTruthy();
  playback.ingest(tick("b", 20, true)); playback.advance(32000);
  expect(playback.sample("a")!.controller_status.state).toBe("ai_limit_reached");
  playback.freeze(); playback.advance(40000); expect(playback.t).toBe(20);
});

test("follows bent geometry and handles a connected edge transition without cutting corners", () => {
  expect(pointOnEdge(edge, 100)[0]).toBeCloseTo(0, 6);
  expect(pointOnEdge(edge, 100)[1]).toBeCloseTo(0.001, 6);
  const next = { ...edge, id: "next", from_node: "b", to_node: "c" };
  const car = interpolateVehicle({ ...vehicle, offset_m: 190 }, { ...vehicle, edge_id: "next", offset_m: 10 }, 0.75, new Map([[edge.id, edge], [next.id, next]]));
  expect(car.edge_id).toBe("next"); expect(car.offset_m).toBe(5);
});

test("fallback wording and daily cap reset eligibility are driven by status", () => {
  const limit = { ...active, state: "ai_limit_reached", effective_controller: "max_pressure", since_t: 20, calls_today: 500 } satisfies ControllerStatus;
  expect(controllerBanner(limit)?.text).toBe("Live AI quota reached. Using adaptive fallback.");
  expect(dailyCapFallback(limit)).toBeTruthy();
  expect(dailyCapFallback({ ...limit, calls_today: 12 })).toBeFalsy();
  expect(dailyCapFallback({ ...limit, state: "ai_unavailable" })).toBeFalsy();
  expect(controllerBanner(active)).toBeNull();
});
