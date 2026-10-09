# Headless simulation

`SimEngine(network, intersections, seed, demand_profile, signalized_ids)` imports
the contract models and implements its protocol. Call `step(dt, commands)`;
large dt values are subdivided into <=0.1 s integration steps. Ask controllers
once per simulation second. `observe()` covers selected signalized junctions;
metrics include approaches at every supplied junction.

IDM uses acceleration 1.4 m/s², comfortable braking 2 m/s², emergency braking
8 m/s², exponent 4, headway uniformly 1.1–1.8 s, 4.5 m cars and 2 m minimum gap.
Each directed edge has one queue per declared lane; there is no lane changing.
Hard gap and stop-line clamps supplement numerical IDM to prevent overlaps.
Desired speed is the speed limit times min(1, scheduled speed factor).
Vehicle positions follow the edge polyline by distance fraction.

Demand levels are 120/200/280/380 veh/hour per entry for low/medium/high/rush
(contract 0.3.0). Every profile's entry flow is scale x 200 (the medium flow);
scale already folds in level x multiplier x entry override. Missing entries
use level x multiplier.
Interarrival times are exponential. Destinations are uniform over reachable
exits other than the origin. Routes minimize length/speed_limit with stable
ties. Independent per-entry RNG streams are seeded from seed and profile ID;
driver headways use another stream. Schedule generation occurs before spawn
blocking and never reads traffic state. Compare schedule digests at equal t.
The profile is deep copied; mutation by the caller cannot affect a run.
`set_demand`/`apply_profile` schedule a change at `at_t` >= t: arrivals up to at_t use
the old rates, then each entry's pending arrival is redrawn from at_t with the new
rate from the same stream, so compare sessions given the same change stay identical.

Spawn deferral is counted once per scheduled vehicle, with unlimited external
FIFO queues per first edge. External waiting is included in stopped time and
elapsed trip time. Free-flow time accumulates distance/speed_limit, including
partial edges. Finished trips remain for 120 s; throughput counts genuine exits
within 60 s, excluding teleports. Trailing windows are (t-window, t].

Phases pair geometrically opposite approaches within 20 degrees, giving NS/EW
on orthogonal four-arm junctions. Three arms yield an opposing pair and singleton;
five arms yield two opposing pairs and singleton when geometry permits. Crossings
reserve their MOVEMENT (in-edge -> out-edge) for 2 s and block conflicting
movements only: crossing paths, permissive left turns vs. opposing traffic and
merges into the same exit. Same-approach followers and opposing non-left
movements cross together (the earlier whole-junction lock capped every junction
at ~1800 veh/h and saturated the grid at low demand). No
crossing begins during yellow or all-red. A reservation acquired before yellow
finishes within the 3 s clearance. Downstream space must exist before admission.

Signal requests survive min green and transitions; changing a request during
clearance queues it for the following green. Starvation requests reserve enough
minimum-green and clearance time for all waiting phases so service arrives
by 60 s (55 s trigger for two phases). With more than five phases the timing
constraints can be infeasible; safety/min green wins. Fixed attachment asks
for the next phase, ensuring a controller swap uses normal clearance.

Unsignalized cars yield to higher road classes and to the right for equal
classes. A minor car accepts a gap if all priority heads are >=3+its headway
seconds away. Every crossing still checks movement reservations. Standoff breaker
(all-way-stop rule): if a head car and every car it yields to are all stopped at their
stop lines (e.g. four equal-class approaches under the right-hand rule, where everyone
yields to someone), the head that has waited longest and can move goes, and keeps the
right of way until it has crossed. Without it such junctions locked up permanently
(the wait-for cycle detector only follows queues and spillback, not yielding).
`standoffs_resolved` counts these events. Entire-network
immobility for 60 s increments the contract counter and teleports the oldest;
local wait-for cycles of cars stopped >120 s also increment it and teleport
the oldest cycle member. Teleports are retained as ended trips in the metric
population but excluded from throughput.

Webster uses expected approach flows propagated along demand routes, 900
veh/hour/lane saturation (calibrated to this engine; 1800 gave too-short cycles), 5 s lost time per phase, critical ratios equal to the
maximum approach flow/lane saturation in each phase, C=(1.5L+5)/(1-Y).
It bounds C to [12n,90] s and gives every phase at least 7 s green.

Run `python -m pytest backend/tests/test_sim.py` and
`python -m scripts.benchmark_sim --seconds 60 --vehicles 500 --maintain`.
Install `backend/requirements.txt` and `backend/sim/requirements.txt` first.
