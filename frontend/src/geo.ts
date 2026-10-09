import type { Map as MapLibreMap } from "maplibre-gl";
import type { BBox } from "./types/contract.gen";

const M_PER_DEG_LAT = 111_320;

export function bboxOf(map: MapLibreMap): BBox {
  const b = map.getBounds();
  return { west: b.getWest(), south: b.getSouth(), east: b.getEast(), north: b.getNorth() };
}

/** Point `d` metres from (lat, lon) along `bearingDeg`; returns [lon, lat]. */
export function offsetPoint(lat: number, lon: number, bearingDeg: number, d: number): [number, number] {
  const b = (bearingDeg * Math.PI) / 180;
  const dLat = (d * Math.cos(b)) / M_PER_DEG_LAT;
  const dLon = (d * Math.sin(b)) / (M_PER_DEG_LAT * Math.cos((lat * Math.PI) / 180));
  return [lon + dLon, lat + dLat];
}
