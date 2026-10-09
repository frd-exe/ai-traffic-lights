# Signal siting: structural pre-filter

**This score is a pre-filter, not an optimal placement.** It ranks junctions by structural
features of the road graph so the UI has sensible defaults and the later simulation-based step
has fewer candidates to evaluate. It knows nothing about actual traffic, turning movements,
pedestrians or safety. Simulation-based selection (`Intersection.sim_gain_s`, Step 5) may reorder it.

Code: `backend/siting/prefilter.py`. Deterministic; ties are broken by intersection id.

## Candidates

Every network node with ≥ 3 distinct neighbour nodes that is not a boundary node
(`backend/roadnet/intersections.py`). Nodes within 20 m are consolidated first (dual carriageways,
complex junctions). **Roundabouts** (`junction=roundabout|circular` ways, `highway=mini_roundabout`
nodes) are never candidates.

## Score

All features are normalised to [0, 1]:

| feature | definition | weight |
|---|---|---|
| degree | `min(approaches, 4) / 4` | 0.25 |
| road class | mean class rank of the two biggest approaches / motorway rank | 0.30 |
| betweenness | betweenness centrality on travel time (length / speed limit), counting only shortest paths between entry and exit nodes, divided by the maximum over candidates | 0.35 |
| OSM signal | 1 if an OSM `highway=traffic_signals` node is within 30 m (0 when `ignore_osm_signals`) | 0.10 |
| spacing penalty | `max over junctions ranked above: max(0, 1 − d / 200 m)` | −0.15 |

`structural_score = clamp(base − 0.15 × spacing_penalty, 0, 1)`.

The spacing penalty is applied greedily: the best junction is picked first, then each remaining
junction is re-scored against the ones already picked, and so on. That spreads the top of the list
over the area instead of clustering it on one corridor. Scores are non-increasing down the list.

`recommended_ids` = the top 3 with a score ≥ 0.2 (the UI's default selection).

## On the demo city (3×3 grid)

All 9 junctions are 4-way, so degree doesn't separate them. The primary street (row 2) wins on road
class; betweenness separates the rest. The centre junction (n22) is 150 m from both other primary
junctions, so the spacing penalty puts it third.

## Simulation-based refinement (`backend/roadnet/sim_siting.py`)

1. Candidates: the top ≤ 8 structurally ranked junctions.
2. Baseline run with every candidate **unsignalised** (priority + gap acceptance).
3. Greedy: each round simulates "current set + one more candidate" for every remaining candidate in
   parallel (one process per run; signals run max-pressure) and adds the one that lowers `avg_wait_s` most.
4. Stop at diminishing returns: best improvement < max(1 s, 3 % of the current wait).
5. `sim_gain_s` = the candidate's marginal reduction of `avg_wait_s` when it was added (for candidates
   never added: their marginal value in the last round, possibly ≤ 0).
6. `recommended_ids` = the **simulation top 3** (`siting_top`): the greedily added signals first, then the
   remaining candidates by `sim_gain_s`. The UI preselects these 3 and the experiments signalise the same 3.

Each run is 1 min warm-up + 3 simulated minutes, same seed and demand (rush by default) for every run, so
differences come from the signals, not from demand noise. Results are cached per (area_id, seed, level);
the demo city is precomputed and committed (`backend/data/siting/area_grid_mock_seed42_rush.json`,
`python scripts/precompute_siting.py`). Other areas get the structural ranking immediately and can
start `POST /api/area/{area_id}/refine` (async).

**Demo city result (seed 42, rush, 12 cores, 13.6 s):** baseline (all unsignalised) avg wait 32.1 s; adding
`i_1c42c10c` (east junction of the primary street) lowers it to 28.1 s (−4.0 s); the next best,
`i_27451942` (centre), would gain only 0.8 s, below the 3 % bar, so the greedy stops at one signal. Most other
single signals make it slightly worse at this demand (the 5 s clearance per switch costs more than priority
rules lose). Top 3 preselected: `i_1c42c10c`, `i_27451942`, `i_ac9b6377`. Single seed, 3-minute windows:
treat as indicative. (An earlier run gave 73 s baseline because of an engine bug: unsignalised four-way
standoffs under the right-hand rule never resolved; fixed, see backend/sim/README.md.)

## Known limitations

- Betweenness assumes every entry→exit pair is equally likely. It is not real demand.
- Road class is a proxy for volume; OSM classes vary by country.
- The weights are hand-picked, not fitted.
- OSM signal tags may be missing or placed on the wrong node; 30 m snapping is a heuristic.
