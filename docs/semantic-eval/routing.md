# Channel-aware routing and selection, and a PPR best-first router (both rejected)

Status: **rejected, 2026-10-11; implementation reverted.** Follow-up to the
LFM pilot in [README.md](README.md). Lexical-only retrieval, the BFS
TreeRouter (`oxide-router-bfs-v1`) and the heuristic judge
(`heuristic-evidence` 1) stay as they are, and every frozen Phase 3/4/5
artifact is unchanged. Gates were frozen before any measurement in
[preregistration.md](preregistration.md) (sha256 `dde89642…b8a0`; it
was written and hashed before the first run but is committed together with
the results, so git does not attest the freeze). The
code is kept as `routing-challengers.patch` (applies to `ee8acf0`). Raw
per-task results are in `routing.json`, the recorded query hits in `hits/`,
and the aggregation is `scripts/routing_summary.py`. Numbers are measured
unless marked *inferred*.

## Question

The pilot found that semantic hits never reached the ContextBundle.
Appended entry points crowded the 64-region route, and the heuristic gave
a semantic-only entry point 0.3, below the 0.4 include floor. If routing
and judging take channel provenance into account, does the evidence reach
the bundle and improve it? Separately, does a personalized-PageRank
best-first router pick better evidence than BFS under the same workload?

## What was built (in the patch)

- **Semantic channel, re-integrated selectively.** The pilot patch's
  kernel input (`Semantic`, rank-only `Append`/`Rrf` fusion, recipe
  `oxide-semantic-recipe-v1`) is back. Its runtime client is a blocking
  `ureq` llama.cpp client instead of Rig and tokio. No dependency is
  added, so serde_json features, and with them JEV request bytes, are
  unchanged (*inferred*: the frozen-baseline suite was not re-run with
  the patch applied). No sorted-key writers and no vendored crate.
  Fixed review findings from the pilot patch: no `block_on`
  (`LlamaCpp::embed` is synchronous), and runner failures are typed
  (404 → `ModelMissing`, other statuses → `Rejected` with the body,
  transport errors → `Unavailable`/`Timeout`, malformed or duplicate
  indices → `Malformed`). Still open, and not needed after the rejection:
  the per-query model-swap check, transient-batch abort, batched token
  window, and cache record re-validation.
- **Channel-aware routing, through entry admission.** `Fusion::Interleave
  { depth, keep }` alternates channels by rank (lexical 1, semantic 1,
  lexical 2, …) and deduplicates by entity, keeping all evidence. The
  first `keep` entities enter. BFS loads entry points in CandidateSet
  order, so this decides which channel's neighbourhood the shared
  64-region budget explores. The router itself, its limits and the
  lexical-only path are unchanged.
- **Channel-aware judge** (`/ch`, a candidate DecisionProvider in the
  harness): an entry point from any scored channel gets the heuristic's
  lexical rank prior, `0.85 − 0.025·(rank − 1) ≥ 0.5`, at its best rank
  in any channel. It uses ranks only and leaves everything else to the
  heuristic, so lexical-only runs come out unchanged. The harness asserts
  that the packed gold at 1,024 is the same per task, and the recall at
  every budget was measured to be equal.
  Capsule v2, confidence handling and Rust-owned selection are untouched.
- **PPR best-first router** (`Strategy::Ppr`, isolated `ppr.rs`). Each
  evidence lane present (seeds, lexical, semantic) gets equal seed mass,
  spread over its entries as `1/(k + rank)`. The frontier region with the
  most residual mass loads next, with ties broken by `RegionId`. Loading
  keeps α = 0.15 of the mass and pushes the rest to the BFS expansion (cut
  to the same fanout). Each neighbour's share is divided by the region's
  full pre-cut degree, which damps hubs; scope and logical-containment
  links weigh ½. Mass reaching an already-loaded region is absorbed, which
  protects against cycles. Depth uses the shortest path seen. The work
  bounds are BFS's: 64 hydrations, one adjacency read each, no graph
  preview. Two preregistered settings: k = 60 (the frozen RRF constant)
  and k = 1 (top-heavy). One difference from BFS is not just ordering.
  BFS cuts children and cross-edges to 16 each *before* the follow-kind
  filter and the merged cut. PPR filters and merges the full lists and
  then cuts. So a region with more than 16 cross-edges, or with
  unfollowed edge kinds, can hand PPR different neighbours than BFS
  (review finding).
- **Harness** `runtime/tests/semantic_routing.rs` (ignored,
  machine-local). Every configuration uses the same lexical limit 20,
  semantic depth 20, route/graph/selection baselines and budgets
  256/1,024/4,096. Query hits are recorded once per task and replayed, so
  reruns need no runner.

## Data

