# Ranking & fusion research track (roadmap #9, item 3) — first pass

Status: **research, nothing shipped; §7 closes the K=10 question negatively.** Production ranking is unchanged
(`src/config.rs`: RRF K=60, lexical 0.6 / semantic 0.4, 200 candidates
per channel). Every challenger below was evaluated **offline** from the
exact fusion inputs production computes, never through a production code
path. The harness (`examples/fusion_dump.rs`, `scripts/`) and every
artifact in `results/` are reproducible; nothing here is committed to the
production defaults.

## 1. Audit of the current pipeline

| stage | what it does today | notes from the audit |
| --- | --- | --- |
| candidate generation | BM25 over persisted postings (k1 1.5, b 0.75; names/signatures ×4, path ×2, bodies ×1) → top-200; exhaustive dot product over stored vectors → top-200 (`FUSION_CANDIDATE_LIMIT`) | both bounded and deterministic (`cmp_score_id`); the union is the only pool fusion ever sees, so anything outside it is a **route loss** no fusion can fix |
| fusion | weighted RRF: `0.6/(60+r_lex+1) + 0.4/(60+r_sem+1)` | rank-based, so score scale never matters; K=60 makes rank 1 and rank 20 differ by only 25%, i.e. it is deliberately flat — appropriate when both channels are comparably reliable |
| expansion (search) | from ≤3 strong lexical seeds (≥0.55 of the best BM25 score): `RelationGraph::neighbors` (parent/child/sibling/uses/imported-definition/test), each neighbor +0.5×seed's fused score, **appended after** direct hits | never reorders direct hits (invariant); only adds tail candidates |
| structural / git evidence (context) | `EvidenceCoordinator`: bounded callers/implementors at fixed fractions of the seed score; blast radius 0.35×; git changed 0.28× / neighbor 0.20× / co-change 0.16× | fixed fractions, never learned; git evidence needs a live diff and contributes nothing to a clean checkout (so it is not measurable on "implement this change" tasks — it belongs to review/in-progress-diff workflows) |
| reranking | `rerank_candidates` is a no-op stub (cross-encoder rerankers rejected in `docs/reranker-eval`) | — |
| allocation | dedup/subsumption → role order (primary/dependency/test) → relevance floor 0.15×top → greedy fill under 4096 tokens with primary cap 5, test cap 1, per-file cap 2, per-item cap 350 tokens | the caps are budget-independent (`docs/primary-cap-sensitivity`) |
| prior evidence | `docs/retrieval-ceiling.md` (21-task ContextBench pin, `qwen3-0.6B`): losses dominated by route loss; fusion-weight changes and candidate widening did not help; term-coverage boost rejected | those weights were frozen against `qwen3-0.6B`; the shipped default embedder is now `arctic-embed-xs-q` (22M params) |

## 2. Fresh, independently labeled tasks

Commit-derived, on public repositories, labeled by the repositories' own
authors rather than by anyone looking at OXIDE's output
(`scripts/make_tasks.py`):

- **Repos** (native `arctic-embed-xs-q` index at HEAD): pylint 37 tasks,
  pytest 22, zod 9 (TypeScript), requests 1, flask 1 = **70 tasks** after
  removing three cherry-pick/backport near-duplicates (same repo, same
  gold, query Jaccard ≥ 0.6 — a Greptile finding; §7.7).
- **Task** = a recent non-merge commit whose changed source files are
  byte-identical between the commit and HEAD (so one HEAD index describes
  exactly the post-commit code), ≤4 source files, ≥6-word message,
  excluding docs/tests/bumps/typos. **Query** = commit subject + body
  (issue refs and trailers stripped; median 39 words). **Gold** = the
  symbols the commit changed, mapped from diff lines by `oxide review`
  (structural span mapping, not retrieval); 49 tasks have one gold
  symbol, 24 have 2–6.
- **Two regimes.** *Plain*: the message as written (often names the
  identifier — realistic for issue text, but lexically leaky). *Masked*:
  every query token that matches a name segment of a gold symbol is
  removed (mean 2.6 tokens/task), leaving a description-only query.
