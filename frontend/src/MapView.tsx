import { useEffect, useRef, useState } from "react";
import * as maplibregl from "maplibre-gl";
// maplibre-gl v6 resolves its worker next to its own module, which breaks once bundled; ship it as an asset.
import workerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?url";
import type { GeoJSONSource, LngLatBoundsLike, StyleSpecification } from "maplibre-gl";
import type { Feature, FeatureCollection } from "geojson";
import { offsetPoint } from "./geo";
import type { AreaResponse, SignalState, Vehicle } from "./types/contract.gen";

const STYLE: StyleSpecification = {
  version: 8,
  sources: {
    osm: {
      type: "raster",
      tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"],
      tileSize: 256,
      attribution: "© OpenStreetMap contributors",
    },
  },
  layers: [{ id: "osm", type: "raster", source: "osm" }],
};

maplibregl.setWorkerUrl(workerUrl);

const SIGNAL_OFFSET_M = 18;
const COLORS = { green: "#1db954", yellow: "#f5c518", red: "#e5383b" } as const;
const empty: FeatureCollection = { type: "FeatureCollection", features: [] };

type Props = {
  area: AreaResponse | null;
  selected: Set<string>;
  vehicles: Vehicle[];
  signals: SignalState[];
  onToggle: (intersectionId: string) => void;
  mapRef: React.RefObject<maplibregl.Map | null>;
};

function setData(map: maplibregl.Map, id: string, data: FeatureCollection) {
  (map.getSource(id) as GeoJSONSource | undefined)?.setData(data);
}

export default function MapView({ area, selected, vehicles, signals, onToggle, mapRef }: Props) {
  const container = useRef<HTMLDivElement>(null);
  const [ready, setReady] = useState(false); // sources/layers added
  const onToggleRef = useRef(onToggle);
  useEffect(() => {
    onToggleRef.current = onToggle;
  }, [onToggle]);

  useEffect(() => {
    const map = new maplibregl.Map({ container: container.current!, style: STYLE, center: [2.165, 41.39], zoom: 15 });
    mapRef.current = map;
    map.on("load", () => {
      for (const id of ["roads", "junctions", "vehicles", "signals"]) map.addSource(id, { type: "geojson", data: empty });
      map.addLayer({
        id: "roads", type: "line", source: "roads",
        paint: { "line-color": ["case", ["get", "major"], "#3a6ea5", "#7a8a99"], "line-width": ["case", ["get", "major"], 5, 3], "line-opacity": 0.7 },
      });
      map.addLayer({
        id: "junctions", type: "circle", source: "junctions",
        paint: {
          "circle-radius": 9,
          "circle-color": ["case", ["get", "selected"], "#ff8c00", "#ffffff"],
          "circle-stroke-color": "#333", "circle-stroke-width": 1.5, "circle-opacity": 0.85,
        },
      });
      map.addLayer({ id: "vehicles", type: "circle", source: "vehicles", paint: { "circle-radius": 4, "circle-color": "#222" } });
      map.addLayer({
        id: "signals", type: "circle", source: "signals",
        paint: { "circle-radius": 5, "circle-color": ["get", "color"], "circle-stroke-color": "#000", "circle-stroke-width": 1 },
      });
      map.on("click", "junctions", (e: maplibregl.MapLayerMouseEvent) => {
        const id = e.features?.[0]?.properties?.id as string | undefined;
        if (id) onToggleRef.current(id);
      });
      map.on("mouseenter", "junctions", () => (map.getCanvas().style.cursor = "pointer"));
      map.on("mouseleave", "junctions", () => (map.getCanvas().style.cursor = ""));
      setReady(true);
    });
    return () => {
      setReady(false);
      map.remove();
      mapRef.current = null;
    };
  }, [mapRef]);

  // Network + junctions
  useEffect(() => {
    const map = mapRef.current;
    if (!ready || !map || !area) return;
    setData(map, "roads", {
      type: "FeatureCollection",
      features: area.network.edges.map((e) => ({
        type: "Feature",
        properties: { id: e.id, major: ["motorway", "trunk", "primary", "secondary"].includes(e.road_class) },
        geometry: { type: "LineString", coordinates: e.geometry },
      })),
    });
    setData(map, "junctions", {
      type: "FeatureCollection",
      features: area.intersections.map((i) => ({
        type: "Feature",
        properties: { id: i.id, selected: selected.has(i.id) },
        geometry: { type: "Point", coordinates: [i.lon, i.lat] },
      })),
    });
  }, [ready, area, selected, mapRef]);

  // Fly to the analysed network once
  useEffect(() => {
    const bb = area?.network.bbox;
    if (mapRef.current && bb) {
      const bounds: LngLatBoundsLike = [[bb.west, bb.south], [bb.east, bb.north]];
      mapRef.current.fitBounds(bounds, { padding: 40, duration: 600 });
    }
  }, [area, mapRef]);

  // Live layers
  useEffect(() => {
    const map = mapRef.current;
    if (!ready || !map) return;
    setData(map, "vehicles", {
      type: "FeatureCollection",
      features: vehicles.map((v) => ({ type: "Feature", properties: { id: v.id }, geometry: { type: "Point", coordinates: [v.lon, v.lat] } })),
    });
    const byId = new Map(area?.intersections.map((i) => [i.id, i]) ?? []);
    const features: Feature[] = [];
    for (const s of signals) {
      const ix = byId.get(s.intersection_id);
      if (!ix) continue;
      for (const a of ix.approaches) {
        const color = s.color_per_approach[a.id] ?? "red";
        features.push({
          type: "Feature",
          properties: { color: COLORS[color] },
          geometry: { type: "Point", coordinates: offsetPoint(ix.lat, ix.lon, a.bearing, SIGNAL_OFFSET_M) },
        });
      }
    }
    setData(map, "signals", { type: "FeatureCollection", features });
  }, [ready, vehicles, signals, area, mapRef]);

  return <div ref={container} className="map" />;
}
