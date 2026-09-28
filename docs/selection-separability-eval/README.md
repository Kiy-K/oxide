# Selection-seam separability screen (issue #32)

Status: **research complete. Disposition: B, NO TRANSFERABLE SIGNAL.**
Production is unchanged: no ranking, RRF, allocator, embedding, structural
expansion, schema or CLI/MCP/JSON change. No model or LLM judge was called.
Every number is **measured** by the scripts in `scripts/` unless marked
*inferred*. Full tables are in `results/tables.md`; raw results in
`results/results.json`.

**Scope of the verdict.**

- It covers the tested candidate universe (fused ranks 1–50 ∪ one-hop
  neighbors of the top-5 seeds) and the held-out set.
- ContextBench was **not evaluated** (§11).
- It shows that the current request-time features carry no transferable
  signal at this seam. It does **not** prove that no future selector could
  work, for example one built on a new representation or new evidence.

## 0. Answer

At the selection seam, OXIDE's current fused order has a held-out
within-task AUROC of **0.848**.

- No single request-time feature beats it.
- No preregistered feature family passes the gate.
- The best all-feature model gains **+0.013 [−0.014, +0.039]**. Removing the
  module/test label artifact turns that negative: **−0.059**.
- Structural features fail to transfer and hurt held-out.
- No query class with an adequate sample shows reliable lift.
- **No candidate passes the preregistered +0.05 gate.**

The selection seam has large oracle headroom (taxonomy §7–10), but the current
request-time features do not expose a transferable signal to exploit it.

## 1. Baseline and workspace

- `origin/main` = `4091adf2ad0cab1d4b42a96a92fa414f2da73744` (`src/` identical to `5f9309d`, the taxonomy baseline; `git diff 5f9309d 4091adf -- src Cargo.*` is empty).
- The work ran in a Claude Code cloud session, in a scratch workspace outside
  the checkout, and was then copied here. Every result was reproduced
  byte-identically from this directory (`results/run.log`).

## 2. Frozen input and provenance

- **The taxonomy traces themselves could not be regenerated.**
  - They are machine-local (71 MB); only `traces.sha256` is committed.
  - Regenerating them needs clones of the 10 third-party corpora, and the
    cloud session's permission policy blocked that cloning.
  - `traces.sha256` could therefore not be checked.
- Instead the study uses the **committed production dumps the traces were
  verified against** (`docs/ranking-fusion-eval/results/dump-*.jsonl.gz`):
  lexical top-200 + BM25 scores, arctic semantic top-200 + cosine, the full
  production fused list + scores, the top-5 fused seeds' `neighbors()` lists,
  symbol spans.
- Provenance:
  - sha256 + git blob of every input: `results/provenance-files.txt`.
  - taxonomy `parity.txt`: 226/226 trace instances reproduce these dumps
    (lexical keys/scores, full fused order, pack, omissions).
  - Independent cross-check (`scripts/provenance_crosscheck.py`): every
    fused / lexical / semantic rank recorded in the taxonomy's committed
    `rows.json.gz` (463 lost-unit keys × 3 channels = **1,389 checks**,
    226/226 tasks) equals the rank in the dumps. **0 mismatches**
    (`results/provenance-crosscheck.txt`).
  - Scope gap: parity and the cross-check cover the lexical and fused lists,
    and the semantic ranks of lost units.
  - **The dumped structural neighbor lists were not independently
    hash-verified.** They are production `neighbors()` output as dumped by
    `fusion_dump`, and no parity check covers them.
  - The dumps' seed lists equal the fused top-5 on 226/226 tasks.
- **ContextBench gold was unavailable.**
  - `cb-tasks.jsonl` has empty gold.
  - The taxonomy read the gold from the ContextBench parquet on GitHub, which
    was part of the blocked clone batch.
  - CB is therefore **not evaluated** (§11).

## 3. Preregistration

