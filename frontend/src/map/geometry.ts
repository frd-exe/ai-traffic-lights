import type { BBox, Edge, Vehicle } from "../types/contract.gen";

export const DEMO_BBOX: BBox = { west: 2.157, south: 41.384, east: 2.173, north: 41.396 };
export function bboxSize(bbox: BBox): [number, number] {
  return [(bbox.east - bbox.west) * 111320 * Math.cos((bbox.north + bbox.south) / 2 * Math.PI / 180),
    (bbox.north - bbox.south) * 111320];
}
export function bboxFromPoints(a: [number, number], b: [number, number]): BBox {
  return { west: Math.min(a[0], b[0]), east: Math.max(a[0], b[0]), south: Math.min(a[1], b[1]), north: Math.max(a[1], b[1]) };
}
export function pointOnEdge(edge: Edge, offset: number): [number, number] {
  const lengths = edge.geometry.slice(1).map((b, i) => {
    const a = edge.geometry[i];
    return Math.hypot((b[0] - a[0]) * Math.cos(a[1] * Math.PI / 180), b[1] - a[1]);
  });
  let remaining = Math.max(0, Math.min(1, offset / edge.length_m)) * lengths.reduce((a, b) => a + b, 0);
  for (let i = 0; i < lengths.length; i++) {
    if (remaining <= lengths[i] || i === lengths.length - 1) {
      const a = edge.geometry[i], b = edge.geometry[i + 1];
      const f = lengths[i] > 0 ? remaining / lengths[i] : 0;
      return [a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f];
    }
    remaining -= lengths[i];
  }
  return [edge.geometry[0][0], edge.geometry[0][1]];
}
export function interpolateVehicle(a: Vehicle, b: Vehicle | undefined, f: number, edges: Map<string, Edge>): Vehicle {
  if (!b) return a;
  const oldEdge = edges.get(a.edge_id), newEdge = edges.get(b.edge_id);
  let edgeId = a.edge_id, offset = a.offset_m;
  if (a.edge_id === b.edge_id && b.offset_m >= a.offset_m) offset += (b.offset_m - offset) * f;
  else if (oldEdge && newEdge && oldEdge.to_node === newEdge.from_node) {
    const distance = (oldEdge.length_m - a.offset_m + b.offset_m) * f;
    offset = a.offset_m + distance;
    if (offset >= oldEdge.length_m) { edgeId = b.edge_id; offset -= oldEdge.length_m; }
  }
  // Unknown multi-edge jumps and mock wraparounds stay at the earlier sample,
  // then snap at the new sample, rather than drawing a chord through buildings.
  const edge = edges.get(edgeId);
  const [lon, lat] = edge ? pointOnEdge(edge, offset) : [a.lon, a.lat];
  return { ...a, edge_id: edgeId, offset_m: offset, lon, lat, speed: a.speed + (b.speed - a.speed) * f };
}