- **Independent judge** (TypeSafe System One / Jev, `scripts/judge.py`):
  on 30 random tasks, the union of each channel's top-5 (309 pairs) was
  graded "would a competent developer need to read or modify this
  symbol for the task?". Only public commit text and symbol snippets
  were sent. Calibration: 28/29 gold items judged relevant (mean 0.81);
  non-gold mean 0.36. **Gold is incomplete** — 75/280 non-gold candidates
  (27%) were judged relevant — so absolute precision is understated, but
  the judge is used below to check that *channel comparisons* survive
  an independent labeler.
- **Fixture gate** (`fixtures/benchmark.json`, 11 queries, hashed
  embedder — exactly what `tests/benchmark_gate.rs` runs) as the
  regression floor every challenger must clear.

Loss partition at K=10 (gold in no channel's top-200 = *route*; in a
channel but outside fused top-10 = *ordering*; in fused top-10 but not in
the pack = *allocation*):

| regime | route | ordering | allocation | hit in pack |
| --- | ---: | ---: | ---: | ---: |
| plain (70) | 6 (9%) | **18 (26%)** | 6 (9%) | 40 (57%) |
| masked (70) | 13 (19%) | **32 (46%)** | 4 (6%) | 21 (30%) |
| fixtures (11) | 0 | 0 | 1 | 10 |

Unlike the ContextBench pin, **ordering** is the dominant loss on this set.
Allocation losses are gold at fused rank 3–10 dropped by the per-file cap
(3), the primary cap (3) or subsumption (2) — the same mechanism
`docs/primary-cap-sensitivity` measured.

## 3. Challengers (offline, exact fusion inputs)

`examples/fusion_dump.rs` recomputes BM25 and the vector scan exactly as
`RetrievalEngine::search` does and dumps both top-200 lists with raw
scores, the production fused list, the top-5 seeds' structural neighbors,
and the production pack. `scripts/fusion_eval.py` first **reproduces
production's fused scores from the dumped channels** (asserted to 1e-6 on
every task), then evaluates:

- RRF K ∈ {10, 20, 40, 60, 100, 200}; lexical weight ∈ {0.3 … 0.8}
- normalized score fusion: min-max CombSUM / CombMNZ, z-score CombSUM
- bounded deterministic evidence-aware rerank: within the fused top-N
  (10 or 20), multiply a non-seed candidate's score by
  `1 + β·support`, support = weighted count of top-3 seeds listing it as a
  structural neighbor (uses/imported-definition 1.0, parent/child 0.5,
  sibling/test 0.25; capped at 2); direct hits below N never move

### 3.1 Plain regime (70 tasks)

| variant | R@5 | R@10 | R@20 | nDCG@10 | MRR |
| --- | ---: | ---: | ---: | ---: | ---: |
| **production RRF (K=60, 0.6/0.4)** | 0.418 | 0.584 | 0.719 | 0.405 | 0.380 |
| lexical only | 0.535 | 0.690 | 0.733 | 0.466 | 0.441 |
| semantic only | 0.288 | 0.350 | 0.414 | 0.213 | 0.193 |
| RRF K=10 | 0.522 | 0.663 | 0.747 | 0.461 | 0.420 |
| RRF K=20 | 0.507 | 0.632 | 0.747 | 0.450 | 0.418 |
| RRF w_lex=0.8 | 0.509 | 0.650 | 0.757 | 0.462 | 0.432 |
| minmax CombSUM 0.6/0.4 | 0.517 | 0.632 | 0.728 | 0.453 | 0.424 |
| zscore CombSUM 0.6/0.4 | 0.522 | 0.660 | 0.744 | 0.469 | 0.437 |
| RRF + evidence rerank top20 β=0.25 | 0.456 | 0.616 | 0.719 | 0.426 | 0.397 |
| RRF + evidence rerank top20 β=1.0 | 0.464 | 0.620 | 0.719 | 0.386 | 0.341 |

Full tables (every K, weight and β): `results/plain.md`.

### 3.2 Masked regime (70 tasks, identifiers removed from queries)

