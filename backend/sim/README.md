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

Demand levels are 150/300/500/700 veh/hour per entry for low/medium/high/rush.
Baseline uses its level (its scales are already encoded by that level). Other
profiles use 300 times each entry scale, defaulting missing scales to 1.
Interarrival times are exponential. Destinations are uniform over reachable
exits other than the origin. Routes minimize length/speed_limit with stable
ties. Independent per-entry RNG streams are seeded from seed and profile ID;
driver headways use another stream. Schedule generation occurs before spawn
blocking and never reads traffic state. Compare schedule digests at equal t.
The profile is deep copied; mutation by the caller cannot affect a run.
`set_demand` is restricted to pre-start testing to preserve compare integrity.

Spawn deferral is counted once per scheduled vehicle, with unlimited external
FIFO queues per first edge. External waiting is included in stopped time and
elapsed trip time. Free-flow time accumulates distance/speed_limit, including
partial edges. Finished trips remain for 120 s; throughput counts genuine exits
within 60 s, excluding teleports. Trailing windows are (t-window, t].

Phases pair geometrically opposite approaches within 20 degrees, giving NS/EW
on orthogonal four-arm junctions. Three arms yield an opposing pair and singleton;
five arms yield two opposing pairs and singleton when geometry permits. All
turn movements require an exclusive 2 s junction reservation, including those
from paired green approaches. This conservative conflict arbitration sacrifices
opposing-through concurrency but avoids unprotected crossing left turns. No
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
seconds away. Every crossing still requires a reservation. Entire-network
immobility for 60 s increments the contract counter and teleports the oldest;
local wait-for cycles of cars stopped >120 s also increment it and teleport
the oldest cycle member. Teleports are retained as ended trips in the metric
population but excluded from throughput.

Webster uses expected approach flows propagated along demand routes, 1800
veh/hour/lane saturation, 5 s lost time per phase, critical ratios equal to the
maximum approach flow/lane saturation in each phase, C=(1.5L+5)/(1-Y).
It bounds C to [12n,120] s and gives every phase at least 7 s green. The exclusive
junction model is more conservative than Webster's saturation assumption.

Run `python -m pytest backend/tests/test_sim.py` and
`python -m scripts.benchmark_sim --seconds 60 --vehicles 500 --maintain`.
Install `backend/requirements.txt` and `backend/sim/requirements.txt` first.
