# Data sources (honest version)

## Traffic: Google Maps Platform, Routes API (only traffic source)

- **What we get:** for a route, `duration` (traffic-aware) and `staticDuration` (no traffic). Their
  ratio is a **congestion indicator**. Google does **not** give vehicle counts, flows, turning
  ratios or queue lengths.
- **How we use it:** one short route per entry node of the area → `congestion_ratio` → `scale`
  (CONTRACT §7), which multiplies a base per-entry flow. **This mapping is a heuristic we chose**, not a
  calibrated traffic model. Numbers like "AI saves 23% wait" are relative to *our simulated*
  demand, not measured reality. Say so in the demo.
- **No cameras, no Waze, no loop detectors.**
- **Quota / cost:** every resolve makes N calls (N = number of entry nodes). Guarded by
  `GOOGLE_DAILY_CAP`, the 30-min cache, and recorded snapshots for offline demos.
- `scripts/check_keys.py` makes exactly one Routes call to validate the key.

## Road geometry: OpenStreetMap (via Overpass)

- Google has **no road-graph export** (Maps/Roads APIs don't return a routable network you may store),
  so geometry, lanes, speed limits, road classes and existing signals (`highway=traffic_signals`)
  come from OSM. © OpenStreetMap contributors, ODbL. Attribution is shown on the map.
- OSM tags are incomplete: `lanes`/`maxspeed` are often missing. Defaults per `road_class` fill the gaps.
- `scripts/fetch_sample_area.py` (run locally) fetches a bbox with a proper User-Agent and caches it.
  Overpass has a fair-use policy: keep boxes small, cache everything.

## Geocoding: Nominatim (proxied by the backend)

Usage policy: ≤ 1 req/s, an identifying User-Agent, cache results. Fine for a hackathon demo, not for production.

## Basemap: OSM raster tiles

`tile.openstreetmap.org` is OK for development and demos under its tile usage policy. For heavier
use, switch to a hosted tile provider.

## AI: Gemini

Called by the supervisor at most every 6 s per session (wall-clock) and capped by `GEMINI_DAILY_CAP`.
Its output (`Plan`) is advisory: the engine's safety rules and max-pressure always apply.

## Unverified / open questions

- **Google Maps Platform terms vs. non-Google basemaps:** the terms restrict using Google Maps
  content with non-Google maps and caching/storing it. We display only *derived* demand on an OSM
  basemap and cache ratios for 30 min / in snapshots. **We have not verified this is compliant.**
  Check the current Google Maps Platform Terms (and the Routes API service-specific terms) before any public use.
- Whether the ratio of a short route reflects congestion at the junctions inside the box (routes may leave the box).
