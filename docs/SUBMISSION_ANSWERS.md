# SIH Submission Answers — Team Subterra Mesh (185972)

Problem statement: **SIH26251 — Predictive Logistics and Forward Supply Chain for the Indian Army in the Ladakh Sector**

All copy-paste answers below are within the portal limits. Character counts exclude the headings and the fenced-block markers; they include every visible character inside each block, including spaces and line breaks. Recheck counts if you edit the text.

---

## Idea Title (limit 100 characters)

```text
DRONA-LOG: Predictive Logistics and Terrain-Aware Resupply for High-Altitude Formations
```

---

## Abstract / Summary (limit 10,000 characters)

```text
DRONA-LOG is a working software prototype for predictive, terrain-aware logistics in high-altitude military formations (SIH26251). Forward posts depend on a small number of mountain passes; a closure invalidates convoy plans, and legacy reorder rules discover shortages only after they occur. DRONA-LOG closes the loop from demand signal to dispatch plan and exception handling.

The system forecasts demand with one LightGBM model per supply class (I rations, III POL, V ammunition) over a 7-day horizon, using temperature, snowfall, troop strength and day-of-week. It routes supplies over a 49-node, 53-edge synthetic Ladakh network with an elevation-penalised A* cost of distance + 6 x gradient percent, so a climb costs more than its map distance. It then allocates supply with an OR-Tools/SCIP mixed-integer program that respects integer trucks, 10 units per truck, per-depot fleet limits, a one-day delivery lead, FOB and depot stock balance, safety buffers, war reserves, corridor inflow caps and different west/south procurement costs.

When an operator closes Zoji La, the pass is removed from the graph, every depot-to-FOB route is recomputed, the MILP is re-solved, and the old and new plans are diffed. The UI shows both measured timings, the convoy diff and the resulting inventory projection from the same API payload. In a recorded run the reroute took 28.5 ms, the re-solve 688.5 ms, and the plan changed by 6 cancelled, 5 added and 4 modified deliveries.

The edge client keeps requisitions working when the backend is unreachable. Entries are stored in IndexedDB as an append-only SHA-256 hash chain. On reconnect the server recomputes every hash, verifies the sequence and previous link, accepts true duplicates idempotently, and advances stock only for accepted receipt entries. Hand-edited entries, altered replays, sequence gaps and broken links are rejected.

In a 30-day seeded simulation with identical demand, weather, troop surge and a 7-day Zoji La closure, stockouts fell from 4.15 to 0.25 units (93.9%), below-buffer unit-days from 983.9 to 493.3 (49.9%), and cost per unit of demand from Rs 30,242 to Rs 25,619 (15.3%). Truck-kilometres per unit rose 22.1% because DRONA keeps delivering through the longer southern corridor during the closure. That is reported as an explicit trade-off, not hidden as a saving. The prototype includes 24 backend and 6 frontend tests, a one-command Docker Compose deployment, and a two-and-a-half-minute recorded demonstration.

All operational data is synthetic and every operational surface is labelled SIMULATED. DRONA-LOG is a decision-support prototype for human planners, not an autonomous or live operational system.
```

---

## Idea Description (limit 50,000 characters)

