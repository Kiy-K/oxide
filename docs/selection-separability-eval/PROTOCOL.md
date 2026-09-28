# Issue #32 — selection-seam separability screen: PREREGISTERED PROTOCOL

Frozen before any feature, model or control metric was computed on any set.
Hash of this file is recorded in `PROTOCOL.sha256` immediately after writing.
Nothing below may change after held-out or ContextBench metrics are seen.

Baseline: `origin/main` = `4091adf2ad0cab1d4b42a96a92fa414f2da73744`
(`src/` identical to `5f9309d`, the taxonomy baseline).

## 0. What was looked at before freezing (disclosure)

- Taxonomy/ranking-fusion READMEs, issue #32, roadmap #9, the research patch.
- Data-shape checks only: per set, the number of gold keys present in the
  fused list / fused top-50 / top-5-seed neighbor lists (dev 115/106/93/2,
  masked 115/86/61/4, heldout 174/160/125/8, cb gold absent from the
  committed task file). No feature, score, AUROC or control was computed.
- Provenance cross-check (§1).

## 1. Frozen input and provenance

The 71 MB taxonomy traces are machine-local and not in the repository; they
cannot be regenerated here (cloning the third-party corpora was refused by
the session's permission policy). The study therefore uses the **committed
production dumps the taxonomy traces were verified against**:

- `docs/ranking-fusion-eval/results/dump-{plain,masked,heldout,contextbench}.jsonl.gz`
  (arctic-embed-xs-q semantic list, lexical top-200 with BM25 scores, full
  production fused list with scores, top-5 fused seeds' `RelationGraph::neighbors`
  lists, symbol spans, production pack).
- Task/gold files: `tasks.jsonl` (dev), `tasks-masked.jsonl`, `heldout-clean.jsonl`,
  `cb-tasks.jsonl` (same dir).
- Provenance evidence: git blob ids + sha256 of every input (`results/provenance-files.txt`);
  taxonomy `parity.txt` (226/226 instances of the traces reproduce these dumps'
  lexical keys/scores, full fused order, pack and omissions); and an
  independent cross-check here: every fused/lexical/semantic rank the taxonomy
  recorded in `rows.json.gz` (926 observations over all 226 tasks) equals the
  rank in these dumps (`results/provenance-crosscheck.txt`).
- Trace-file hashes in `traces.sha256` cannot be checked (no trace files).

## 2. Datasets and split discipline

| role | set | use |
|---|---|---|
| fit / select | dev (70), masked (70) | orientation of single features, fitting LR, choosing the "best single feature" and "best fixed combination" |
| gate | heldout (65) | evaluated once with frozen scorers |
| gate | ContextBench-21 (cb) | evaluated once with frozen scorers, **if gold is obtainable** (§9) |

Held-out and cb are never used for fitting, orientation, selection, feature
design or thresholds. All held-out/cb numbers are produced by one script run
after the fit/selection artifacts are written and hashed
(`results/frozen_models.json`).

## 3. Candidate universe (per task)

U = fused ranks 1–50 ∪ N5, where N5 = every key in the dumped neighbor lists
of the top-5 fused seeds. (The dumps' seeds equal the fused top-5 on 226/226.)
Admitted evidence / the pre-allocation pool is not in the dumps; the final
pack is a future-stage decision and is **not** used to build U or features.

Bands (by fused rank): B1 1–5, B2 6–16, B3 17–50, B4 neighbor-only
(in N5, not in fused 1–50; may have fused rank > 50 or none).

## 4. Labels (never used as features)

- dev / masked / heldout: positive iff candidate key ∈ task gold keys.
- cb: positive iff the candidate is a non-module symbol whose span overlaps
  ≥ 1 ContextBench gold line (taxonomy "bearer" rule, path-normalized as in
  `retrieval-failure-taxonomy/scripts/cbgold.py`). CB candidates without a
  known span (neighbor-only keys absent from the dump's `spans`) are
  unlabeled and dropped from the cb universe.

## 5. Features (exact list; all label-free and request-time)

Notation: lr = lexical rank (1..200, absent → 201), sr = semantic rank
(absent → 201), fr = fused rank (absent from fused list → len(fused)+1).
Top-5 seeds s1..s5 = fused ranks 1..5. A "link" is (seed rank j, relation r)
with the candidate in seed j's neighbor list (self-links ignored).

Retrieval family R:
- R1 `fused_rank` = −ln fr
- R2 `fused_score` = fused score / fused score of rank 1 (absent → 0)
- R3 `lex_rank` = −ln lr
- R4 `lex_score` = BM25 / task max BM25 (absent → 0)
- R5 `sem_rank` = −ln sr
- R6 `sem_score` = cosine (absent → min cosine of the task's semantic top-200)
- R7 `in_lex`, R8 `in_sem`, R9 `in_both` (0/1)
- R10 `rrf_lex` = 0.6/(60+lr) (absent → 0); R11 `rrf_sem` = 0.4/(60+sr) (absent → 0)
- R12 `rank_disagree` = |ln lr − ln sr|
- R13 `lex_minus_sem` = ln sr − ln lr

Structural family S (relative to top-5 seeds):
- S1 `rel_distance` = −d, d = 0 if fr ≤ 5, 1 if ≥ 1 link, else 2
- S2 `n_seed_links` = number of distinct seeds linking the candidate
- S3 `best_seed_rank` = −(min linking seed rank), none → −6
- S4–S9 `rel_uses`, `rel_imported_definition`, `rel_parent`, `rel_child`,
  `rel_sibling`, `rel_test` (0/1, any link of that type)
- S10 `seed_fanout` = −ln(1 + min neighbor-list length over linking seeds), none → −ln(1 + max fan-out in task + 1)
- S11 `rel_weight` = max link weight (uses/imported-definition 1.0,
  parent/child 0.5, sibling/test 0.25; ranking-fusion a-priori map), none → 0
- S12 `link_top1` = linked by seed 1 (0/1)
- Candidate-side bounded local degree: **not available** in the frozen
  records → not measured (stated, not imputed).

Identity/path family I:
- I1 `is_module` (key ends `:__module__`)
- I2 `is_test` (production `context::is_test_symbol` predicate on file + simple name)
- I3 `is_nested` (qualified name contains `.` or `::`)
- I4 `path_depth` = number of `/` in file path
- I5 `name_in_query` = simple name (last qualified component, len ≥ 3) occurs
  as a whole word in the query (case-insensitive)
- I6 `file_stem_in_query` = file basename without extension (len ≥ 3) occurs
  as a whole word in the query (case-insensitive)
- I7 `same_file_top1` = same file as fused #1 and not #1 itself
- I8 `same_file_top5` = same file as some top-5 seed other than itself

Cost/role family C:
- C1 `span_lines` = ln(1 + end − start + 1); unknown → task median, plus C2
- C2 `span_missing` (0/1)
- C3 `in_seed_cut` = fr ≤ 16 (production seed/primary-eligible)
- C4 `neighbor_only` = candidate in B4

Label-construction caveat (a priori): on the commit-derived sets gold
excludes modules and test files by construction (ranking-fusion §7.1), so
I1/I2 (and anything that learns them) are inflated there. Handled by the
artifact-free gate condition in §8.

## 6. Scorers

Controls (not gate candidates):
- `random`: 200 seeded (seed 32+i) uniform scorings, averaged.
- `fused_order` = R1 (OXIDE's current fused order; absent tied last).
- `pool_order` = max(fused score, 0.4 × fused score of the best-scoring
  linking seed) (production context-expansion admission score).
- `lexical_only` = R3 (tie → R5); `semantic_only` = R5 (tie → R3).
- `rrf_baseline` = R10 + R11 (production RRF restricted to its two terms).
- relation-only controls: S11, S2, S3 alone.

Single features: each of the 37 features, oriented by the sign of
(dev+masked mean macro AUROC − 0.5); ties at 0.5 keep the listed sign.

Fixed monotonic combinations (no fitting):
- FX1 `best_channel_rr` = max(1/(60+lr), 1/(60+sr))
- FX2 `lexical_first` = R3 + 1e-3·R1
- FX3 `agreement_first` = 2·R9 + R2
- FX4 `struct_vote` = 1/(60+fr) + Σ_links 0.5·w(r)/(60+j)
  (production-style RRF vote from each linking seed, w from S11 map)

Fitted models: logistic regression, fitted **once** on pooled dev+masked
rows of eligible tasks; features z-scored with dev+masked mean/std;
balanced class weights; L2 with C = 1 (penalty ‖w‖²/2 added to the sum of
weighted log-losses, intercept unpenalized); L-BFGS; no hyperparameter
search. One model per family set:
LR-R, LR-S, LR-I, LR-C, LR-RS, LR-ALL.
Structural contribution fits (same recipe): LR-R+reltype (S4–S9, S11),
LR-R+distance (S1), LR-R+seedrank (S3, S12), LR-R+degree (S2, S10).
No tree model.

Selection on dev+masked only (metric = mean of dev and masked macro AUROC):
- `best_single` = best oriented single feature;
- `best_fixed` = best of FX1–FX4.

Gate candidates (8): LR-R, LR-S, LR-I, LR-C, LR-RS, LR-ALL, best_single, best_fixed.

## 7. Metrics

Per task, over its universe (or band):
- AUROC with ties = 0.5 (Mann–Whitney).
- AP (AUPRC): candidates ordered by score, ties broken by sha1(key) (label-free); random baseline = positive rate.
- Macro = unweighted mean over eligible tasks.
- Pairwise ordering accuracy = pooled over all within-task (positive,
  negative) pairs of eligible tasks, ties 0.5.

Minimum-positive rule: a task is eligible for a cell iff it has ≥ 1 positive,
≥ 1 negative and ≥ 5 candidates in that cell. A cell is interpreted only
with ≥ 10 eligible tasks; 5–9 shown as "n<10, not interpreted"; < 5 not shown.

Statistics: task-level bootstrap within dataset, 2000 resamples, seed 32,
percentile 95 % CI; Δ vs a control is paired (same resampled tasks).

## 8. Acceptance gate (fixed)

A gate candidate G passes a dataset D iff, with ≥ 10 eligible tasks:
1. Δ macro AUROC(G − C*) ≥ +0.05 and its 95 % CI lower bound > 0, where C* is
   whichever of `fused_order` / `pool_order` has the higher macro AUROC on D;
2. pairwise ordering accuracy Δ(G − C*) ≥ −0.01 (no regression);
3. on heldout only, (1) also holds on the artifact-free universe (U minus
   modules and test files), since commit gold excludes those by construction;
4. every feature G uses is in memory at request time (checked in code, §10).

## 9. Decision rule (fixed)

- **A. SIGNAL EXISTS** iff some G passes on heldout AND cb.
- **B. NO TRANSFERABLE SIGNAL** iff heldout is evaluable and every G fails on
  heldout, or both are evaluable and every G fails on at least one.
  (If cb is not evaluable but every G fails heldout, B still holds: cb can
  only restrict, never rescue, a candidate. The report must state cb was not
  evaluated.)
- **C. INSUFFICIENT EVIDENCE** otherwise (e.g. heldout < 10 eligible tasks,
  or some G passes heldout while cb is not evaluable).

If A: recommend exactly one next experiment (deterministic selector /
typed decision model / structural-selection policy) matching the passing
family. If B: stop reranker/allocator work on the current representation and
move upstream (representation / embeddings / new evidence).

## 10. Secondary (descriptive, not gating)

- Controls, single features, fixed combos, all LRs on all four sets.
- Family ablations (LR-R, S, I, C, RS, ALL).
- Rank bands B1–B4 (scores from the frozen scorers, evaluated within band).
- Query classes (taxonomy `analyze.classify`, query-only regex).
- Structural contribution: Δ(LR-RS − LR-R) and the four LR-R+subgroup fits vs LR-R, with CIs.
- Artifact-free universe on all sets; heldout without the later duplicate
  `httpx-88a81c5d`.
- Runtime practicality from code reading (where each feature lives at
  request time) and existing profile measurements; nothing is benchmarked.