`PROTOCOL.md`, sha256 **`f6453d321f88c79d776f2dcb09cba86456ef4eba6b11f37f1bd05eb46de2e448`**,
frozen 2026-09-28T04:26:07Z (read-only). It was written before any feature,
control or model metric was computed on any set. Before freezing, only data
shape was inspected (gold counts in fused / top-50 / neighbor lists), and this
is disclosed in PROTOCOL §0. Fit/selection artifacts: `results/frozen_models.json`,
sha256 `dc6058794f63f5c09ca486cf0d1347467f5bd65edfffa48b6d13b34485fbec52`.
They were produced by `fit.py`, which reads only dev and masked. `evaluate.py`
refuses to run if that file's hash has changed.

## 4. Exact feature list (37, PROTOCOL §5)

- **R retrieval (13):** fused_rank, fused_score, lex_rank, lex_score, sem_rank, sem_score, in_lex, in_sem, in_both, rrf_lex, rrf_sem, rank_disagree, lex_minus_sem.
- **S structural (12):** rel_distance, n_seed_links, best_seed_rank, rel_{uses, imported_definition, parent, child, sibling, test}, seed_fanout, rel_weight, link_top1.
- **I identity/path (8):** is_module, is_test (production predicate), is_nested, path_depth, name_in_query, file_stem_in_query, same_file_top1, same_file_top5.
- **C cost/role (4):** span_lines, span_missing, in_seed_cut, neighbor_only.
- **Not measured:** candidate-side bounded local degree, ast-grep caller flags,
  and snippet-window tokens. They are absent from the frozen records and were
  excluded rather than imputed.
- **Deviations from PROTOCOL §5**, disclosed after review and all label-free:
  - `is_nested` and `name_in_query` are 0 for module symbols;
  - `lexical_only` uses +1e-3·sem_rank as a tie-break, not strictly
    lexicographic at lr 200 vs 201 (negligible);
  - `best_single` was a 3-way tie at 0.924 (lex_rank, lex_score, rrf_lex; all
    rank-equivalent), broken by the largest name. That tie-break was not in the
    protocol, but all three give the same heldout result.
- **`span_missing` / imputed `span_lines` are dump artifacts.** The dumps hold
  spans only for fused-list keys, so `span_missing` is equivalent to "not in
  the fused list". Production has real spans for every symbol. LR-C and LR-ALL
  use them, so they would also fail gate condition 4 (request-time fidelity).
  Moot, since both fail the AUROC margin.
- **Universe:** fused ranks 1–50 ∪ every neighbor of the top-5 seeds.
  Admitted evidence and the pre-allocation pool are not in the dumps. The
  final pack is a future-stage decision and is not used.

## 5. Eligible tasks (≥ 1 positive, ≥ 1 negative, ≥ 5 candidates)

| set | tasks | eligible | positives | negatives |
|---|---:|---:|---:|---:|
| dev (fit) | 70 | 60 | 95 | 6,778 |
| masked (fit) | 70 | 44 | 65 | 5,070 |
| heldout (gate) | 65 | 61 | 133 | 5,775 |
| ContextBench (gate) | 21 | **0: gold unavailable** | — | — |

## 6. Controls (macro within-task AUROC [95 % CI], task bootstrap)

| scorer | dev | masked | heldout |
|---|---|---|---|
| random (200 draws) | 0.501 | 0.500 | 0.502 |
| **fused order (current)** | 0.921 | 0.868 | **0.848 [0.794, 0.897]** |
| pool order (0.4 × seed for neighbors) | 0.921 | 0.864 | 0.844 |
| lexical only | 0.944 | 0.904 | 0.824 |
| semantic only | 0.843 | 0.766 | 0.791 |
| RRF baseline (two terms) | 0.921 | 0.868 | 0.848 |
| relation-only (weight / links / seed rank) | 0.36 / 0.37 / 0.39 | 0.31 / 0.31 / 0.34 | 0.43 / 0.44 / 0.45 |

- Heldout AUPRC: fused 0.392 vs a random baseline of 0.024.
- Heldout pairwise ordering accuracy: fused 0.872.
- The relation-only controls are **below chance**. Most one-hop neighbors are
  non-gold. Being a neighbor is a negative signal relative to being a direct
  hit.

## 7. Single features (oriented on dev+masked)

- No single feature beats fused order on heldout. The best non-fused feature
  is lexical rank/score: 0.821, Δ −0.028 [−0.058, +0.001].
