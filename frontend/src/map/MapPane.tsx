import { useEffect, useRef, useState } from "react";
import * as maplibregl from "maplibre-gl";
import workerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?url";
import type { Feature, FeatureCollection } from "geojson";
import type { AreaResponse, BBox, DemandProfile, SimTick } from "../types/contract.gen";
import { offsetPoint } from "../geo";
import { bboxFromPoints, pointOnEdge } from "./geometry";

maplibregl.setWorkerUrl(workerUrl);
const EMPTY: FeatureCollection = { type: "FeatureCollection", features: [] };
// Synthetic demo city: dark background only, no map tiles (the grid is not a real place, and the
// demo must work offline).
const PLAIN: maplibregl.StyleSpecification = { version: 8, sources: {}, layers: [
  { id: "background", type: "background", paint: { "background-color": "#0c141e" } } ] };
const APP_SOURCES = ["roads", "junctions", "area-box", "vehicles", "signals"];
const SIGNAL_COLORS = { green: "#45e5a9", yellow: "#ffd46a", red: "#ff6476" };

type Props = {
  area: AreaResponse | null; selected: Set<string>; recommended: Set<string>;
  profile: DemandProfile | null; bbox: BBox | null; drawing?: boolean;
  onBox?: (bbox: BBox) => void; onToggle: (id: string) => void;
  onReady?: (map: maplibregl.Map) => void; getTick: () => SimTick | null;
};

function setData(map: maplibregl.Map, id: string, data: FeatureCollection) {
  (map.getSource(id) as maplibregl.GeoJSONSource | undefined)?.setData(data);
}

function addLayers(map: maplibregl.Map) {
  for (const id of APP_SOURCES) if (!map.getSource(id)) map.addSource(id, { type: "geojson", data: EMPTY });
  if (map.getLayer("roads")) return;
  map.addLayer({ id: "area-fill", type: "fill", source: "area-box", paint: { "fill-color": "#4ddbc2", "fill-opacity": 0.06 } });
  map.addLayer({ id: "area-line", type: "line", source: "area-box", paint: { "line-color": "#4ddbc2", "line-width": 2, "line-dasharray": [3, 2] } });
  map.addLayer({ id: "roads-glow", type: "line", source: "roads", paint: { "line-color": "#3685bc", "line-width": 10, "line-opacity": 0.08 } });
  map.addLayer({ id: "roads", type: "line", source: "roads", paint: {
    "line-color": ["case", [">", ["get", "ratio"], 0], ["interpolate", ["linear"], ["get", "ratio"], 1, "#45e5a9", 1.5, "#e2ba6a", 2, "#ff6476"], "#56758e"],
    "line-width": ["case", ["get", "major"], 4, 2.5], "line-opacity": 0.85 } });
  map.addLayer({ id: "junctions", type: "circle", source: "junctions", paint: {
    "circle-radius": ["case", ["get", "selected"], 8, 5],
    "circle-color": ["case", ["get", "selected"], "#4ddbc2", "#233343"],
    "circle-stroke-color": ["case", ["get", "recommended"], "#f7d988", "#7c93a6"], "circle-stroke-width": 2 } });
  map.addLayer({ id: "vehicles", type: "circle", source: "vehicles", paint: { "circle-radius": 3.8,
    "circle-color": ["interpolate", ["linear"], ["get", "speed"], 0, "#ff6476", 4, "#ffc66b", 10, "#67dcf1"],
    "circle-stroke-color": "#09131c", "circle-stroke-width": 1 } });
  map.addLayer({ id: "signals", type: "circle", source: "signals", paint: { "circle-radius": 4.5,
    "circle-color": ["get", "color"], "circle-stroke-color": "#07101a", "circle-stroke-width": 2 } });
}

function boxData(bbox: BBox | null): FeatureCollection {
  return { type: "FeatureCollection", features: bbox ? [{ type: "Feature", properties: {}, geometry: {
    type: "Polygon", coordinates: [[[bbox.west, bbox.south], [bbox.east, bbox.south], [bbox.east, bbox.north], [bbox.west, bbox.north], [bbox.west, bbox.south]]] } }] : [] };
}

function networkData(map: maplibregl.Map, props: Props) {
  const ratios = new Map(props.profile?.entries.map(e => [e.entry_node_id, e.congestion_ratio ?? 0]) ?? []);
  setData(map, "roads", { type: "FeatureCollection", features: props.area?.network.edges.map(e => ({
    type: "Feature", properties: { ratio: ratios.get(e.from_node) ?? 0, major: ["primary", "secondary", "trunk", "motorway"].includes(e.road_class) },
    geometry: { type: "LineString", coordinates: e.geometry } })) ?? [] });
  setData(map, "junctions", { type: "FeatureCollection", features: props.area?.intersections.map(i => ({
    type: "Feature", properties: { id: i.id, selected: props.selected.has(i.id), recommended: props.recommended.has(i.id) },
    geometry: { type: "Point", coordinates: [i.lon, i.lat] } })) ?? [] });
  setData(map, "area-box", boxData(props.bbox));
}