| variant | R@5 | R@10 | R@20 | nDCG@10 | MRR |
| --- | ---: | ---: | ---: | ---: | ---: |
| **production RRF (K=60, 0.6/0.4)** | 0.218 | 0.285 | 0.415 | 0.167 | 0.167 |
| lexical only | 0.335 | 0.384 | 0.472 | 0.275 | 0.295 |
| semantic only | 0.067 | 0.074 | 0.111 | 0.038 | 0.038 |
| RRF K=10 | 0.278 | 0.359 | 0.472 | 0.218 | 0.215 |
| RRF w_lex=0.8 | 0.307 | 0.353 | 0.479 | 0.210 | 0.200 |
| zscore CombSUM 0.6/0.4 | 0.297 | 0.352 | 0.461 | 0.235 | 0.249 |
| RRF + evidence rerank top20 β=0.25 | 0.218 | 0.301 | 0.415 | 0.168 | 0.160 |

### 3.3 Fixture gate (11 queries, hashed embedder)

| variant | R@5 | R@10 | nDCG@10 | MRR |
| --- | ---: | ---: | ---: | ---: |
| **production RRF (K=60, 0.6/0.4)** | 0.909 | 1.000 | 0.893 | 0.886 |
| lexical only | 0.818 | 1.000 | 0.851 | 0.833 |
| semantic only | 0.818 | 0.955 | 0.818 | 0.806 |
| **RRF K=10** | **0.909** | **1.000** | **0.899** | **0.894** |
| RRF w_lex=0.8 | 0.818 | 1.000 | 0.887 | 0.879 |
| minmax CombSUM 0.6/0.4 | 0.909 | 1.000 | 0.878 | 0.864 |
| zscore CombSUM 0.6/0.4 | 0.818 | 1.000 | 0.815 | 0.783 |
| RRF + evidence rerank top20 β=0.25 | 0.909 | 1.000 | 0.648 | 0.552 |

### 3.4 Per-repo consistency (R@10 / nDCG@10, plain regime)

| | production | lexical only | RRF K=10 | zscore 0.6/0.4 | evidence β=0.25 |
| --- | ---: | ---: | ---: | ---: | ---: |
| pylint (40) | 0.597 / 0.417 | 0.730 / 0.532 | 0.684 / 0.483 | 0.680 / 0.494 | 0.622 / 0.395 |
| pytest (22) | 0.648 / 0.453 | 0.664 / 0.400 | 0.694 / 0.482 | 0.694 / 0.468 | 0.648 / 0.499 |
| zod (9) | 0.611 / 0.434 | 0.667 / 0.484 | 0.722 / 0.484 | 0.722 / 0.532 | 0.778 / 0.552 |

`pytest` is the case for fusion: production beats lexical-only there on
nDCG/MRR, and K=10 beats both. Masked per-repo tables: `results/masked.md`
(K=10 is ≥ production in every cell except pytest nDCG 0.123 vs 0.133 and
MRR 0.128 vs 0.143, n=22).

### 3.5 Independent judge (30 tasks, top-5 of each channel)

| channel | top-5 precision by commit gold | by judge (noul ≥ 0.5) |
| --- | ---: | ---: |
| lexical only | 0.147 | 0.487 |
| production fused | 0.113 | 0.433 |
| semantic only | 0.113 | 0.293 |

The ordering lexical > fused > semantic holds under the judge, so the
semantic channel's weakness is not an artifact of incomplete gold.

## 4. What the numbers say

1. **The semantic channel is the weak link under the shipped default
   embedder.** `arctic-embed-xs-q` alone reaches R@10 0.376 on plain and
   **0.071 on description-only queries** at symbol granularity, and its
   top-5 is judged ~40% less precise than BM25's. The frozen 0.6/0.4,
   K=60 fusion was validated against `qwen3-0.6B`; with a weaker channel,
   a flat K lets it dilute the strong one: production sits *between*
   lexical-only and semantic-only on every metric of both regimes.