- Every structural feature is 0.49–0.65 alone.
- The dev-selected best single feature is `rrf_lex`, rank-equivalent to
  lexical rank. It wins on dev (0.944) and masked (0.904) but loses on heldout.
  This is consistent with ranking-fusion's reading that lexical dominance on
  commit-message sets is identifier leakage *(inferred)*.

## 8. Feature-family ablations and combined models (heldout Δ vs fused order)

| scorer | dev* | masked* | heldout | Δ AUROC [CI] | Δ pairwise |
|---|---:|---:|---:|---|---|
| LR-R (retrieval) | 0.948 | 0.904 | 0.834 | −0.015 [−0.036, +0.006] | −0.025 |
| LR-S (structural) | 0.816 | 0.773 | 0.723 | −0.125 [−0.168, −0.086] | −0.102 |
| LR-I (identity/path) | 0.879 | 0.756 | 0.776 | −0.072 [−0.127, −0.019] | −0.067 |
| LR-C (cost/role) | 0.893 | 0.846 | 0.816 | −0.033 [−0.056, −0.012] | −0.024 |
| LR-RS (retrieval+structural) | 0.956 | 0.927 | 0.806 | −0.042 [−0.074, −0.014] | −0.045 |
| **LR-ALL** | 0.978 | 0.956 | **0.862** | **+0.013 [−0.014, +0.039]** | +0.003 |
| best fixed (FX2 lexical-first) | 0.944 | 0.904 | 0.824 | −0.024 [−0.054, +0.005] | −0.027 |
| FX1 best-channel RR / FX3 agreement / FX4 structural vote | | | 0.841 / 0.849 / 0.761 | −0.007 / +0.000 / −0.087 | |

\* in-sample (fitted or selected there).

- **Best single feature:** `rrf_lex`, heldout 0.821.
- **Best fixed combination:** FX3 agreement-first on heldout (0.849, +0.000).
  The dev-selected one was FX2 (0.824).
- **Best fitted model:** LR-ALL (0.862, +0.013, CI includes 0).
- LR-ALL's two largest weights are is_module (−3.15) and is_test (−3.09).
  Commit-derived gold excludes modules and test files by construction.
  On the artifact-free universe LR-ALL's heldout Δ is **−0.059
  [−0.094, −0.027]**, so its small full-universe edge comes from that artifact.

## 9. Gate (PROTOCOL §8): heldout

C* = fused order (0.848 vs pool 0.844).

| candidate | Δ AUROC [CI] | margin ≥ 0.05, CI > 0 | pairwise ≥ −0.01 | artifact-free | result |
|---|---|:-:|:-:|:-:|:-:|
| LR-R | −0.015 [−0.036, +0.006] | n | n | n | fail |
| LR-S | −0.125 [−0.168, −0.086] | n | n | n | fail |
| LR-I | −0.072 [−0.127, −0.019] | n | n | n | fail |
| LR-C | −0.033 [−0.056, −0.012] | n | n | n | fail |
| LR-RS | −0.042 [−0.074, −0.014] | n | n | n | fail |
| LR-ALL | +0.013 [−0.014, +0.039] | n | y | n (−0.059) | fail |
| best_single (rrf_lex) | −0.028 [−0.058, +0.001] | n | n | n | fail |
| best_fixed (FX2) | −0.024 [−0.054, +0.005] | n | n | n | fail |

**0 of 8 pass heldout.** Every candidate's CI upper bound is below +0.05, so a
+0.05 gain is excluded, not merely undetected. Gate condition 4 (request-time
availability) was checked by hand, not in code, and is moot. Sensitivity
checks agree:

- dropping the duplicate `httpx-88a81c5d` gives best +0.013 [−0.015, +0.040];
- on the artifact-free universe the best is LR-R, −0.007.

## 10. Held-out result

- Nothing passes, and no candidate even reaches half the margin.
- The only positive point estimate (LR-ALL, +0.013) is not significant and is
  explained by the module/test label artifact.

## 11. ContextBench result

**Not evaluated: no gold labels in this environment** (see §2).