export default function MapPane(props: Props) {
  const element = useRef<HTMLDivElement>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);
  const liveProps = useRef(props);
  const [mapError, setMapError] = useState<string | null>(null);
  const [revision, setRevision] = useState(0);
  useEffect(() => { liveProps.current = props; });

  useEffect(() => {
    let map: maplibregl.Map;
    try { map = new maplibregl.Map({ container: element.current!, style: PLAIN, center: [2.165, 41.39], zoom: 15,
      attributionControl: false, maxZoom: 18 }); }
    catch {
      const timer = window.setTimeout(() => setMapError("WebGL is unavailable. Use the intersection list and live dashboard to run the simulation."), 0);
      return () => window.clearTimeout(timer);
    }
    mapRef.current = map;
    liveProps.current.onReady?.(map);
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "bottom-right");
    map.addControl(new maplibregl.FullscreenControl(), "bottom-right");
    map.on("style.load", () => { addLayers(map); networkData(map, liveProps.current); setRevision(n => n + 1); });

    const popup = new maplibregl.Popup({ closeButton: true, offset: 12 });
    map.on("click", "junctions", event => {
      if (liveProps.current.drawing) return;
      const id = event.features?.[0]?.properties?.id as string | undefined;
      const intersection = liveProps.current.area?.intersections.find(i => i.id === id);
      if (!intersection) return;
      const content = document.createElement("div");
      const title = document.createElement("strong"); title.textContent = `Junction ${liveProps.current.area!.intersections.indexOf(intersection) + 1}`;
      const detail = document.createElement("p");
      detail.textContent = `Structural score ${intersection.structural_score.toFixed(2)} · ${intersection.sim_gain_s == null ? "Simulated gain not available" : `Simulated gain ${intersection.sim_gain_s.toFixed(1)} s`}`;
      const button = document.createElement("button"); button.textContent = liveProps.current.selected.has(intersection.id) ? "Remove signal" : "Add signal";
      button.onclick = () => { liveProps.current.onToggle(intersection.id); popup.remove(); };
      content.append(title, detail, button); popup.setLngLat([intersection.lon, intersection.lat]).setDOMContent(content).addTo(map);
    });
    // Pointer drag on the canvas avoids dependence on tile loading / style state.
    const canvas = map.getCanvas();
    let start: [number, number] | null = null;
    const point = (event: PointerEvent): [number, number] => {
      const rect = canvas.getBoundingClientRect(), ll = map.unproject([event.clientX - rect.left, event.clientY - rect.top]);
      return [ll.lng, ll.lat];
    };
    const down = (event: PointerEvent) => {
      if (!liveProps.current.drawing || event.button !== 0) return;
      event.preventDefault(); start = point(event); canvas.setPointerCapture(event.pointerId);
    };
    const move = (event: PointerEvent) => { if (start) setData(map, "area-box", boxData(bboxFromPoints(start, point(event)))); };
    const up = (event: PointerEvent) => {
      if (!start) return;
      const box = bboxFromPoints(start, point(event)); start = null;
      if (canvas.hasPointerCapture(event.pointerId)) canvas.releasePointerCapture(event.pointerId);
      if (box.east > box.west && box.north > box.south) liveProps.current.onBox?.(box);
    };
    const cancel = () => { start = null; setData(map, "area-box", boxData(liveProps.current.bbox)); };
    canvas.addEventListener("pointerdown", down); canvas.addEventListener("pointermove", move);
    canvas.addEventListener("pointerup", up); canvas.addEventListener("pointercancel", cancel);
    let raf = 0;
    const animate = () => {
      const tick = liveProps.current.getTick();
      if (map.getSource("vehicles")) {
        const edges = new Map(liveProps.current.area?.network.edges.map(e => [e.id, e]) ?? []);
        setData(map, "vehicles", { type: "FeatureCollection", features: tick?.vehicles.map(v => ({
          type: "Feature", properties: { speed: v.speed }, geometry: { type: "Point", coordinates: edges.has(v.edge_id) ? pointOnEdge(edges.get(v.edge_id)!, v.offset_m) : [v.lon, v.lat] } })) ?? [] });
        const intersections = new Map(liveProps.current.area?.intersections.map(i => [i.id, i]) ?? []);
        const signals: Feature[] = [];
        for (const state of tick?.signals ?? []) {
          const ix = intersections.get(state.intersection_id);
          for (const a of ix?.approaches ?? []) signals.push({ type: "Feature", properties: { color: SIGNAL_COLORS[state.color_per_approach[a.id] ?? "red"] },
            geometry: { type: "Point", coordinates: offsetPoint(ix!.lat, ix!.lon, a.bearing, 14) } });
        }
        setData(map, "signals", { type: "FeatureCollection", features: signals });
      }
      raf = requestAnimationFrame(animate);
    };
    raf = requestAnimationFrame(animate);
    const resize = new ResizeObserver(() => map.resize()); resize.observe(element.current!);
    return () => {
      cancelAnimationFrame(raf); resize.disconnect(); popup.remove();
      canvas.removeEventListener("pointerdown", down); canvas.removeEventListener("pointermove", move);
      canvas.removeEventListener("pointerup", up); canvas.removeEventListener("pointercancel", cancel);
      map.remove(); mapRef.current = null;
    };
  }, []);

  useEffect(() => { if (mapRef.current) networkData(mapRef.current, props); }, [props, revision]);
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    if (props.drawing) map.dragPan.disable(); else map.dragPan.enable();
    map.getCanvas().style.cursor = props.drawing ? "crosshair" : "";
  }, [props.drawing]);
  useEffect(() => {
    const bbox = props.area?.network.bbox;
    if (bbox && mapRef.current) mapRef.current.fitBounds([[bbox.west, bbox.south], [bbox.east, bbox.north]], { padding: 70, duration: 600 });
  }, [props.area]);
  return <div className="map-wrap"><div className="map" ref={element} aria-label="Traffic simulation map" />
    <span className="basemap-label">Synthetic demo city</span>
    {props.drawing && <div className="draw-hint">Drag a rectangle on the map · Esc to cancel</div>}
    {mapError && <div className="map-error" role="status">{mapError}</div>}
  </div>;
}
