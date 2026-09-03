# Term-coverage corroboration experiment

Status: **Original (unbounded-multiplicative) sweep DONE — REVISE. Follow-up
(bounded-additive fix) IN PROGRESS: harness/scoring/eval fixes done and
tested, partial 11/21 rerun leans SAFE-BUT-MARGINALLY-USEFUL, full 21-task
rerun pending.**

## Follow-up status (read this first)

Everything below "## Objective" describes the *original* sweep — unbounded
multiplicative `1 + alpha*coverage`, alphas 0.0-0.5, verdict REVISE. That
verdict drove three fixes, all implemented and tested on `main` working
tree (not yet all committed as of this note):

1. **Scoring** (`src/config.rs::TERM_COVERAGE_MAX_BONUS_FRACTION`,
   `src/retrieval.rs`): the boost is now a bounded *additive* bonus capped
   to a small fraction of the query's top fused score, and `SymbolKind::
   Module` (whole-file) symbols are excluded from receiving it at all —
   see the two new regression tests `term_coverage_boost_never_applies_to
   _module_symbols` and `term_coverage_bonus_cannot_overturn_a_leader
   _whose_margin_exceeds_it` in `src/retrieval.rs`.
2. **Eval** (`scripts/agent_eval/term_coverage_eval.py`): `is_coarse_symbol`
   excludes `kind == "module"` from symbol-level relevance/coverage
   credit; a new `gold_file_in_context`/`file_relevant_items` pair tracks
   file-level relevance as an explicitly separate signal, never folded
   into symbol-level `gold_in_context`.
3. **Harness reuse** (`src/embedding_cache.rs`, `examples/
   term_coverage_index.rs`, `scripts/agent_eval/contextbench_run.py::
   index_repo_cached`): commit-keyed worktrees (`ensure_repo_checkout`)
   fixed a real stale-checkout bug but lost cross-commit embedding reuse,
   making a full 21-task rerun impractically slow (one repo's pinned
   commits alone could cost 15-30min *each*). A separate, content-
   addressed embedding cache — keyed by `(embedding fingerprint,
   content_hash(embedded text))`, entirely disjoint from any commit's own
   `.oxide/index.db` — now lets unchanged content reuse its vector across
   commits without a second HTTP round trip, while symbol/structural-
   relation state stays fully commit-exclusive. Regression tests: 7 unit
   tests in `src/embedding_cache.rs` (wrong-checkout fails closed,
   identical content reuses across separate cache instances, changed
   content re-embeds, different fingerprints never share vectors, failed
   embeds never cached) plus one full `update_index`-level integration
   test (`tests/embedding_cache_reuse.rs`) proving no symbol/graph state
   leaks between two independently-indexed repos even as they share
   embeddings.

A repeatability probe (raw HTTP calls to the embedder, same text, same
process, several times) attributes the previously-reported same-task
jitter directly to the embedder's own prompt-cache/slot-reuse behavior: a
"cold" call and a "warm" (cache-hit) call for identical text return
numerically different vectors (max abs diff ~0.0044 in one probe), and
interleaving an unrelated query before a repeat measurably perturbs the
result too (cosine similarity 0.99986 vs. 1.0 on a clean repeat) — not
floating-point-precision noise, and not a bug in OXIDE's own ranking code
(no Rust `HashMap`-seed hypothesis needed). This explains why near-tied
rankings can flip between separate runs even with byte-identical index
state and byte-identical query text.

**Partial rerun evidence** (bounded scoring, alphas 0.0/0.05/0.1, 11 of 21
tasks — stopped by explicit instruction before completing, preserved at
`results/incomplete-run-3-bounded-scoring-stopped-at-11-of-21/`, see that
directory's `NOTE.md` for the honest read: valid data, incomplete coverage,
not final): 9/11 tasks completely flat, 2/11 (pylint) show real positive
MRR/nDCG movement at alpha=0.1 with zero regressions, and neither task that
regressed under the old formula (`...2e76c8cd`, `...9cca0774`) regresses
here. Reading as **SAFE-BUT-MARGINALLY-USEFUL**: the fix appears to work,
but the sample is too small and Python-skewed (no TypeScript, no pytest) to
call it validated, and the effect size at alpha≤0.1 is thin. **The full
21-task rerun must show a meaningfully larger, still-regression-free win
before any nonzero default is adopted** — this partial run is not that bar.

## Objective

Does rewarding lexical evidence that comes from multiple *distinct* query
terms/concepts (rather than repetition of one term) improve retrieval/context
precision enough to justify a nonzero default for
`$OXIDE_TERM_COVERAGE_ALPHA`? See `src/retrieval.rs` for the hook itself
(`1 + alpha * coverage` multiplicative reweight of the fused RRF score,
`coverage = matched_idf / total_idf` from BM25 evidence `LexicalIndex::search`
already computes — no new postings traversal, no new data structures).

## Setup

- 21 pinned ContextBench Tier A tasks (`eval-agent/results/tier_a_instances.txt`):
  17 Python + 4 TypeScript, across 7 distinct repos (code-server, darkreader,
  seaborn, flask, requests, pylint, pytest).
- Frozen `main` at commit `29d03b7` (post scoped-indexing, post batched-embedding-writes,
  post `oxide watch`) — no retrieval-weight, allocator, embedding, graph, watcher, or
  model changes were made during this run.
- Qwen3-Embedding-0.6B-Q8_0 via the local llama.cpp server (`scripts/embedder.sh`),
  `RetrievalMode::Balanced`, `SearchMode::Hybrid`, alpha grid `[0.0, 0.1, 0.2, 0.3, 0.5]`.
- Harness: `examples/term_coverage_sweep.rs` (one process per task, one
  `RetrievalEngine` reused across all 5 alphas, `CachingEmbedder` memoizing
  `embed_query` — see commit `87a00d0`), driven by
  `scripts/agent_eval/term_coverage_eval.py`, summarized by
  `scripts/agent_eval/summarize_term_coverage.py`.
- Raw results: `docs/term-coverage-eval/results/results.jsonl` (105 rows = 21
  tasks × 5 alphas, one continuous run, no resume). Two earlier interrupted
  attempts are preserved under `results/incomplete-run-1-stopped-at-12-of-21/`
  and `results/incomplete-run-2-stopped-at-11-of-21/` — both explicitly **not
  evidence**, per their own `NOTE.md`.
- A small-subset check (django, one query, alphas 0.0/0.3) confirmed the
  provenance gate and output shape before committing to the full run.

## Headline finding: the coverage boost is confounded by symbol size, and it changes the verdict

Every parsed file gets a whole-file `:__module__` symbol (per `AGENTS.md`'s
own note on the parser's per-file module symbol). Its lexical "document" is
the entire file's text, so `coverage = matched_idf / total_idf` is close to
1.0 for it almost by construction — a 3000-line file's module symbol will
contain most query terms somewhere in the file, regardless of whether the
file is actually the right answer. As alpha rises, these whole-file symbols
get pulled into the ranked results and the context pack far more often than
their real relevance justifies, and — because the eval's own relevance test
(`overlaps_gold`, a line-range overlap) trivially credits *any* symbol whose
span contains the gold lines — a whole-file symbol automatically counts as
"relevant" whenever gold exists anywhere in that file at all.

This was checked directly against the recorded (not re-run) 105-row dataset:

- **Both `gold_in_context` improvements in the whole 21-task run are 100%
  driven by a `:__module__` id entering the pack** — `...1409977d` (alpha
  0.2/0.3/0.5, `pylint/checkers/variables.py:__module__`, a 3326-line file)
  and `SWE-PolyBench...41cd3842` (alpha 0.1+, `src/node/app.ts:__module__`,
  a 134-line file). Neither flip involves a function/class-level symbol
  actually landing on the gold code. This means the mean-table's headline
  "gold-in-context 71.4%→81.0%" number is not evidence of better retrieval —
  it's fully explained by whole-file symbols getting free credit.
- **One of the four precision@5 win/loss events is the same artifact**:
  `...1409977d` at alpha=0.5 (P@5 0.0→0.2) is driven entirely by that same
  module symbol.
- Two win/loss events are real, uncontaminated function/class-level
  reranking with no module symbol in the pack: `...88e1ffd3` (win at
  alpha=0.1: `PreparedRequest.prepare_content_length` → `prepare_body`) and
  `...23963510` (win at alpha≥0.3, `PreparedRequest.prepare_url`).
- The two P@5 regressions (`...2e76c8cd` fully, `...9cca0774` partially) are
  **not** module-symbol artifacts — they are genuine cases of the coverage
  boost displacing a real, narrow, correct symbol (see failure attribution
  below).

Net: of the two headline "improvement" axes in the summary table, the
`gold_in_context` axis is 100% artifact at every alpha it moves. The
precision@5 axis is mostly real but has one artifact event mixed in
(`...1409977d` at alpha=0.5). Critically, **the size bias is alpha-dependent,
not uniform** — see the module-vs-non-module count in Section 1: it's mild
at alpha=0.1 (module hits +1, non-module +2 — most of that alpha's gain is
real) and dominant by alpha=0.5 (module hits +4, non-module +1). Read
per-alpha, not as one pooled tally, in Section 5.

## 1. Baseline vs. treatment (raw means — read with the caveat above)

Mean over 21 tasks (`summarize_term_coverage.py`), **not adjusted for the
module-symbol confound**:

| alpha | P@5 | R@5 | MRR | nDCG@10 | relevant items/task | tokens/task | gold-in-context |
|---|---|---|---|---|---|---|---|
| 0.0 (baseline) | 0.3143 | 0.5113 | 0.5124 | 0.5651 | 1.14 | 1804 | 71.4% |
| 0.1 | 0.3238 | 0.5351 | 0.5433 | 0.5887 | 1.29 | 1807 | 76.2% |
| 0.2 | 0.3048 | 0.4875 | 0.5759 | 0.5975 | 1.33 | 1816 | 81.0% |
| 0.3 | 0.3143 | 0.4875 | 0.5759 | 0.5968 | 1.33 | 1806 | 81.0% |
| 0.5 | 0.3143 | 0.5129 | 0.5746 | 0.5946 | 1.38 | 1896 | 81.0% |

Count of `:__module__` symbols vs. function/class symbols appearing in
`relevant_ids` (i.e. counted as gold-overlapping context) across all 21
tasks, by alpha — the mechanism made visible in aggregate:

| alpha | module-symbol "relevant" hits | function/class "relevant" hits |
|---|---|---|
| 0.0 | 1 | 23 |
| 0.1 | 2 | 25 |
| 0.2 | 3 | 25 |
| 0.3 | 3 | 25 |
| 0.5 | 5 | 24 |

The bias is alpha-dependent: at alpha=0.1 module hits go +1 while non-module
hits go +2 (most of that alpha's gain is real, function/class-level
reranking); by alpha=0.2/0.3 module hits are +2 from baseline, and by
alpha=0.5 they're +4, while non-module hits barely move (+1 total across
the whole grid) — the bias is mild at low alpha and dominates at high alpha. The composite metrics above conflate both
regimes into one mean per alpha, which is not a clean read of precision
improvement at any single alpha without the per-event check below.

Per-alpha win/loss/tie vs. alpha=0.0, from `summarize_term_coverage.py`
(unadjusted; see per-event attribution above for which are real):

| alpha | P@5 wins/losses | gold-in-context wins/losses |
|---|---|---|
| 0.1 | 1 / 0 | 1 / 0 (**artifact**) |
| 0.2 | 0 / 1 | 2 / 0 (**both artifact**) |
| 0.3 | 1 / 1 | 2 / 0 (**both artifact**) |
| 0.5 | 2 / 2 | 2 / 0 (**both artifact**) |

4 of 21 tasks (`...10750f29`, `...da598baa`,
`Multi-SWE-Bench...2fb50735`, `Multi-SWE-Bench...8d780f70`) are all-zero at
every alpha (gold never retrieved at all, unaffected by alpha). Of the
remaining 17, 15 move at least one ranked metric at *some* alpha, but most
of that is small nDCG@10/MRR drift (±0.01–0.05) from reordering among
already-near-tied candidates, with no P@5/recall@5 change. Four additional
tasks not otherwise discussed in this document (`...51b4c299`, `...049a7048`,
`...0eecae1e`, `SWE-PolyBench...42165c4e`) show small nDCG@10 (and, for the
last, MRR) regressions only at alpha≥0.2 — none at alpha=0.1 — which is
more mild negative evidence against raising alpha past 0.1, on top of the
two real P@5 regressions already covered. The **P@5, recall@5, and
gold-in-context flips** — the events with a clear directional call, covered
individually above — occur in exactly 6 tasks: `...88e1ffd3`, `...23963510`,
`...1409977d`, `SWE-PolyBench...41cd3842`, `...2e76c8cd`, `...9cca0774`.

## 2. Task-level wins and regressions

**Real wins** (function/class-level reranking, no module-symbol involvement):

- `...88e1ffd3` (requests, `PreparedRequest.prepare_content_length` →
  `prepare_body`): P@5 0.2→0.4, recall@5 0.5→1.0, from alpha=0.1. Both
  symbols are gold-overlapping — the top-5 *hits* gain a second correct
  item (driving the ranked-metric win) while the *pack*'s single reported
  relevant item happens to change identity between the two, which is why
  `relevant_ids` shows a swap rather than an addition. The cleanest
  positive result in the run.
- `...23963510` (requests, `PreparedRequest.prepare_url`): P@5 0.4→0.6 at
  alpha=0.3 and 0.5, no module symbol in the pack at either alpha.
- `...4b7ae9d9` (pytest, `create_new_paste`): MRR 0.5→1.0, nDCG@10 +0.253
  from alpha=0.1 — the gold hit moves from rank 2 to rank 1; a module symbol
  (`src/_pytest/pastebin.py:__module__`) is present in the pack at *every*
  alpha including baseline, unchanged rank, so it does not explain this
  particular delta.
- Three more tasks show real, non-module nDCG@10 gains at alpha=0.1 with
  `relevant_ids` either unchanged or gaining a genuine function/class symbol:
  `...1fdd9275` (+0.080, gains the `PreparedRequest` class symbol),
  `...1397ea97` (+0.055, `relevant_ids` unchanged — a real rank reorder among
  already-correct hits), `...88e1ffd3` (+0.067, alongside its P@5 win above).

**Artifact "wins"** (do not represent real precision gains):

- `...1409977d` (pylint): `gold_in_context` False→True at alpha=0.2+ and
  recall@5 gain at alpha=0.5 are both solely `pylint/checkers/variables.py
  :__module__` (a 3326-line file) trivially overlapping wherever the gold
  lines happen to fall in that file.
- `SWE-PolyBench...41cd3842` (TypeScript): `gold_in_context` False→True from
  alpha=0.1 is solely `src/node/app.ts:__module__` (134 lines — smaller
  file, so "the whole file" is a less egregious answer than the pylint case,
  but it's still not a function-level match).

**Regressions** (real, not module-symbol artifacts):

- `...2e76c8cd` (flask, "Require a non-empty name for Blueprints"): the gold
  symbol `Blueprint` drops out of the top 5 at alpha≥0.2 (P@5 0.2→0.0,
  recall@5 1.0→0.0). See failure attribution below — the concerning one.
- `...9cca0774` (requests, `Session.request`): unchanged through alpha=0.3;
  at alpha=0.5, P@5 drops 0.2→0.0 (the ranked-hit metric regresses — gold
  fell out of the top-5 *search* results) even though the context *pack*
  still contains `Session.request` (via structural expansion, independent
  of the raw ranked cutoff) alongside a newly-added
  `requests/utils.py:__module__` entry.

## 3. Failure attribution

Two independent, compounding mechanisms are visible in this data, not one:

**(a) Identifier dominance** (the risk this experiment was designed to
check for, and did find): `2e76c8cd`'s query is almost entirely one exact
class name — "Require a non-empty name for **Blueprints** ... if a
**Blueprint** is given an empty name ... a `ValueError` was raised." The
gold symbol wins on BM25/cosine alone at alpha=0.0. At alpha≥0.2, other
candidates that incidentally match more of "empty", "name", "ValueError",
"raised" pick up enough coverage bonus to outrank it, even though none of
them is the actual answer. `9cca0774` is the same mechanism at lower
intensity, surviving until alpha=0.5. This is exactly the failure mode the
experiment's own brief called out up front — corroboration "must not
overpower clearly stronger retrieval evidence" — and here it does.

**(b) Symbol-size bias** (found during this session, not anticipated in the
brief): `coverage` is computed from a symbol's BM25 lexical-document
evidence, and a whole-file module symbol's document is the entire file —
confirmed directly against the index for three of the files involved
(`pylint/checkers/variables.py:__module__` spans lines 1–3326,
`requests/utils.py:__module__` spans 1–1048, `src/node/app.ts:__module__`
spans 1–134: by construction, every module symbol's span is the whole
file). Longer documents contain more distinct query terms almost by
definition, so `coverage` is structurally biased toward large symbols
regardless of actual relevance. This compounds with the eval harness's own
`overlaps_gold` line-range test, which gives a whole-file symbol free credit
for "containing" gold whenever gold exists anywhere in that file. The two
effects reinforce each other: the same property (being large) that inflates
a symbol's coverage score also inflates its apparent correctness under the
eval's own relevance definition.

Both real regressions trace to (a); both artifact wins trace to (b), per
the per-event attribution in Section 1 (recorded 105-row dataset, not
re-derived). The bias in (b) is alpha-dependent — mild at alpha=0.1, where
it produces only the one `gold_in_context` flip and no ranked-metric
contamination; dominant by alpha=0.5, where module hits roughly quintuple
from baseline. This means the win/loss tallies at low alpha are mostly
trustworthy in this sample, but the mechanism has no principled cap, so a
larger or different task sample could push more contamination into the
low-alpha regime too — see the recommendation below.

## 4. Overhead

**CPU/latency** (`examples/term_coverage_overhead.rs`, in-process, two
sequential arms — first alpha=0.0 with its own warm-up loop, then alpha=0.3
with its own — 1600 samples/arm on Django's 35k-symbol index):

```
alpha=0.0  mean=63.94ms  p50=59.78ms  p95=76.51ms  p99=150.53ms
alpha=0.3  mean=64.00ms  p50=58.45ms  p95=74.26ms  p99=321.03ms
mean delta: 0.10%
```

Mean and p50/p95 are within noise. The p99 gap (150ms vs. 321ms) is
**unresolved, not dismissed** — the two arms ran sequentially (not
interleaved), so ordering effects on a single tail percentile can't be
ruled out from this run alone; it does not affect the mean/p50/p95 read,
and is not concerning enough to block on for a feature that's default-off,
but a future overhead check should interleave the two arms per query to
close this out cleanly.

**The in-sweep `search_seconds` numbers in `results.jsonl` are not usable
for an overhead comparison at all** — alpha=0.0 always runs first in each
task's 5-alpha sweep and absorbs the engine's one-time lexical-index/
vector-cache warm-up (`p50_s`=0.508, `p95_s`=2.691 vs. ~0.007–0.009s for
every later alpha in the same process). That's a measurement-order artifact
of the harness, not a per-alpha cost; the microbenchmark above isolates it
correctly.

**Embedding/memory:** every task logged exactly 1 real `embed_query` HTTP
call across all 5 alphas (`CachingEmbedder` collapsing the redundant calls
as designed) — the corroboration hook itself never touches the embedder.
Peak RSS for the whole overhead-benchmark process (one in-memory index +
both alpha arms) was 353 MB, consistent with "no new model memory": that
figure is the django index itself, not a per-alpha allocation.

## 5. Recommendation: REVISE — do not adopt a nonzero default yet

Read per-alpha, not as one pooled tally — the two regimes are different:

- **alpha=0.1, taken alone, looks clean**: zero regressions on any ranked
  metric across all 21 tasks, one real P@5 win (`...88e1ffd3`), three more
  real nDCG@10 gains from genuine reranking (`...4b7ae9d9`, `...1fdd9275`,
  `...1397ea97`), and only one artifact (`gold_in_context` flip on
  `...41cd3842`, which doesn't affect any ranked metric).
- **But the mechanism behind it has no principled limit.** Section 3
  established that `coverage` is structurally biased toward large/whole-file
  symbols, and that bias is mild at alpha=0.1 only because 0.1 is a small
  multiplier — not because the formula corrects for symbol size. The same
  formula is already dominant at alpha=0.5 in this sample. Shipping a
  default on a formula with a known, unnormalized confound — even at a
  setting where this particular 21-task sample happens not to trigger it
  much — is exactly the kind of thing that looks fine until a different task
  mix (more large files, more coarse-grained gold) tips it. That, not a
  regression count, is why this isn't a KEEP.
- **The regressions that do exist are real and specific**: identifier
  dominance (`...2e76c8cd` at alpha≥0.2, `...9cca0774` at alpha=0.5) is
  exactly the risk this experiment was designed to check for, and it's
  confirmed.
- Before re-running this experiment with a verdict that could change a
  default: (1) exclude whole-file/module symbols from `coverage` eligibility
  or normalize `coverage` by document length so large symbols stop winning
  by virtue of size, and (2) tighten the eval's `overlaps_gold` so a
  multi-thousand-line symbol can't claim credit for "containing" gold lines
  it doesn't meaningfully retrieve. Both are eval/hook-detail fixes, not new
  architecture, and are scoped follow-up work, not done in this session.
  Re-measure the low-alpha region specifically after the fix, since that's
  where the real signal in this run actually lives.
- `$OXIDE_TERM_COVERAGE_ALPHA` stays env-gated and defaults to `0.0` (no-op)
  — unaffected by this document either way.

This closes the term-coverage corroboration experiment. No further
experiments follow from this session per the resumption brief.
