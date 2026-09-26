# Issue #15 — Laya evidence reranking: pre-registered protocol

Written **before** any Laya output was produced. The only numbers here are
the baseline measurements listed in §2, taken at the pinned commit.
Changes made after results were seen are listed in §9 with the reason for
each. Research only: no production code, defaults or dependencies change.

## 1. Pins

| item | value |
| --- | --- |
| OXIDE | `main` @ `5696358d6af01722b8004f21c008a38e2c5d51cc` (post-#14; #14 closed 2026-09-25) |
| retrieval config | frozen: RRF K=60, lexical 0.6 / semantic 0.4, 200 candidates/channel, exact vector scan, SQLite authoritative |
| embedder | `native:arctic-embed-xs-q` (shipped default) |
| Laya | PyPI `laya==0.3.20`, `torch 2.14.0+cpu`, `transformers 5.17.0`, Python 3.11, isolated venv at `~/.cache/oxide-laya-eval/venv` (outside the repo) |
| checkpoint | HF `convaiinnovations/laya` @ `55cf4c4ebb4ebe31b2550e8bdf3bd21b99753851` (Apache-2.0). `model.safetensors` sha256 `891102d3…8d86c` (English, ModernBERT-large, `max_len` 512 / `head_max_len` 192); `multilingual/model.safetensors` sha256 `9d628fd9…f204` (mmBERT-base, 1024 / 256) |
| runtime | in-process SDK (`laya.load(<local dir>)`), CPU only, `HF_HUB_OFFLINE=1`, fixed `torch.set_num_threads`, no HTTP server, no network at inference |
| machine | 16 logical CPUs, 15 GiB RAM, no GPU |

## 2. Post-#14 baseline, measured at the pin

- **Ranking identity.** `examples/fusion_dump` at the pin reproduces the
  frozen `docs/ranking-fusion-eval/results/dump-contextbench.jsonl.gz`
  byte-for-byte on all 21 ContextBench instances. That covers the lexical and
  semantic top-200 lists, the fused and expanded lists, the neighbors, spans,
  ids and the production pack.
- **CLI parity.** `oxide query --json` packs match those dump packs exactly
  (same item sets, same `used_tokens`). Only the CLI/MCP presentation sort
  order differs.
- **Fixture gate.** `oxide eval --config fixtures/benchmark.json`: hybrid
  R@5 0.909, P@5 0.200. This equals the fixture baseline in
  `ranking-fusion-eval` §3.3.
- **Cost.** One-shot `oxide query --json` on the 21 CB repos: 1 warm-up run,
  then 3 timed cold-process runs per task, with peak RSS read via `wait4`.
  Median of the per-task medians was **124 ms**, p90 233 ms, max 340 ms.
  Peak RSS: median **83 MB**, max 112 MB. Raw data is in
  `results/baseline/timing-oxide-query.json`.
- **Not re-derivable.** The dev (70) and held-out (65) dumps come from
  `ranking-fusion-eval`, and their indexes were deleted with that session's
  scratchpad. Their validity at the pin rests on two things: the CB
  byte-identity above and #14's `lean_snapshot_output_matches_the_complete_
  corpus_oracle`. That is a stated limitation, not a verified fact.

## 3. Sets and their roles

| set | n | role | why |
| --- | ---: | --- | --- |
| **dev** | 61 | selects the prompt, checkpoint and combination rule only | `ranking-fusion-eval` dev tasks for pylint/pytest/flask/requests. The cached clones' HEAD reproduces the dump's module spans in 5,671 of 5,676 cases, so snippets can be rebuilt without re-indexing. zod (9) is excluded because no clone is present. Both plain and masked regimes are used. **Leaky**: those indexes hold post-commit code, so a snippet can contain the fix itself. Acceptable for selection only. |
| **ContextBench** | 21 | **primary decision set**: candidate level and final pack | Human-labeled gold, repos checked out at their **base** commits, so there is no future-revision leakage. `kept` pools and packs were captured at the pin. |
| held-out | 65 | secondary; run **only if** CB passes | Commit-derived, post-commit code (leaky), and the repos are not all present. |

No gold labels, gold names, commit diffs or future revisions go into any
model input. The masked dev regime removes gold-identifier tokens from the
query, following the `ranking-fusion-eval` recipe.

## 4. Shortlist and reordering (offline; production untouched)

- **Shortlist.** The fixed top-N of the production candidate order, with
  N = 20:
  - **CB:** the `kept` pool after structural expansion, evidence collection
    and dedup/subsumption, before allocation. This is exactly what
    `rerank_candidates` would see, captured with `OXIDE_DEBUG_DUMP_KEPT`,
    and sorted as production sorts it (role, then score desc, then id).
  - **dev:** the fused top-20. It has no `kept` pool because no index
    exists.
- **Model input.** `state = {"task": <query>, "candidate": {"path",
  "symbol", "source"}}`.
  - `source` is the symbol body, windowed the way `render_snippet` does it
    (query-term centred, char budget = tokens × 4).
  - The query and source are each bounded so the whole state fits the
    checkpoint's state budget (`max_len − head_max_len`). Truncation is
    measured with the checkpoint's own tokenizer and reported.
- **Reorder.** Candidates are sorted by Laya P(relevant) descending, with
  ties broken by original rank. Only the order changes; scores are
  **transplanted rank-for-rank**. The i-th candidate in the new order
  receives the i-th largest original score among the N, and it keeps its
  own role, reasons and provenance.
  - Because of that, the relevance floor, the role caps and the per-file
    caps see the same score multiset, as RET-005 requires: never overwrite
    `Candidate.score` with a model score.
  - Nothing is removed, and candidates outside the top N never move.
  - On any Laya error, the whole task falls back to production order and
    the failure is counted.
- **Pack replay.** A Python replica of `context.rs`'s allocation takes the
  `kept` pool through five steps in order:
  1. role sort
  2. floor of 0.15 × the top seed score
  3. per-file cap 2
  4. primary cap
  5. test cap 1, then greedy fill with `render_snippet` shrink-to-fit
     within the 4,096-token budget

  It must reproduce **every** baseline pack's item set and `used_tokens`
  on all 21 CB tasks under the identity order. If it does not, the pack
  analysis is not run.

## 5. Prompt / configuration variants (selected on dev only; at most 8)

`{checkpoint} × {question} × {combination}`:

- checkpoint: `english` (512), `multilingual` (1024).
- question: one fixed instruction, reused verbatim from
  `ranking-fusion-eval/scripts/judge.py` ("Would a competent developer need
  to read or modify the code symbol …"), asked in one of two forms:
  - Q1: `noul` with neutral model-facing labels `A`/`B`, following the
    README's #156 label-following warning.
  - Q2: a two-option `choice` keyed `A` (relevant) / `B` (not relevant).

  The score is P(relevant): `noul` probability, or `choice` probability of
  `A`.
- combination:
  - C1: pure Laya order.
  - C2: an equal-weight RRF of the original rank and the Laya rank with
    K = 60. It is fixed, not tuned.

Selection criterion: the mean of plain and masked **nDCG@10 within the
top-20** on dev. One configuration is carried forward. The other seven are
reported as dev-only exploration.

## 6. Controls

1. Unchanged production order (identity).
2. Seeded random permutation of the top-20: mean over seeds 0–9. It shows
   how much any reshuffle moves each metric.
3. The frozen, already-rejected confidence-aware reranker
   (`ranking-fusion-eval/results/weights.json`). It is cited, not refit.
4. Agreement with the 309 existing Jev judgments (`judgments.jsonl`). Wire
   compatibility is not the same as equivalent output, and this measures
   the difference. It is reported only; it is never used as a label for
   selection.
5. Laya sanity controls before any quality run:
   - repeat determinism (bit-identical scores on a repeat call);
   - a clearly relevant and a clearly unrelated candidate for the same task;
   - an A/B label swap (swapping which option means "relevant" must roughly
     mirror P).

## 7. Metrics

- **Candidate level** (within the top-20): nDCG@10, MRR, R@5, R@10, and AUC
  of the score vs. gold.
  - Route loss (gold absent from the top-20 shortlist, and from the whole
    pool) is reported separately; no reordering can fix it.
  - CB candidate gold means the symbol span overlaps a ContextBench gold
    line interval (`Gold.init + Gold.add`) in the same file.
- **Pack level (CB):** ContextBench's own `evaluate_task` on the pack items'
  symbol spans:
  - file / symbol / span / line coverage and precision
  - gold-in-pack
  - gold-relevant tokens per 1k pack tokens
  - loss partition: route / order (in pool, not in pack) / allocation
- **Uncertainty:** a paired bootstrap 95% CI over tasks (2,000 resamples,
  seed 0), plus win/loss counts, per repo, and per query-style stratum
  where n allows.
- **Cost:**
  - Laya: cold import + load, warm scoring latency for N=20 (p50/p90),
    per-pair latency, peak process RSS, checkpoint disk
  - end-to-end total: `oxide query` + Laya
  - failure count

## 8. Gates (pre-registered)

- **O (operational), checked before any quality run.**
  - **Hard stop:** warm p50 > 5 s to score one N=20 shortlist on this CPU,
    or peak RSS > 4 GiB, or non-deterministic scores on repeat.
    `docs/reranker-eval` set the precedent: a ~36 s/query reranker was
    disqualified for a synchronous CLI.
  - Between the hard stop and the target (warm p50 ≤ 1 s, p90 ≤ 2 s), the
    quality screen still runs, but the best possible disposition is
    "insufficient evidence / cost-blocked", never "further candidate".
- **Q1 (dev screen).** The selected configuration must beat production on
  dev nDCG@10 in **both** plain and masked, and its AUC must beat the
  fused-rank AUC in both. Otherwise: stop, and A is **rejected**. It
  failed even in-sample, on leaky snippets that favour a content reader.
- **Q2 (CB, primary).** Candidate nDCG@10 Δ and pack symbol-coverage Δ
  against production are both computed. Dispositions:
  - **Further candidate:** both > 0, at least 2:1 wins:losses on the pack
    metric, file coverage not worse than the random-permutation control's
    loss, and gate O's target met.
  - **Reject:** either Δ ≤ 0.
  - **Insufficient evidence:** otherwise, including when the CI spans zero
    with a positive mean.
  - **Only if Q2 passes:** the held-out candidate-level run, as a
    direction-consistency check.

Hypothesis B (entity alignment) is **not run** in this pass. No
independently labeled identity / related / nonmatch pair set exists in the
repository, and building one is its own budgeted task (issue #15 §4).

## 9. Deviations after seeing results

1. **CB shortlist width.** The captured `kept` pools hold 8–17 candidates
   (16 search seeds plus bounded expansion, after dedup). N = 20 is
   therefore the whole pool on every CB task. This follows from production
   behavior; nothing was re-chosen.
2. **CPU-configuration sweep.** The default measurement breached gate O's
   hard stop. At the user's request, the software configuration was ruled
   out before the stop was accepted: they pointed out that the CPU is not
   lacking and may simply need different optimization techniques. The
   sweep is operational only: 3 fixed CB shortlists, the `noul_ab`
   question, and parity against the stock output
   (`scripts/cpu_sweep.py`, `results/ops/sweep/`). It covered:
   - thread count
   - P-core pinning
   - `reference_compile=False`
   - length-sorted batching
   - bf16 autocast
   - int8 dynamic quantization
   - ONNX Runtime via the official upstream exporter

   Gate O thresholds were not changed.
   Irregularities in how the sweep ran:
   - **bf16 was killed by hand** after 11 min 05 s. Its result file,
     `sweep/torch-bf16-t6-pcore.json`, was written by hand from that
     observation, not by the script.
   - **int8 crashed on its first attempt.** `quantize_dynamic` also
     replaced the decision head's `nn.TransformerEncoder` Linears, and
     torch's fused-path check then failed (`AttributeError` in
     `sweep.log`). The script was changed to quantize the encoder only
     (`cpu_sweep.py`, `torch-int8` branch) and int8 was rerun on its own.
   - **Two rows come from a different harness.** The 10-thread and
     multilingual numbers come from `laya_score.py ops` on a 5-task subset
     (3 reps, one global warm-up), not from `cpu_sweep.py`. Their parity
     against the reference was not recorded.
   - **The ONNX thread count is not controlled by the harness.**
     `ONNXAgent` builds its `InferenceSession` without `SessionOptions`, so
     ONNX Runtime picks its own intra-op thread count inside the 6-CPU
     `taskset` mask.
3. **Quality stages not run.** Gate O's hard stop held in every official
   configuration, so, as pre-registered, the following were **not run**:
   - Q1 and Q2 (the dev screen, and the CB candidate and pack evaluation)
   - the held-out check
   - the §6.5 model sanity controls: the relevant vs. unrelated pair and
     the A/B label swap. They precede quality runs, and none took place.

   No quality number exists for Laya in this pass.
4. **Mapping a gate-O hard stop to a disposition.** §8 says "stop" for a
   hard stop but does not name an issue-level disposition. Issue #15
   requires one of three. This report records hypothesis A as **reject
   (cost) for the local synchronous CPU path**, with quality **insufficient
   evidence**. That is stricter than §8's "insufficient evidence /
   cost-blocked", which covered only the band between the target and the
   hard stop, and it applies only to the deployment measured here.
5. **Replay parity scope.** `evaluate.py parity` (`results/baseline/parity.json`)
   reproduces all 21 baseline packs exactly. On these tasks it exercises the
   per-file, primary and test caps. It does **not** exercise the relevance
   floor or the budget-overflow and shrink-to-fit paths: no task omits
   anything for those reasons, and the maximum `used_tokens` is 2,555 of
   4,096.
6. **Determinism resolution.** Laya rounds `noul` and `choice`
   probabilities to 4 decimals. "Identical" and "deterministic" in the
   results therefore mean identical at 1e-4, not bit-identical logits.
7. **Timing conditions.** The first scoring call (13.9 s for 13
   candidates) was faster than the later warm p50 (20.8 s). It ran on the
   task with the shortest inputs (698 vs a median of 1,684 query+source
   chars), which explains it without any clock effect. Thermal throttling
   under sustained load was not measured. The gate verdict holds either
   way.
8. **Provenance files** (`results/provenance/`) were added after the
   independent review: CPU topology and ISA flags, upstream's CPU
   benchmark JSON and ONNX exporter, the Context7 quote, and the
   baseline-dump hash. `protocol.md` is an untracked file, so its "written
   before any Laya output" claim is not verifiable from mtimes, which
   post-date the results because of these later edits.

## 10. Quality-screen continuation (2026-09-26, written before any relevance output)

Gate O remains failed and is not re-litigated: the 5 s threshold holds, and
nothing here can promote Laya to the request path. On 2026-09-26 the user
directed that the quality screen be completed, because the gate-O stop
left Laya's relevance quality unmeasured. The stages below replace the
all-at-once §5 selection with a staged one. The stages, the selection
rules and the kill rules are fixed here, before any Laya relevance score
exists. The pre-registered variant grid of §5 does not change.

**Stage 0: sanity, selection, and offline-judge subset.**
- *Pairs.* The 232 Jev-judged dev pairs that fall inside the dev-plain
  top-20 shortlists (28 tasks, 26 commit-gold). Add one unrelated control
  per task: a candidate taken from a task in a *different* repository,
  which is non-relevant by construction.
- *Scored.* Both checkpoints (`english`, `multilingual`), with all three
  questions (`noul_ab`, `choice_ab`, `choice_ba`).
- *Measured on the same pairs:*
  - AUC vs. commit gold for each (checkpoint, question), next to the
    fused-rank AUC (the position in the frozen fused list);
  - AUC and thresholded agreement vs. Jev (Jev `noul` ≥ 0.5);
  - Jev's own AUC vs. commit gold, which is the bar for the offline-judge
    application;
  - label-swap mirroring: Spearman correlation of `choice_ab` P(A) with
    `choice_ba` P(B);
  - separation of the unrelated controls: the fraction of controls scored
    below the task's median judged candidate.
- *Selection.* Exactly one (checkpoint, question) pair is selected: the
  highest AUC vs. commit gold on these pairs. A tie goes to `english`,
  then `noul_ab`.
- *Kill rule (stop the screen, A-quality = reject).* Stop if the label
  swap does not mirror (Spearman < 0.5 for both checkpoints). Also stop
  if every configuration's bootstrap 95% upper bound on AUC (task-level
  resampling, seed 0) is below the fused-rank AUC. With 26 positives,
  nothing short of that clear loss stops the screen.

**Stage 1: full dev (Q1 as pre-registered in §8).**
- Only the selected pair is scored: plain + masked, 61 tasks, fused
  top-20, P-core pinned, with length-sorted batches of 4. That setting was
  measured to give output identical to within 1e-4 (§3.2).
- Q1 unchanged: C1 or C2 (whichever is better on the mean of plain and
  masked nDCG@10, as §5 specified) must beat production nDCG@10 **and**
  fused-rank AUC in **both** regimes.
- Also reported:
  - the random-permutation control;
  - route vs. ordering loss, where route means gold outside the top-20
    shortlist, split into "in the top-200 union" and "absent";
  - top-10 false positives (non-gold promoted into the top-10) and false
    negatives (gold demoted out of it);
  - per-task regressions.
- Dev has no `kept` pools, so pack-level metrics do not exist on dev.

**Stage 2: only if Q1 passes.**
- ContextBench candidate + pack (Q2 as pre-registered).
- *Before any challenger pack is trusted,* rerun production `oxide query`
  at `--budget-tokens 1024` on the 21 tasks and require exact replay
  parity at that budget. That exercises the over-budget and shrink-to-fit
  paths the 4,096-token parity never hit.
- The held-out set runs only if ContextBench also passes. It needs clones
  that are missing.

**Later steps, gated on measured quality.**
- *Selective reranking* is tested only if Stage 1 and Stage 2 both show a
  gain.
- *Entity alignment* uses no Laya output until an independently labeled
  identity / related / nonmatch set exists. That set comes from an oracle
  independent of Laya, Jev and OXIDE's own name-matching heuristics.
- No runtime engineering (port, quantization, new model) happens without
  a measured quality gain.

**Outcome (recorded after the runs).**
- *Stage 0.* English label swap mirrored (Spearman 0.65); multilingual did
  not (0.26). No kill: not every upper CI was below the fused-rank AUC
  of 0.6105 (multilingual `noul_ab`'s upper bound of 0.6102 was, the
  others were not). English `noul_ab` was selected (AUC 0.626).
- *Stage 1.* C2 was chosen per §5, on the mean nDCG@10 (0.266 vs C1
  0.189). It does not beat production nDCG@10 in either regime (plain
  −0.032, masked −0.002; both CIs span zero). Its ordering's per-task AUC
  (plain 0.733, masked 0.656) is below the fused-rank AUC (0.750, 0.691);
  the raw Laya score AUC is 0.611 and 0.579. **Q1 fails.** Stage 2, the held-out set,
  selective reranking and runtime work were not run.
- *Deviations.*
  - Stage 0 used the pre-existing Jev judgments as a second label source
    for the offline-judge comparison only; they were never used for
    selection.
  - `laya_score.py score` gained a question filter and length-sorted
    batches of 4 before any Stage 0 output. Sequence construction is
    unchanged: the room is still computed from the full question set.
