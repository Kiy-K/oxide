# Phase 5 follow-up 2 preregistration: routing order and JEV stability

Frozen 2026-10-10, before any measurement of the configurations below. It
builds on the Phase 5 follow-up (`4582f6d`) and changes none of its
artifacts. Changing this file after results exist needs a new, dated
version.

## Known before freezing

From `docs/phase-5-followup.md` (measured): 248 of 410 verified gold
symbols are never routed, and 130 of them are reachable within depth 2 of
the 20 entry points once the 64-region budget is lifted. Every route loads
exactly 64 regions. Equal-work knob reallocations (`lexical_limit`,
`max_depth`, `max_fanout`, lexical top-64) never beat the baseline.

From the code (read, not measured): the baseline router is
level-synchronous BFS. Layer 0 loads the 20 entry points; layer 1 is
loaded in the order its regions were discovered, which is entry-point
order. So the 44 remaining loads go to the neighbors of the first few
entry points, and lower-ranked entry points are never expanded (inferred).
Under the heuristic, lexical entry points (relevance 0.5–0.85) outrank
every routed-only candidate (0.45 / 0.3), so the routing order can hardly
move a heuristic bundle. JEV can, but new capsules have no recorded JEV
answer, and no live call is allowed. Routed-gold coverage (gold that
reaches the DecisionProvider) is therefore the primary measure. Heuristic
bundle quality is a non-regression guard only. JEV bundle gains from
routing are not measured here.

## Part A: routing order at equal work

Dev split only, heuristic judge, 1,024 units. Every variant keeps the
baseline limits (64 regions, depth 2, fanout 16, 2,048 edges), the 20
entry points, the baseline follow policy, the 64-node graph and the
selection policy. Only the order in which a layer's regions are loaded
changes. That order matters only when the region budget cuts a layer.

| Variant | Order of each layer |
| --- | --- |
| `bfs` | baseline: discovery order |
| `balanced` | round-robin over the entry point each region descends from: the k-th region of every entry point before the (k+1)-th of any; ties by entry order, then discovery order |
| `lexical` | regions that are top-200 lexical hits first, by lexical rank; the rest in discovery order |
| `lexical+balanced` | lexical hits first by rank, the rest round-robin as in `balanced` |
| `lexical_only=64` | control: lexical top 64, depth 0 (equal-work lexical baseline) |

The lexical ranks come from one extra lexical request (limit 200) with
the same terms as retrieval. Its cost is reported as routing work.

Measured per task: routed-gold coverage (Phase 4 metric), fully packed
recall, gold token share, regions loaded, edges examined, route latency,
and, as a diagnostic, how many baseline-unrouted gold symbols are reached
at depth 1 vs depth 2 with unbounded work.

- **Acceptance (dev)**: routed-gold coverage improves by a mean ≥ +0.05
  with a 95% task-bootstrap lower bound > 0, and fully packed recall at
  1,024 has a 95% lower bound > −0.02. Route latency p50 at most 1.5× the
  baseline's.
- The accepted variant with the highest mean coverage gain is confirmed
  once on calibration: mean coverage gain > 0 and recall mean ≥ −0.01.
- Only a confirmed variant may become the default, as a new router
  version; `oxide-router-bfs-v1` stays available and the Phase 3/4/5
  baselines are not edited. After confirmation, the test split is
  reported once for the confirmed variant and the baseline, descriptively.

## Part B: JEV stability from recorded answers

No live call. Inputs: every recorded exchange in the DecisionBench JEV
sessions and the follow-up live smoke. Value is `score / 2`.

- **B1 drift**: request bodies answered two or more times. Reported: the
  distribution of |Δ value| and |Δ confidence| over all answer pairs, and
  the share of bodies whose answers fall on both sides of the
  `include_floor` (0.4).
- **B2 ranking**: for the 3 smoke tasks, relevance capsules answered both
  in Phase 5 and in the smoke run: Kendall τ between the two answer sets,
  and overlap of the top 10.
- **B3 selection (observed)**: for the 3 smoke tasks, the Phase 5 replay
  and the smoke run's own replay: plan Jaccard, bundle item Jaccard and
  fully packed recall.
- **B4 selection (simulated)**: all 52 tasks, config C at 1,024 units.
  20 replicates per task. In each, every JEV value is shifted by a delta
  drawn (seeded by capsule digest and replicate) from the B1 pool of
  signed pairwise deltas, clamped to [0, 1]; confidences are kept. A
  capsule with no recorded answer falls back as in replay, and those
  fallbacks are counted. Per replicate: plan identical, same plan set in a
  different order, different set; bundle item Jaccard; fully packed recall
  difference from the unperturbed replay.

"Harmless" is a replicate whose plan set is unchanged. "Consequential"
changes the plan set. "Recall-changing" changes fully packed recall. B is
descriptive; it gates nothing.
