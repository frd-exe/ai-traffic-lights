# Data sources (honest version)

## Traffic demand: simulated (no external traffic data)

Since contract 0.2.0 there is **no external traffic source**: no cameras, no loop detectors,
no Waze, no Google traffic. Demand is simulated per entry node from a level
(low 150 / medium 300 / high 500 / rush 700 veh/h per entry), a global multiplier and optional
per-entry overrides (CONTRACT §7). These numbers are plausible orders of magnitude for urban
approaches, **not measurements**. Any result like "AI saves 23% wait" is relative to that
simulated demand, so say so in the demo.

## Road geometry: synthetic demo city, or OpenStreetMap

- **Demo city (default):** `backend/data/grid_network.json`, a hand-generated 3×3 grid with 150 m
  blocks (generator: `scripts/gen_grid_network.py`). The backend uses it whenever there is no OSM sample.
- **OSM (optional):** `scripts/fetch_sample_area.py` (run locally) fetches roads and signals for a bbox
  from Overpass into `backend/data/sample_area.json`; `backend/roadnet/` parses it.
  © OpenStreetMap contributors, ODbL. Attribution is shown on the map.
  OSM tags are incomplete: `lanes`/`maxspeed` are often missing, so defaults per road class fill the gaps.
- **Overpass endpoints:** the default is `overpass-api.de`. The script also tries these mirrors:
  `overpass.kumi.systems`, `overpass.private.coffee`, `maps.mail.ru/osm/tools/overpass`.
  **The mirror URLs are from memory and unverified.** Override them with
  `OVERPASS_ENDPOINTS=url1,url2` in `.env`. The public servers often answer 504 under load; the script
  retries with backoff, splits the box into 2×2 tiles, and caches every success.
  Keep boxes small (≤ ~1 km²) and requests few (fair-use policy).

## Geocoding: Nominatim (proxied by the backend)

Usage policy: ≤ 1 req/s, an identifying User-Agent (set `NOMINATIM_CONTACT` in `.env`), cache
results. The backend enforces all three. Fine for a hackathon demo, not for production.

## Basemap: OSM raster tiles

`tile.openstreetmap.org` is OK for development and demos under its tile usage policy. For heavier
use, switch to a hosted tile provider.

## AI: Gemini

Called by the supervisor at most every 6 s per session (wall-clock) and capped by `GEMINI_DAILY_CAP`.
Its output (`Plan`) is advisory: the engine's safety rules and max-pressure always apply.

## Unverified / open questions

- The Overpass mirror URLs above (from memory).
- Whether the demand levels resemble any real city; they are not calibrated.