```text
DRONA-LOG: Predictive Logistics and Terrain-Aware Resupply for High-Altitude Formations

PROBLEM

Forward posts in high-altitude sectors are supplied through a small number of corridors. A single pass closure forces long detours, reduces daily inflow and invalidates convoy plans that were built for the open corridor. The legacy process is typically a spreadsheet-and-experience cycle: trailing-average demand, nearest-depot resupply, fixed lead times and manual convoy orders. It reacts late, hoards stock at depots, and parks requisitions when a corridor or the network link fails. Planners also lack a fast, defensible way to compare a new plan with the plan it replaced.

The problem is not only forecasting demand. It is deciding what to move, from which depot, by which route, in which trucks, on which day, under a changed road network, while keeping safety buffers and reporting the cost of the decision. The forward edge adds a second problem: requisitions must still be captured and later trusted when the link to the rear is unavailable.

PROPOSED SOLUTION

DRONA-LOG is a working end-to-end prototype that closes the loop from forecast to dispatch plan to exception handling. It combines four decision engines with an offline-first edge ledger and an operator interface.

1. Demand forecast
One LightGBM regressor is trained per supply class (I rations, III POL, V ammunition) on 365 days of synthetic daily history: 10,950 FOB-class-day records. Features are daily mean temperature, snowfall, troop strength and day-of-week. The model produces a 7-day daily demand vector for every FOB and class, which becomes the demand input to the allocator. Models train locally at first boot in about 30 seconds and are persisted; no external inference service is used.

2. Terrain-aware routing
The synthetic theatre has 49 nodes and 53 road edges: 2 gateways, 22 transit nodes, 12 named passes, 2 staging depots, 1 base depot and 10 FOBs. Each edge carries a distance, simulated gradient and corridor. Routing is A* over an elevation-penalised cost:
cost-km = distance-km + 6.0 x gradient-percent.
A climb therefore costs more than its map distance, and the API reports the gradient penalty separately. A pass closure deletes the pass node and all incident edges. The router then returns None for the severed corridor and a valid alternate for the other, and every depot-to-FOB pair is recomputed before the planner runs.

3. Allocation
The allocator is an OR-Tools mixed-integer linear program solved with SCIP. It chooses integer trucks and continuous loads for each depot, destination, class and day over a 7-day horizon. Constraints include:
- 10 units per truck and a shared truck across classes on a route;
- per-depot daily fleet limits of 24, 8 and 8 trucks for Leh, Karu and Upshi;
- a one-day delivery lead from dispatch to arrival;
- FOB stock balance, demand satisfaction and a soft stockout variable;
- 7-day safety buffers at FOBs and a war reserve at depots, both soft and penalised;
- depot stock balance with gateway inflow only at Leh;
- separate west and south corridor inflow caps per class per day;
- only reachable depot-destination pairs.
The objective combines transport (Rs 42 per unit-km plus Rs 4,000 per truck), procurement (Rs 18,000 per unit west, Rs 24,000 per unit south), stockout penalty (Rs 500,000 per unit), buffer-deficit penalty (Rs 60,000 per unit-day) and a small day-escalation term. SCIP runs with one thread, a 10-second limit and a 1% gap. Wall-clock solve time is measured and returned with every plan.

4. Closure response
The operator can close or reopen a named pass from the command view. The backend persists the closure, rebuilds the graph, remaps every route, re-solves the MILP, computes a delivery-level diff against the previous plan, records affected routes, and stores both timings and both plans in an event. The UI animates the corridor state from that response, so the map, plan table, timings, diff and inventory chart cannot disagree.

In a recorded Zoji La closure the router took 28.5 ms, the re-solve 688.5 ms, and the plan changed by 6 cancelled, 5 added and 4 modified deliveries while remaining OPTIMAL with zero modelled stockouts. The west gateway route became unreachable and the south route via Manali, Rohtang, Keylong, Baralacha, Sarchu, Debring and Tanglang La became the active corridor. The 7-day objective rose from Rs 23.14 lakh to Rs 27.94 lakh because the south corridor carries a 33% procurement premium and less daily inflow. The increase is shown, not hidden.

5. Offline edge ledger
Each edge device keeps an append-only ledger in IndexedDB. Every entry stores seq, prev_hash, payload_json, FOB, class, entry type, quantity and a SHA-256 hash over:
seq | prev_hash | payload_json | fob_id | class_id | entry_type | qty.toFixed(4).
The genesis previous hash is 64 zeros. The browser uses WebCrypto and the server uses Python hashlib; a test asserts byte-identical digests for the same input. On sync the server recomputes each hash, verifies the per-device chain head, enforces seq = previous + 1 and the stored previous hash, accepts true duplicates idempotently, and rejects tampered entries, altered replays, sequence gaps, broken links and entries already owned by another device. Accepted receipt entries advance server stock; requisitions are recorded but do not silently change stock. The UI demonstrates a hand edit as "0 accepted, 1 rejected - HASH MISMATCH" while previously accepted entries remain valid.

OPERATOR EXPERIENCE

The prototype has two operator views plus API documentation. The command view is a dark tactical map of the synthetic theatre with the open corridor, alternate corridor, FOBs, depots, passes and planned convoys, alongside a 7-day allocation table, FOB stock versus buffer, closure controls, live timings and a 30-day impact panel. The edge client records requisitions, shows the local hash chain, reports backend online/offline state, syncs pending entries and exposes tamper simulation. FastAPI publishes OpenAPI documentation at /docs. Every operational screen is labelled SIMULATED. The interface is desktop-first and designed for a single dense planning surface rather than a sequence of wizards.

MEASURED RESULTS

The evaluation runs two policies against the same seeded 30-day scenario: identical starting stock, identical synthetic actual demand (755 units), identical weather and troop surge, and a Zoji La closure on days 10-16 inclusive. The naive policy uses nearest-depot resupply, a 3-day fixed lead, trailing-mean demand and no rerouting; convoys are held when the pass is shut. The DRONA policy uses the LightGBM forecast, a 3-day rolling MILP replan, a 1-day lead and corridor-aware rerouting.

Stockout units: 4.15 naive vs 0.25 DRONA (93.9% lower)
Below-buffer unit-days: 983.9 vs 493.3 (49.9% lower)
Cost per unit of demand: Rs 30,242 vs Rs 25,619 (15.3% lower)
Trucks dispatched: 207 vs 160
Truck-km per unit of demand: 34.46 vs 42.06 (22.1% higher)
Mean days of cover at FOBs: 8.6 vs 8.2
Held-requisition counter: 258 vs 0

The last two rows need context. The higher truck-km is the cost of keeping availability through the longer southern corridor during the closure; DRONA-LOG does not claim a fuel saving. The zero hold counter is structural for the DRONA simulation because that policy has no "hold a convoy" path; it should not be read as a measured 100% queue reduction. These caveats appear in the report and the UI. Cost is divided by total demand rather than delivered units, so a policy cannot look cheap by failing to deliver.

WHAT IS DIFFERENT

- Closed loop, not a dashboard: a closure produces new routes, a new plan, a diff and measured timings in one request.
- Terrain and corridor capacity are first-class: gradient-penalised routing, pass deletion, separate corridor inflow caps and procurement premiums.
- The optimisation is capacitated and fleet-aware: integer trucks, per-depot daily limits, one-day lead and shared loads across supply classes.
- Offline integrity is server-verifiable: a hash chain with sequence and previous-link checks, idempotent duplicates and explicit rejection reasons, not just local storage.
- Metrics are computed at runtime from the seeded simulation. Nothing in the results cards is hardcoded, and every operational figure is labelled SIMULATED.
- The demonstration is reproducible: seed 26251, one Docker command, no API keys, no external tile server, no GPU.

TECHNOLOGY

Frontend: React 19, Vite 8, Tailwind CSS 4, MapLibre GL 6, React Router, IndexedDB, Web Crypto.
Backend: Python 3.12, FastAPI, SQLite, LightGBM, OR-Tools with SCIP, NetworkX A*, Pydantic.
Delivery: Docker Compose, nginx SPA and /api reverse proxy, persistent SQLite volume, healthcheck-gated startup.

VALIDATION

The repository contains 24 backend tests and 6 frontend hash-parity tests, all passing on 20 October 2026. Backend tests cover health and network shape, plan feasibility and zero stockouts, forecast shape, gradient routing, Zoji La severance, Khardung La alternate routing, all-FOB reachability, closure flow, metrics labels and consistency, offline sync after restart, receipt stock application, tampered entries, altered replays, sequence gaps and broken links. Frontend tests cover Python/JavaScript digest parity, quantity tampering, chain verification, swapped links and sequence gaps. The production bundle builds successfully.

FEASIBILITY AND DEPLOYMENT

The whole stack runs on a laptop with Docker. docker compose up --build starts the backend, waits for its healthcheck, then starts the frontend. The first boot seeds 49 nodes, 53 edges, 365 days of history and 10 FOBs, then trains three models. The command view is at http://localhost:8080 and OpenAPI at http://localhost:8000/docs. A reset is docker compose down -v followed by docker compose up -d. Recorded solve times are sub-second on the development machine; the UI reports the live value rather than a fixed claim. A production deployment would need real weather and road feeds, authentication and roles, device key registration and signing, and validation against service records; none of those is claimed by this prototype.

IMPACT

The prototype demonstrates a practical decision loop for a logistics staff: anticipate demand, choose a terrain-realistic route, allocate scarce fleet and stock, and recover from a corridor closure in under a second of solve time. It also demonstrates a defensible offline requisition trail that a server can verify rather than simply trust. The measurable effect in the synthetic scenario is fewer stockouts, less time below safety buffers and lower cost per unit of demand, with an explicit fuel-proxy trade-off when a longer corridor is used.

LIMITATIONS AND NEXT STEPS

The data is synthetic and the network is schematic, not survey-grade. The forecast is a point estimate without uncertainty bands. The simulation uses one 30-day scenario and one scripted closure, not a Monte Carlo study. The edge ledger is per-device with a fixed genesis and no device signatures or conflict resolution. Authentication, roles and classified-data handling are out of scope. The next steps are real data adapters, signed device identities, stochastic or robust demand, multi-pass stress testing, browser end-to-end tests and a pilot evaluation with logistics users.

TEAM

Team Subterra Mesh, Team ID 185972, Smart India Hackathon 2026, problem statement SIH26251.
```

---

## Technology Bucket

```text
Software
```

Primary bucket: **Software**. The solution is an AI/ML, optimisation, geospatial and offline-web software system; it does not require custom hardware.

---

## Idea Template Upload

```text
docs/Subterra_Mesh_SIH26251_Idea_Template.pdf
```

- 6 pages, A4 landscape, exported from the final editable PPTX.
- 1.26 MB, below the 10 MB portal limit.
- Team ID 185972 is present on the deck.
- Companion editable file: `submission/final/Subterra_Mesh_SIH26251.pptx`.

---

## Character count summary

Counts were verified after the last edit. If any block is changed, recheck the portal limits: title 100, abstract 10,000, description 50,000.