- `features.py` accepts `CB_PARQUET=<ContextBench data/full.parquet>` and
  `evaluate.py` then applies the frozen gate unchanged.
- Under the preregistered rule (§9 of PROTOCOL), CB can only *restrict* a
  candidate, never rescue one. With 0/8 passing heldout, CB cannot change the
  verdict.
- The CB gold that is in the repo cannot implement the §4 line-level labels
  without bias: `reranker-eval/results/ceiling.jsonl` is file-level only, and
  the taxonomy's `rows.json.gz` lists lost units only. Neither was used.
- CB labeling would also be partial. Neighbor-only CB candidates have no span
  in the dumps and would be dropped (PROTOCOL §4).

## 12. Rank bands (heldout, macro AUROC within band)

| band | eligible | pos/neg | fused | lexical | semantic | LR-R | LR-RS | LR-ALL |
|---|---:|---|---:|---:|---:|---:|---:|---:|
| B1 fused 1–5 | 36 | 57/123 | 0.708 | 0.644 | 0.674 | 0.690 | 0.699 | 0.831 |
| B2 6–16 | 27 | 40/257 | **0.529** | 0.580 | 0.474 | 0.599 | 0.527 | 0.768 |
| B3 17–50 | 23 | 28/754 | 0.621 | 0.554 | 0.563 | 0.602 | 0.548 | 0.736 |
| B4 neighbor-only | 5 (n<10) | 8/234 | 0.622 | | | 0.270 | 0.410 | 0.450 |

- **Inside the seed band (6–16), fused order is near chance (0.53).** The
  headroom the taxonomy located there is real: the current order does not sort
  it.
- **Full-universe in-band Δ vs fused order** (descriptive, from preregistered
  §10):
  - LR-ALL: B1 **+0.123 [+0.025, +0.231]**, B2 **+0.239 [+0.127, +0.358]**,
    B3 +0.115 [−0.033, +0.258];
  - LR-RS: B1 −0.009, B2 −0.002, B3 −0.073, all with CIs spanning 0.
- **These in-band LR-ALL gains are not shown to survive removing modules and
  test files.** Commit gold excludes both by construction, and they are
  LR-ALL's two largest weights. The check is `diag_bands_artifact_free.py`,
  which is **not preregistered** and explanatory only.
  - B2: +0.060 [−0.098, +0.219] (n = 21);
  - B3: −0.046 [−0.212, +0.110] (n = 23);
  - B1: n = 8, below the n ≥ 10 rule, not interpreted.
- **Hypothesis only.** One post-hoc cell stays positive: retrieval-only LR
  (LR-R) within fused ranks 6–16, held-out, modules and tests removed:
  **+0.169 [+0.019, +0.318], n = 21**.
  - It was **not preregistered**.
  - It was found among many diagnostic cells (about 18 cells × 4 scorers).
  - It was **not tested on ContextBench**.
  - Similar patterns have failed out of sample before: RRF K=10, lexical
    weight ≥ 0.7, and the taxonomy §13 seed-band pattern.
  - **It does not justify implementation.**

## 13. Query classes (heldout; full universe; n ≥ 10 only)

| class | n | fused | LR-R | LR-RS | LR-ALL | Δ LR-ALL |
|---|---:|---:|---:|---:|---:|---|
| NL behavioral description | 26 | 0.787 | 0.803 | 0.759 | 0.820 | +0.034 [−0.012, +0.084] |
| exact identifier/symbol | 12 | 0.925 | 0.908 | 0.903 | 0.927 | +0.002 [−0.019, +0.026] |
| mixed/other | 16 | 0.848 | 0.791 | 0.795 | 0.864 | +0.016 [−0.030, +0.060] |

- Heldout test discovery (n = 1), quoted literal / error text (n = 3) and
  filename/path (n = 3) are too small to show. Callers/impact has no heldout
  tasks. Implementation discovery (NL) has none on heldout and one on masked.
- No class shows a significant transferable gain.

## 14. Structural contribution (heldout Δ macro AUROC [CI])