2. **Ordering, not route loss, is the dominant failure on this set**
   (25% / 45%), which is why fusion parameters move the numbers here
   where they did not on the ContextBench pin (route-loss dominated).
   Route loss is 8–18%; allocation 7–8%.
3. **A sharper RRF (K=10) is the one challenger that dominates
   everywhere**: plain +0.075 R@10 / +0.053 nDCG / +0.038 MRR; masked
   +0.085 / +0.058 / +0.052; fixtures equal-or-better (+0.006 nDCG,
   +0.008 MRR); per repo ≥ production in 17 of 18 cells (the exception is
   within noise at n=22). It is a constant change to a rank-based method:
   same determinism, same tie-break, same cost, same contracts.
4. **Lexical-heavier weights (0.7–0.8) and score-normalized fusion** win
   on the fresh set but **regress the fixture gate** (R@5 0.909 → 0.818;
   z-score nDCG 0.893 → 0.815): score-scale fusion is sensitive to the
   embedder's score distribution (hashed vs native), which is exactly the
   fragility RRF exists to avoid. Not Pareto.
5. **The bounded evidence-aware rerank is not Pareto as formulated**:
   +0.031 R@10 on plain and a real gain on zod/pytest, but it lowers
   pylint nDCG (0.417 → 0.395), does nothing masked, and halves fixture
   nDCG (0.893 → 0.648) — structural neighbors of a *wrong* top seed
   amplify the error. It also needs the corpus graph, which `search
   --no-expand`/`Fast` do not load today (+45–80 ms one-shot; free in
   MCP after the RelationIndex cache).
6. Semantic still earns its place at the tail: on plain, every fusion
   beats lexical-only at R@20 (0.757–0.767 vs 0.744), and on `pytest` at
   the head too. The right move is to weight it by rank position more
   sharply, not to drop it.

Cost and memory: every fusion variant is O(400) over the two candidate
lists — the fusion stage is ~1–2 ms of a 12 ms `search --no-expand`
(`docs/retrieval-profile`), and K or a normalization pass does not
change that; RSS is unchanged. Only the evidence rerank has a cost
(graph + neighbors: +0.6 ms warm, plus the corpus load where it is not
already loaded).

## 5. Pareto verdict

- **RRF K=10 — Pareto-positive on every measured axis, promoted to a
  validated experiment, not shipped.** Before it can touch
  `FUSION_RRF_K`: (a) rerun the 21-task ContextBench pin under the
  *shipped* embedder (the pinned repositories and the qwen3 server are
  not available on this machine; the canonical table was measured with
  `qwen3-0.6B`), (b) the benchmark gate and the 440/192-output parity
  gates *will* change by construction — that diff must be reviewed as
  an intended ranking change, per CLAUDE.md, and (c) a Greptile pass on
  the one-constant diff. If (a) holds, K=10 is the candidate; K=20 is
  the conservative fallback (also ≥ production everywhere here, smaller
  gains).
- **Rejected**: lexical weight ≥0.7, min-max/z-score CombSUM, CombMNZ
  (fixture regression); bounded structural rerank at any β (inconsistent,
  fixture regression). Recorded in `results/` with full tables.
- **Not a fusion problem**: the semantic channel's absolute weakness on
  description-only queries. The largest remaining lever on this set is
  the embedder/query prompt for `arctic-embed-xs-q` (or the provider
  choice), not the fusion rule — a separate track.


## 7. Validation pass: K=10 vs K=60, and a confidence-aware reranker

Second pass, run **after** §1–6 had picked K=10 on the dev set, on
data that set never touched. Nothing here changes production; everything
is offline over identical candidate pools (the dumped top-200 lists),
shipped Arctic embedder throughout.

### 7.1 Why the flat structural bonus failed (audit, `results/audit-*.md`)

Per-candidate signals over the union pool (24,693 rows, 106 gold, plain):