Dev split only. Calibration and test stay sealed: no dev gate passed, so
they were never opened. Lexical and PPR configurations ran on 9 tasks
(sphinx 4, keras 5). Sympy's 5 tasks were OOM-killed at the 3 GB harness
cap while indexing (as in the pilot) and are excluded, as the
preregistration allows. Semantic configurations ran on the 4 sphinx tasks
(21 gold symbols), whose vectors were already cached. Nothing was
re-embedded: 0 of 6,569–7,128 unique documents per task.

## Results

Semantic repository (sphinx, 4 tasks). Recall is the task mean of Phase 4
`required_symbol_recall` over *all* verified gold: 25 entities, including
file-level gold credited by range cover. Routed gold and packed deltas are
pooled gold *symbols* (21) at 1,024 units, compared with `lexical`. The two
denominators differ, and likewise 39 against 33 on the 9 lexical tasks.

| configuration | recall @256 / @1,024 / @4,096 | routed gold (pooled) | packed @1,024 +/− |
|---|---|---|---|
| `lexical` (baseline) | 0.042 / 0.183 / 0.267 | 11/21 | — |
| `semantic` (existing judge) | 0.000 / 0.042 / 0.115 | 10/21 | +0 / −3 |
| `semantic/ch` | 0.083 / 0.115 / 0.198 | 10/21 | +3 / −4 |
| `append` (hybrid, existing policy) | 0.042 / 0.183 / 0.267 | 10/21 | +0 / −0 |
| `append/ch` | 0.000 / 0.125 / 0.340 | 10/21 | +2 / −3 |
| **`interleave40/ch`** (primary) | 0.000 / 0.125 / 0.340 | **11/21** | +2 / −3 |
| `interleave20/ch` (equal entry count) | 0.000 / 0.125 / 0.340 | 10/21 | +2 / −3 |
| `rrf20` (frozen RRF) / `rrf20/ch` | 0.042 / 0.183 / 0.267 | 9/21 | +0 / −0 |
| `interleave40/ch/ppr60`, `/ppr1` | 0.000 / 0.125 / 0.340 | 11/21 | +2 / −3 |
| `control/ch` (body BM25 only) | 0.000 / 0.083 / 0.167 | 6/21 | +2 / −4 |
| `interleave40-control/ch` | 0.000 / 0.125 / 0.308 | 9/21 | +2 / −3 |
| *post hoc* `interleave40/fill` | 0.042 / 0.183 / 0.402 | 11/21 | +0 / −0 |
| *post hoc* `interleave40-control/fill` | 0.042 / 0.183 / 0.402 | 9/21 | +0 / −0 |

Lexical-only, 9 dev tasks (33 gold symbols):

| router | recall @256 / @1,024 / @4,096 | routed coverage @1,024 | routed gold (pooled) | edges examined | route p50 |
|---|---|---|---|---|---|
| BFS (baseline) | 0.019 / 0.304 / 0.359 | 0.663 | 19/33 | 635 | 140 ms |
| PPR k = 60 | 0.019 / 0.304 / 0.396 | 0.619 | 17/33 | 696 | 150 ms |
| PPR k = 1 | 0.019 / 0.304 / 0.359 | 0.619 | 17/33 | 689 | 148 ms |

All routers loaded exactly 64 regions. Each load is one `tree::region()`
call: one entity read and one adjacency read of at most
`MAX_REQUEST_ITEMS` edges, the same request for both routers, and neither
does a preview read. That comes from the code; the harness records it,
it does not count it. PPR's frontier is about 270 regions against BFS's 226. It
reaches depth 2 in every task, where BFS stops at depth 1 in one. Route
time is one timed route per configuration and task under a 200% CPU quota,
always with the heuristic. The route columns for `/ch` rows therefore
describe the heuristic route, not the one judged by the `/ch` provider.
Its branch values are the heuristic's 0.5, which never prune, so the two
routes are the same (*inferred*). The frontier count includes deferred
steps; PPR can record a depth deferral twice.

### Gates

- **Semantic integration fails.** For `interleave40/ch` against
  `lexical`: recall changes by −0.042 at 256, −0.058 at 1,024 and +0.073
  at 4,096, where gate 1 needed ≥ +0.05 at 1,024 and no loss elsewhere.
  Pooled packed gold is +2/−3, where gate 2 needed a net of +2 or more.
  It ties the body-BM25 control at 1,024 (gate 3 needed a gain). Gate 4
  was not fully measured. Per task there was one first query
  (0.79–0.98 s) and three warm ones (0.77–0.94 s), so the latency half
  passes on that small sample. The RSS half could not be separated,
  because every configuration ran in one process (peak 286–333 MiB on
  the sphinx tasks, vectors 26–28 MiB). The other hybrids are no
  better.
- **PPR fails.** Recall at 1,024 is +0.000 for both settings, with
  bootstrap interval [0, 0] (gate 1 needed ≥ +0.03 and a lower bound
  > 0). Routed coverage drops by 0.044 for both (gate 2). Work and
  latency are within bounds (gate 3). k = 60's +0.037 at 4,096 comes
  from one task.