| added to LR-R | dev | masked | heldout |
|---|---|---|---|
| all structural (LR-RS − LR-R) | +0.008 | +0.023 | **−0.028 [−0.055, −0.005]** |
| relation type | +0.007 | +0.015 | −0.018 [−0.040, −0.001] |
| relation distance | +0.001 | +0.003 | +0.002 [−0.006, +0.009] |
| seed rank | +0.006 | +0.007 | +0.003 [−0.008, +0.015] |
| degree / fan-out / link count | +0.002 | −0.003 | −0.003 [−0.011, +0.006] |
| LR-S alone vs fused | −0.105 | −0.095 | −0.125 [−0.168, −0.086] |

**Relation information adds no transferable signal beyond fused order.**

- Its small in-sample gains on dev and masked turn into a significant loss on
  heldout.
- The structural gain appears only on one data family (commit-message dev and
  masked), so it counts as non-transferable.
- *(inferred)* This is consistent with ranking-fusion's finding that
  structural evidence is informative only when the seed is right, which is not
  observable at request time.

## 15. Runtime practicality (code reading; nothing benchmarked; corrected after review)

- **In memory inside `engine.search` today:**
  - both channel top-200 lists and scores (`src/retrieval/engine.rs:334–362`);
  - the fused scores and reasons.
- **Fused ranks 17–50 are cut before the context seam.** `search` truncates to
  `limit` = 16 seeds. A selector at the context stage would need them plumbed
  through (a small interface change) *(inferred)*.
- **Neighbor lists:** `src/context.rs:213–236` iterates `graph.neighbors` for
  the top-5 seeds, but stops once the expansion total (2) is reached, so usually
  only seed 1's list is built. Building all five is cheap *(inferred)*.
  ranking-fusion measured graph + neighbors at +0.6 ms warm.
- **Spans and names:** on `Symbol`, so real spans are available. The study's
  imputed spans are not a production quantity (§4).
- **`pool_order`** only approximates production. Production caps expansion at 2
  admissions and never re-scores seeds.
- Universe size: mean 98 candidates per task on heldout, 116–119 on dev/masked.
  The per-request arithmetic is negligible *(inferred)*.
- **Moot, since nothing passed.**

## 16. Verdict (preregistered rule)

**B. NO TRANSFERABLE SIGNAL.**

- Heldout was evaluable (61 eligible tasks) and all 8 gate candidates failed it.
- ContextBench was **not evaluated** (gold unavailable). Under the frozen rule
  it could only restrict, never rescue, a candidate.

## 17. Consequences and next direction

- **Stopped:** further reranker, allocator and typed-judge work on the
  current representation. Do not build another model judge at this seam.
  - This is scoped to what was tested: the fused 1–50 + top-5-neighbor
    universe and held-out evidence. 133 of 174 held-out gold keys lie in that
    universe.
- **Frozen:** retrieval/fusion (RRF K=60, 0.6/0.4, depth 200) and the allocator.
- **Next direction: upstream,** toward candidate and semantic representation,
  or new evidence. The open question is:

  > Can a different symbol representation improve semantic ranking of
  > already-relevant code, particularly for description-style queries and in
  > the fused 6–16 band, without degrading identifier/code lookup?

- A pointer, not a result: within held-out fused 6–16, the semantic channel's
  own ordering is **0.474 AUROC** (below chance) and fused order is 0.529
  (§12).
- **Do not assume comments, docstrings or docs are the answer.** #30
  (`docs/intent-evidence-eval/`) found no intent-specific gain over a
  comment-free code control.
- The semantic-quality checkpoint (`docs/semantic-quality-eval/CHECKPOINT.md`)
  found that no text variant (D1/D3/D5, bare query) and no small model
  (Granite, arctic-embed-s) moved description-query fused ranking.
- The next experiment must be scoped against both of those before anything
  is implemented or benchmarked.

## 18. Limitations

- **The taxonomy traces could not be regenerated**, because cloning the
  third-party corpora was blocked. Provenance was checked against the
  committed ranking-fusion dumps instead: 1,389 fused, lexical and semantic
  rank observations, **0 mismatches**.
- **The dumped structural neighbor lists were not independently
  hash-verified.**