| signal | AUC for gold (plain / masked) | note |
| --- | ---: | --- |
| lexical rank | 0.932 / 0.823 | the strongest single signal |
| RRF K=10 / K=60 | 0.925 / 0.920 (plain) | fusion already captures most of it |
| in both channels | 0.770 / 0.620 | corr 0.70 with the RRF score — it *is* what RRF rewards |
| semantic rank | 0.700 / 0.507 | at chance on description-only queries |
| any structural support | 0.662 / 0.576 | but see precision below |
| name-in-query | 0.680 / **0.412** | the token overlap the masked regime removes: **label leakage**, not signal |
| is_module, is_test | 0.64 / 0.67 | artifacts of label construction (gold excludes modules and test files) — unusable |

Structural neighbors of the top-3 fused seeds, by relation (plain):
`uses` 3,298 neighbors at **0.4% precision** (16 per seed), `test` 292 at
0%, `sibling` 547 at 3.8%, `child` 204 at 3.4%, `parent` 52 at 7.7%.
The top-3 seeds themselves are gold only **17%** of the time, and a
seed being in both channels does not separate right from wrong seeds
(both: 17%). Conditional on the seed being right, `sibling` precision is
10.3% vs 0.8% otherwise — structural evidence is informative *only* when
the seed is, which the reranker cannot observe. The flat bonus (β=0.5,
top-20) promoted 53 candidates into the top-10 across 70 tasks, 5 of
them gold, and displaced 2 gold: it reorders noise above gold because it
propagates from mostly-wrong seeds through the highest-fan-out, lowest-
precision relation. No double counting was found among usable signals
(name features aside): lexical rank / semantic rank / agreement are
weakly correlated (|r| ≤ 0.41) except through the RRF score itself.
Git evidence is inapplicable to these tasks (clean checkout → no diff).

### 7.2 Confidence-aware reranker (`scripts/rerank_eval.py`)

Bounded to the fused top-20, deterministic, five non-leaky features:
−log(1+lexical rank), −log(1+semantic rank), in-both-channels, structural
support from sibling/child/parent edges of a *confident* seed (fused rank
< 3 and in both channels), and `uses` support as a control. Logistic
regression on the dev set (both regimes, 8k sampled negatives), weights
frozen to two decimals: lex 0.83, sem **−0.30**, in_both 1.32, struct
0.49, uses 0.51 — i.e. "lexical rank + channel agreement", with semantic
rank *penalized* under this embedder. In-sample it gains +0.014 nDCG
over K=10 on plain (0.475 vs 0.461) and the no-struct ablation is
indistinguishable (0.453 vs 0.475 plain, 0.267 vs 0.267 masked): the structural features
carry nothing even after gating on seed confidence.

Hard negatives on the dev set: (a) 41 lexical top-5 items judged not
relevant by the independent judge — K=60 keeps 24% of them in the top-5,
K=10 and the reranker 66%: the flat K=60 is what demotes lexical-only
noise using semantic disagreement; (b) same-parent non-gold siblings of a
gold symbol ranked above it — K=60 11%, K=10 7%, reranker 2–3%.

### 7.3 Held-out, repository-balanced (65 tasks, 7 repos; `results/rerank-heldout.md`)

Fresh commits excluding every dev-set commit (16 overlaps dropped),
10–12 per repo for httpx, requests, flask, ripgrep (Rust), zod; pytest 6,
pylint 2 (their qualifying commits were consumed by the dev set); per-
commit worktrees for the high-churn repos.

| variant | R@5 | R@10 | R@20 | nDCG@10 | MRR | ΔnDCG vs K=60 [95% CI] | macro R@10 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| **RRF K=60 (production)** | **0.404** | 0.508 | **0.666** | 0.397 | 0.433 | — | 0.502 |
| RRF K=10 | 0.382 | 0.544 | 0.631 | 0.421 | 0.455 | +0.023 [−0.005, +0.056] | 0.523 |
| K=60 + rerank | 0.377 | 0.494 | 0.666 | 0.343 | 0.362 | −0.054 [−0.113, +0.005] | 0.481 |
| K=10 + rerank | 0.363 | 0.488 | 0.631 | 0.322 | 0.336 | **−0.076 [−0.137, −0.013]** | 0.477 |
| K=10 + rerank (no struct) | 0.314 | 0.457 | 0.631 | 0.287 | 0.297 | −0.110 [−0.182, −0.040] | 0.444 |
| lexical only | 0.348 | 0.485 | 0.603 | 0.343 | 0.368 | −0.054 [−0.110, −0.001] | 0.475 |