## What the evidence shows

1. **Semantic gold now reaches the bundle, but the net change is
   negative.** With the channel-aware judge, semantic-only gold is packed
   (+2 at 1,024; `semantic/ch` +3), where the existing judge packed none.
   The net is still +2/−3. Interleaving's pooled routed gold equals
   lexical's (11/21, against 10/21 when appended), but per task it is not
   a recovery: `f0418d70` routes 2 fewer gold symbols and `9c4b27a6` 2
   more.
2. **The remaining limitation: channel ranks are not comparable, and
   nothing in the deterministic path calibrates them.** At 1,024 units a
   bundle holds 2–4 primaries. Every slot a semantic hit takes, a lexical
   hit loses. Whether that helps depends on the task, and nothing in the
   capsule predicts it. On `5462d419` the channel is strong (R@20 0.67):
   +2/−1, recall from 0.333 to 0.5. On `f0418d70` it has no gold in its
   top 20 (R@20 0.0), and its rank-1 noise, valued like lexical rank 1,
   pushes out both lexical gold symbols: 0.4 → 0.0. A per-channel prior
   that knew each channel's precision at each rank, per repository or per
   query, would need labelled held-out data (a calibration fit, SPEC §
   Uncertainty). A learned judge reading the capsule's channel evidence
   is the designed place for that. Neither exists offline without
   training, which is out of scope.
3. **The model adds nothing end to end over body-text BM25.** The
   post-hoc `fill` judge (not preregistered, and not a promotion
   candidate) ranks semantic-only entries below every lexical one but
   above the include floor. It removes the displacement: no loss at 256
   or 1,024, and +0.135 at 4,096, all of it from two tasks (`5462d419`,
   `9c4b27a6`). Body-text BM25 under the same policy has the same recall
   at every budget on every task, though not through the same candidates.
   On `9c4b27a6` its gain is file-level gold covered by packed ranges,
   with no routed gold symbol. So the model's 4,096-unit gain is no
   larger than what a channel that reads symbol bodies gets, and such a
   channel would provide it at about 0.1 s and no runner (*inferred* from the
   control's 103–118 ms in this harness). The LFM channel costs 0.8–1.0 s
   per query, a 0.6 GB runner, and a 23-minute cold embedding per sphinx
   snapshot (pilot).
4. **PPR reorders exploration without selecting better evidence.** Under
   the heuristic judge, routed-only candidates (0.45 at depth 1, 0.3
   deeper) lose to every entry point at 1,024 units. As in Phase 5
   follow-ups 2 and 4, changing which routed-only regions are explored
   cannot change the bundle. PPR also routed 2 fewer gold symbols:
   reaching depth 2 costs depth-1 regions that BFS kept (*inferred*). A
   router that prioritizes better would only pay off with a judge that
   separates routed candidates, and that judge is JEV, which has no
   offline answers for new capsules.

## Known defects of the rejected code (for a re-application)

- PPR records `stopped_by: Depth` whenever a depth deferral happens
  before the region bound fires, even when the 64-region bound ends the
  route. The metrics are not affected.
- Recall here counts file-level gold (see Results); a re-run should
  report symbol-only recall beside it.
- The ureq client's per-query model check still compares only `/props`
  `model_path` and `build_info`. A swapped file at the same path is caught
  only by the build-time digest.

## Next steps the evidence supports (not done)

- A body-text lexical channel with a non-displacing admission and judge
  rule is the cheapest candidate for the 4,096-unit gain. *Tested since*,
  in [../lexical-fields-eval/](../lexical-fields-eval/README.md): no gain
  at 1,024 units, so it was rejected. It needs its
  own preregistration and held-out confirmation; n = 4 is indicative only.
- Semantic evidence needs calibrated per-channel value. That means a
  judge with held-out calibration (DecisionBench), not a hand-set prior.

## Reproduce

`git apply docs/semantic-eval/routing-challengers.patch`, copy `hits/*.json`
to `$OXIDE_DECISIONBENCH_DATA/semantic-eval/`, then:

```text
OXIDE_CB_WORKTREES=<dir>:<dir> OXIDE_SEMANTIC_REPOS=sphinx-doc/sphinx \
  OXIDE_SKIP_REPOS=sympy/sympy \
  OXIDE_SEMANTIC_CANARY_LFM=496,-9,89,-42,-587,-555,116,-281,-219,47,219,607,-31,209,-76,564 \
  cargo test --release -p oxide-runtime --test semantic_routing -- --ignored --nocapture
python3 docs/semantic-eval/scripts/routing_summary.py docs/semantic-eval/routing.json
```

Run it under the usual caps (`systemd-run --user --scope -p MemoryMax=3G
-p MemorySwapMax=0 -p CPUQuota=200%`, `-j 2`). With the hits replayed, no
runner is contacted. Latencies vary from run to run, but recall and stage
counts are deterministic.