- **ContextBench gold was unavailable, so CB was not evaluated.** Verdict B
  rests on the held-out failure. It is sufficient under the preregistered
  rule, because SIGNAL EXISTS required passing both sets.
- Commit-derived gold is incomplete and excludes modules and tests by
  construction. Handled a priori by the artifact-free condition.
- Candidate universe lacks admitted evidence callers and the pre-allocation pool.
- The coarse kind proxy (module / nested) stands in for `SymbolKind`.
- The heldout B4 cell is tiny (n = 5).
- Scripts were not hashed at freeze time. `evaluate.py` was edited after
  fitting, but before its first heldout run, and no run log exists for that
  first run.
- Since then, the whole pipeline was re-run twice, once in the scratch
  workspace and once from this directory after the scripts' paths were made
  repository-relative. Both runs reproduced `frozen_models.json`,
  `results.json`, `tables.md` and the diagnostic byte-identically
  (`results/run.log`).

## 19. Independent review

This experiment ran in Claude Code cloud, where Codex was unavailable. The
independent reviewer was therefore **a fresh Claude subagent**, launched after
all results existed, which audited the study read-only. It did not take part in
the design, tuning or result generation, and had no access to the researcher's
reasoning. It found **no BLOCKER and no MAJOR** issue.

- It recomputed heldout fused order 0.848, LR-ALL 0.862, Δ +0.013
  [−0.014, +0.039] and the artifact-free Δ −0.059 with its own pair counting,
  and rebuilt the universe and labels (0/65 mismatches).
- It confirmed there is no leakage: labels, pack and omissions are never read.
- It confirmed that fitting and selection use only dev/masked, and that the
  gate code implements PROTOCOL §8/§9 exactly.
- It found that B follows from the evidence.

Its MINOR and NOTE findings:

- `span_missing` / imputed spans are dump artifacts;
- §15 runtime claims were overstated (ranks 17–50 are truncated; the neighbor
  walk stops early; `pool_order` is approximate);
- §12 leaned on an n = 8 cell and omitted the full-band CIs;
- undisclosed definition deviations and the tie-break;
- crosscheck count (1,389, not 926) and a missing crosscheck script;
- parity scope does not cover the neighbor lists;
- no script hashes or run log;
- two unlabeled inferences;
- why the partial CB gold in the repo was unusable;
- the "stop" recommendation should be scoped.

All were fixed in reporting (§2, §4, §7, §9, §11, §12, §14, §15, §17, §18).
No analysis or decision rule was changed. The reviewer proposed no retuning.
The full findings table is in `REVIEW.md`.


## Files

- `PROTOCOL.md`, `PROTOCOL.sha256`: the frozen preregistration. Its sha256
  is unchanged since the freeze.
- `REVIEW.md`: the independent review findings and their resolutions.
- `scripts/`:
  - `features.py`: candidate rows from the committed dumps;
  - `common.py`: scorers and metrics;
  - `fit.py`: dev/masked only; writes `results/frozen_models.json`;
  - `evaluate.py`: the frozen gate;
  - `report.py`: `results/tables.md`;
  - `diag_bands_artifact_free.py`: the non-preregistered diagnostic;
  - `provenance_crosscheck.py`.
- `results/`:
  - `frozen_models.json` (+ `.sha256`), `results.json`, `tables.md`,
    `diag_bands_artifact_free.json`;
  - `provenance-files.txt`, `provenance-crosscheck.txt`;
  - `run.log`.
- The 2 MB per-candidate `rows.jsonl.gz` is not committed. `features.py`
  regenerates it deterministically; `run.log` records its content hash.

## Reproduce

```bash
cd docs/selection-separability-eval/scripts   # needs python3, numpy, scipy
export PYTHONDONTWRITEBYTECODE=1
python3 provenance_crosscheck.py              # 1389 checks, 0 mismatches
python3 features.py                           # results/rows.jsonl.gz (CB_PARQUET=<ContextBench full.parquet> to label CB)
python3 fit.py                                # reads dev + masked only; must reproduce frozen_models.json sha256 dc605879…
python3 evaluate.py && python3 report.py      # results.json, tables.md
python3 diag_bands_artifact_free.py           # NOT preregistered; explanatory only
```