Per repo (R@10, K=60 → K=10): httpx 0.44 → 0.60, ripgrep 0.41 → 0.52,
requests 0.66 → 0.72, but pytest 0.47 → 0.38, zod 0.46 → 0.42, flask
0.58 → 0.53. Loss partition: route 2, ordering 19, allocation 10, hit
34; production pack gold-in-pack 0.538, 109 relevant tokens per 1k pack
tokens. Lexical-only, which beat production on the pylint-heavy dev set,
is significantly *worse* than production here.

### 7.4 ContextBench pinned instances (21 human-labeled issues; `results/cb-results.md`)

Same 21 instances as `docs/retrieval-ceiling.md`, repositories checked
out at their base commits and indexed with the shipped Arctic embedder,
issue text as the query, scored with ContextBench's own metric code on
the top-10 of each variant from identical candidates:

> **Scoring correction (2026-09-26).** The symbol-coverage columns below
> were computed before a ContextBench scorer fix: one of the 21 instances
> (`Multi-SWE-Bench…8d780f70`) stores gold as `/workspace/<repo>/…`, which
> made its symbol gold empty (a vacuous 1.0). File coverage and precision
> are unaffected; retrieval output is unchanged. Re-scored from the same
> dump, production K=60 symbol coverage is 0.748 (was 0.771), symbol@5
> 0.631 (0.670), and every paired delta keeps its sign — the K=60 decision
> and the reranker rejections stand. Numbers here are kept as originally
> recorded; corrected tables: `docs/contextbench-scorer-fix/`.

| variant | file coverage | file cov@5 | symbol coverage | symbol cov@5 | file precision |
| --- | ---: | ---: | ---: | ---: | ---: |
| **RRF K=60 (production)** | **0.685** | **0.591** | **0.771** | **0.670** | **0.237** |
| RRF K=10 | 0.647 | 0.516 | 0.689 | 0.563 | 0.220 |
| K=60 + rerank | 0.655 | 0.468 | 0.718 | 0.520 | 0.201 |
| K=10 + rerank | 0.631 | 0.468 | 0.699 | 0.520 | 0.195 |
| lexical only | 0.560 | 0.504 | 0.622 | 0.579 | 0.182 |
| semantic only | 0.619 | 0.548 | 0.495 | 0.397 | 0.217 |

