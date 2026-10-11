# Preregistration: channel-aware routing/selection and PPR best-first routing

Frozen 2026-10-11, before any measurement of the configurations below.
Harness: `oxide_kernel/crates/runtime/tests/semantic_routing.rs`. Dev split
only (`docs/phase-5/decisionbench-v1/splits.json`); calibration and test
stay sealed unless a dev gate below passes. Heuristic judging only; no JEV
call, no training. Gold labels are used only to score runs, never to order,
route or select anything.

## Fixed for every configuration

Lexical limit 20, semantic depth 20 (the pilot's), route limits
`BASELINE_LIMITS` (64 regions, depth 2, fanout 16, 2,048 edges), graph 64
nodes, selection `oxide-select-greedy-v1` baseline policy, budgets
256 / 1,024 / 4,096 oxide-units. Semantic hits: LFM2.5-Embedding-350M
Q4_K_M, recipe `oxide-semantic-recipe-v1`, vectors from the pilot's
cache, query hits recorded once per task and replayed. The body-text BM25
control ranks the same documents.

## Configurations

Lexical (all dev tasks): `lexical` (BFS, heuristic; the baseline),
`lexical/ch` (channel-aware judge; must be identical to `lexical`),
`lexical/ppr60`, `lexical/ppr1` (PPR best-first, alpha 0.15, rank prior
k = 60 or 1).

Semantic (sphinx, the repository whose vectors are cached): `semantic`,
`semantic/ch`, `append` (hybrid, existing policy), `append/ch`,
`interleave40/ch`, `interleave20/ch`, `rrf20` (frozen weighted RRF, K=60,
0.6/0.4), `rrf20/ch`, `interleave40/ch/ppr60`, `interleave40/ch/ppr1`,
`control/ch`, `append-control`, `interleave40-control/ch`.

`/ch` is the channel-aware judge: any channel's entry point gets the
heuristic's lexical rank prior at its best rank in any channel (ranks only);
everything else is the heuristic's. Constants were not fitted.

**Primary corrected hybrid**, chosen before results: `interleave40/ch`
(every lexical entry kept, channels alternate by rank, channel-aware judge,
BFS). The other hybrids are diagnostics.

## Gates

Semantic integration (sphinx dev, `interleave40/ch` vs `lexical`), all of:

1. ContextBundle: mean `required_symbol_recall` at 1,024 ≥ +0.05, and not
   lower at 256 or 4,096 (mean difference ≥ 0).
2. Pooled gold symbols packed at 1,024: gained − lost ≥ 2, lost ≤ 1.
3. Model, not body text: mean recall at 1,024 above
   `interleave40-control/ch`.
4. Cost: query p50 ≤ 1.5 s on the 2-core runner; harness peak RSS within
   +100 MiB of the lexical-only run.

n = 4 tasks cannot establish a reproducible gain. Passing the dev gates
only licenses a confirmation on the calibration split (cold embedding of
its repositories, capped); promotion needs that confirmation with a paired
task-bootstrap 95% lower bound > 0 on recall at 1,024. Failing any dev gate
keeps lexical-only and removes the experimental code.

PPR best-first (lexical-only, every dev task that fits the 3 GB harness
cap; each PPR variant vs `lexical`), all of:

1. Mean `required_symbol_recall` at 1,024 ≥ +0.03 with a paired
   task-bootstrap (10,000 resamples, seed 1) 95% lower bound > 0.
2. Mean recall at 256 and 4,096 not lower by more than 0.01; mean routed
   gold coverage not lower.
3. Equal work: same region loads and adjacency reads (one per load, no
   graph preview); route p50 ≤ 1.5× BFS.

A passing variant needs the same comparison on the calibration split
(lexical-only, no embeddings) before replacing BFS. The hybrid PPR rows
are indicative only (n = 4).
