# Phase 5 follow-up preregistration: JEV orchestration latency and routing recall loss

Frozen 2026-10-10, before any measurement of the configurations below. It
builds on Phase 5 (`1de6429`) and changes none of its artifacts. Changing
this file after results exist needs a new, dated version.

## Known before freezing

From the Phase 5 live run (machine-local caches), measured: a JEV context
request at 256 units took p50 21.1 s and p95 24.9 s; the heuristic took
0.33 s / 0.48 s. It made p50 66 and p95 76 JEV requests, one at a time.
The median added latency per request was 316 ms, while a request's own
round trip was p50 306 ms. So the latency is sequential calls, not
per-request overhead. Routed gold coverage was 36–40%: 60–64% of verified
gold was never routed. Every ContextBench route loaded the 64-region
limit.

## Part A: JEV orchestration

Offline only. No live JEV call. Each recorded answer is served after its
own recorded live round trip (`elapsed_ms`). This simulates the service at
recorded latency, under no extra concurrency load: how the service behaves
under concurrent load is not measured here.

| Config | Judgment |
| --- | --- |
| A | heuristic, no provider |
| B | JEV, `concurrency` 1 (Phase 5 behavior) |
| C-k | JEV, `concurrency` k ∈ {4, 8, 16} |
| D-K | C-8 with the shared judgment allowance set to K ∈ {16, 32} (the first K capsules in graph order are judged, the rest keep the heuristic value) |

All configs use the Phase 4 baseline config and the Phase 5 C setup
(`CandidatesOnly`, `Disclosure::Source`), at 1,024 units. Measured per
context: end-to-end latency (p50/p95), time in the judge, time in the
transport, judge calls and requests, input tokens, cost, fallbacks, peak
RSS, and the Phase 4 context metrics.

- **Simulation check**: simulated B p50 within 15% of the live 21.1 s.
  Otherwise the simulation is reported as unvalidated.
- **C acceptance**: for every task, C-k's decisions, selection plan and
  bundle are byte-identical to B's, so quality is preserved by
  construction. The p50 latency reduction is reported. The recommended
  concurrency is the smallest k with p50 ≤ 3 s, or 16 if none reaches it.
- **D (selective judging)**: chosen on dev only. D-K is acceptable if, on
  dev at 1,024 units, D-K − C has a mean fully packed recall ≥ −0.01 and a
  95% task-bootstrap lower bound > −0.05. The smallest acceptable K is then
  confirmed once on the calibration split by the same rule. The test split
  is not used.

## Part B: routing recall waterfall and experiments

The waterfall classifies each verified gold entity (Phase 5 labels) by
where it was lost. Verified spans that map only to a file are reported as
extraction losses, as are unverified spans. For each verified gold symbol
under the frozen baseline at 1,024 units:

- **packed**: fully covered by a bundle item. Coverage through an enclosing
  item is counted separately.
- If it was routed:
  - **graph truncation**: not in the 64-node graph;
  - **selection**: in the graph but not planned;
  - **budget**: planned but not fully packed.
- If it was not routed, the first test that holds:
  - **work limit**: reachable within depth 2 from the actual entry points,
    under the baseline follow policy, once region, edge and fanout work is
    unbounded;
  - **entry limit**: so reachable from the top 200 lexical hits, but not
    from the top 20 used;
  - **depth limit**: reachable within depth 3 from the actual entry points,
    with unbounded work;
  - **lexical miss and graph gap**: none of the above.

Each gold symbol's lexical rank, if any, is reported alongside.

Reported per split (dev, calibration, test) for the frozen baseline, with
B (heuristic) and, where recorded answers exist, C (JEV) for stages 7–8.
This is descriptive. It tunes nothing.

Routing experiments run on dev only, with the heuristic (B), because new
capsules have no recorded JEV answer. Each changes one knob at equal size
(64-node graph) and at most equal work (≤ 64 regions, ≤ 2,048 edges):
`lexical_limit` 10 and 40; `max_depth` 1 and 3; `max_fanout` 8 and 32.
Control: lexical-only routing, with `lexical_limit` 64 and `max_depth` 0.

- **Acceptance**: a variant beats the baseline on dev if routed-gold
  coverage improves by a mean ≥ +0.05 with a 95% lower bound > 0, and B
  fully packed recall at 1,024 has a 95% lower bound > −0.02. The best
  accepted variant, if any, is confirmed once on calibration (mean coverage
  gain > 0, recall mean ≥ −0.01). Only a confirmed variant may become a new,
  versioned routing config. The Phase 3/4 baselines are never edited.