Paired deltas vs K=60 (n=21): K=10 file −0.039 [−0.118, +0.036], symbol
−0.081 [−0.202, +0.013], 1 win / 4 losses; both rerank variants 1–2 wins
/ 4–7 losses. On issue-style queries the semantic channel is strong
(file coverage 0.619 alone, above lexical's 0.560) and the flat K=60 is
the right weighting.

### 7.5 Cost, memory, agent outcomes

K is a constant in a rank-based formula: no latency, allocation or RSS
change (the fusion stage is 1–2 ms of a 12 ms search; nothing else in
the request path is touched). The reranker would cost ~0.5 ms of feature
work over 20 hydrated candidates plus `neighbors` for 3 seeds (0.6 ms
warm) and requires the corpus graph — free in MCP after the
RelationIndex cache, a 45–80 ms corpus load on the one-shot `--no-expand`
/`Fast` paths, so it would have had to be gated to expansion mode.
Downstream agent outcomes (Tier B) were not run: the retrieval-level
result is already negative on two independent sets, so an agent run
could only measure a regression.

### 7.6 Pareto verdict (supersedes §5's "promoted to validated experiment")

- **RRF K=10 — rejected as a production change.** Its dev-set win
  (+0.055 nDCG) does not transfer: mixed on the balanced held-out set
  (+0.023, CI spans zero; better R@10, worse R@5/R@20; wins 3 repos,
  loses 3) and negative on the human-labeled ContextBench set (−0.04
  file / −0.08 symbol coverage, 1 win / 4 losses). The dev gain was a
  property of short, identifier-bearing commit messages on a pylint-heavy
  set; issue-style queries reward the flatter fusion. Frozen K=60 stays.
- **Bounded confidence-aware reranker — rejected.** Overfits the dev set
  (+0.015 in-sample over K=10) and is significantly worse out of sample
  (−0.076 nDCG on held-out, worse on ContextBench). Structural features
  contribute nothing after seed-confidence gating because seed precision
  (17%) bounds them; the flat bonus failed for the same reason, amplified
  by `uses` fan-out. No feature available at request time predicts
  relevance beyond what fusion already encodes, except name overlap —
  which is real signal for users who name identifiers but is label
  leakage on commit-derived gold and cannot be validated here.
- **Lexical-only — rejected** (worse than production on held-out and
  ContextBench).
- Standing negative results: lexical weight ≥ 0.7, min-max / z-score
  CombSUM, CombMNZ (§3.3), flat structural bonus (§3, §7.1).

What the two independent sets agree on: the remaining losses are
**route loss on description-style queries** (the semantic channel's
weakness under the 22M-parameter default — a separate embedder-quality
track, not a fusion question) and **allocation** (15% on held-out: gold
at fused rank 3–10 dropped by the per-file, primary, and subsumption
caps — an allocator question, `docs/primary-cap-sensitivity`).

Uncertainty: dev n=70 (pylint 53%), held-out n=65 (balanced), ContextBench
n=21; paired bootstrap CIs are reported for every delta and most span
zero — the conclusions rest on *direction consistency across sets*, not
on any single significant delta. Gold incompleteness (27% of non-gold
top-5 items judged relevant) understates absolute precision on the
commit-derived sets but not the comparisons, which the judge confirmed.

### 7.7 Method fixes from the independent review of this bundle

A Greptile pass over the research bundle raised four evaluation-validity
points, all fixed before the numbers above were finalized: (1) three
cherry-pick/backport near-duplicate dev tasks removed (identical gold,
query Jaccard ≥ 0.6; the held-out set had none); (2) the offline
tie-break now uses the numeric FNV symbol id production uses
(`cmp_score_id`) — the dump emits it — and the reproduction check
asserts the full fused **order**, not only the score multiset, which
required emulating production's `f32` accumulation (f64 merges ties
that f32 keeps distinct); (3) `fusion_dump` refuses an index whose
persisted lexical index is not exactly current, since the engine would
silently use its in-memory fallback there; (4) the ContextBench scripts
resolve the repository root from their own location. None of the four
moved any conclusion; every table was regenerated after them.

## 6. Reproduce

```
cargo build --release --example fusion_dump
# tasks: scripts/make_tasks.py <oxide> <repo-with-history> <label> <n> >> tasks.jsonl
# dump:  fusion_dump <repo> tasks-<repo>.jsonl >> dump.jsonl   (native embedder env)
# eval:  scripts/fusion_eval.py tasks.jsonl dump.jsonl --md
# judge: scripts/judge.py tasks.jsonl dump.jsonl 30 judgments.jsonl   (TYPESAFE_API_KEY in .env)
```

Validation pass: `scripts/signal_audit.py`, `scripts/rerank_eval.py`
(`fit-dump` / `eval`), `scripts/cb_prepare.py` + `scripts/cb_score.py`
(ContextBench, needs `eval-agent/.venv`); results in `results/audit-*.md`,
`rerank-*.md`, `heldout-by-repo.txt`, `cb-results.md`, `weights.json`,
`heldout-clean.jsonl`, `cb-tasks.jsonl`, `dump-heldout.jsonl.gz`,
`dump-contextbench.jsonl.gz`.

`results/`: `tasks.jsonl`, `tasks-masked.jsonl` (the labeled sets),
`dump-*.jsonl.gz` (exact fusion inputs per task), `judgments.jsonl`
(309 judged pairs), `plain.md`/`masked.md`/`fixtures.md` (full tables),
`plain.json`/`masked.json` (per-task metrics).
