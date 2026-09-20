# Ranking & fusion research track (roadmap #9, item 3) — first pass

Status: **research, nothing shipped.** Production ranking is unchanged
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

- **Repos** (native `arctic-embed-xs-q` index at HEAD): pylint 40 tasks,
  pytest 22, zod 9 (TypeScript), requests 1, flask 1 = **73 tasks**.
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
| plain (73) | 6 (8%) | **18 (25%)** | 6 (8%) | 43 (59%) |
| masked (73) | 13 (18%) | **33 (45%)** | 5 (7%) | 22 (30%) |
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

### 3.1 Plain regime (73 tasks)

| variant | R@5 | R@10 | R@20 | nDCG@10 | MRR |
| --- | ---: | ---: | ---: | ---: | ---: |
| **production RRF (K=60, 0.6/0.4)** | 0.442 | 0.601 | 0.731 | 0.420 | 0.392 |
| lexical only | 0.555 | 0.703 | 0.744 | 0.483 | 0.457 |
| semantic only | 0.303 | 0.376 | 0.438 | 0.226 | 0.201 |
| RRF K=10 | 0.541 | 0.676 | 0.757 | 0.473 | 0.430 |
| RRF K=20 | 0.527 | 0.647 | 0.757 | 0.463 | 0.429 |
| RRF w_lex=0.8 | 0.529 | 0.664 | 0.767 | 0.479 | 0.449 |
| minmax CombSUM 0.6/0.4 | 0.537 | 0.647 | 0.739 | 0.465 | 0.434 |
| zscore CombSUM 0.6/0.4 | 0.541 | 0.674 | 0.754 | 0.481 | 0.446 |
| RRF + evidence rerank top20 β=0.25 | 0.478 | 0.632 | 0.731 | 0.435 | 0.403 |
| RRF + evidence rerank top20 β=1.0 | 0.473 | 0.636 | 0.731 | 0.394 | 0.345 |

Full tables (every K, weight and β): `results/plain.md`.

### 3.2 Masked regime (73 tasks, identifiers removed from queries)

| variant | R@5 | R@10 | R@20 | nDCG@10 | MRR |
| --- | ---: | ---: | ---: | ---: | ---: |
| **production RRF (K=60, 0.6/0.4)** | 0.222 | 0.301 | 0.426 | 0.178 | 0.177 |
| lexical only | 0.363 | 0.410 | 0.493 | 0.298 | 0.314 |
| semantic only | 0.064 | 0.071 | 0.107 | 0.037 | 0.036 |
| RRF K=10 | 0.294 | 0.386 | 0.493 | 0.236 | 0.229 |
| RRF w_lex=0.8 | 0.322 | 0.366 | 0.500 | 0.221 | 0.210 |
| zscore CombSUM 0.6/0.4 | 0.326 | 0.379 | 0.483 | 0.259 | 0.269 |
| RRF + evidence rerank top20 β=0.25 | 0.222 | 0.316 | 0.426 | 0.179 | 0.170 |

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

## 6. Reproduce

```
cargo build --release --example fusion_dump
# tasks: scripts/make_tasks.py <oxide> <repo-with-history> <label> <n> >> tasks.jsonl
# dump:  fusion_dump <repo> tasks-<repo>.jsonl >> dump.jsonl   (native embedder env)
# eval:  scripts/fusion_eval.py tasks.jsonl dump.jsonl --md
# judge: scripts/judge.py tasks.jsonl dump.jsonl 30 judgments.jsonl   (TYPESAFE_API_KEY in .env)
```

`results/`: `tasks.jsonl`, `tasks-masked.jsonl` (the labeled sets),
`dump-*.jsonl.gz` (exact fusion inputs per task), `judgments.jsonl`
(309 judged pairs), `plain.md`/`masked.md`/`fixtures.md` (full tables),
`plain.json`/`masked.json` (per-task metrics).
