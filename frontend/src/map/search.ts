import type { Map as MapLibreMap } from "maplibre-gl";
import type { GeocodeResult } from "../types/contract.gen";

export function focusResult(map: MapLibreMap | null, result: GeocodeResult) {
  if (result.bbox) map?.fitBounds([[result.bbox.west, result.bbox.south], [result.bbox.east, result.bbox.north]], { padding: 100 });
  else map?.flyTo({ center: [result.lon, result.lat], zoom: 15 });
}
