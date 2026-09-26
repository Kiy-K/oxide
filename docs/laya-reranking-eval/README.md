# Issue #15 — local Laya as a task-aware evidence reranker

Status: **research only. Nothing in `src/`, the defaults or the dependencies
changed.**

- **Hypothesis A, reranking:** fails on **both** axes. The synchronous CPU
  cost gate failed first (§3). The quality screen, completed at the
  user's direction on 2026-09-26 (§4), then showed **no improvement** over
  frozen OXIDE on the development set:
  - The Q1 configuration (Laya fused with the original rank) is −0.032
    (plain) and −0.002 (masked) nDCG@10; both CIs span zero.
  - Ordering by Laya alone is significantly worse on plain (−0.143).
  - A post-hoc, judged-only check with Jev-augmented relevance labels
    leans slightly positive, with CIs spanning zero.

  Quality under complete labels is therefore **insufficient evidence**,
  not a proven loss.

  Pre-registered gate Q1 fails, so ContextBench validation, selective
  reranking and all runtime engineering stop there.
- **Offline-judge use:** not supported. On the same pairs, Laya separates
  gold far worse than the existing Jev judge.
- **Hypothesis B, entity alignment:** not run. Only a labeling
  specification exists.

Protocol, pins, staging and deviations: [`protocol.md`](protocol.md).
Stages were pre-registered in §10 before any relevance output.

## 1. Pinned baseline (post-#14, measured now)

- `main` @ `5696358` (after #14 lean snapshot), shipped
  `native:arctic-embed-xs-q`, frozen RRF K=60 / 0.6–0.4 / 200 candidates.
- `examples/fusion_dump` reproduces the frozen ContextBench dump
  byte-for-byte on 21/21 instances: lexical, semantic, fused, expanded,
  neighbors, spans, ids and pack.
- `oxide query --json` packs equal those packs as sets, with the same
  `used_tokens`.
- Fixture gate: hybrid R@5 0.909, the same value as the fixture table in
  `ranking-fusion-eval` §3.3.
- **Cost of the thing being reranked:** one-shot `oxide query` over the 21
  CB repos, 3 cold-process runs each after a warm-up. Median **124 ms**
  (p90 233 ms, max 340 ms). Peak RSS median **83 MB** (max 112 MB).
  Raw data: `results/baseline/`.
- An offline allocator replica (`scripts/oxide_replay.py`) reproduces
  **every** CB pack exactly: item set, per-item `est_tokens` and
  `used_tokens`, 21/21 (`evaluate.py parity` → `results/baseline/parity.json`).
  - On these tasks it exercises the per-file cap (9 tasks), the primary cap
    (19) and the test cap (7).
  - It does **not** exercise the relevance floor or the budget-overflow and
    shrink-to-fit paths: none fire, and the maximum `used_tokens` is 2,555
    of 4,096. Those branches would need a targeted check before a
    challenger pack is trusted.
  - The pack-level comparison was never needed.

## 2. Official runtime validation

| item | result |
| --- | --- |
| package | `laya==0.3.20` (PyPI latest), `torch 2.14.0+cpu`, `transformers 5.17.0`, isolated venv outside the repo |
| checkpoint | `convaiinnovations/laya` @ `55cf4c4e`, Apache-2.0. The same revision as upstream's own CPU benchmark. English: ModernBERT-large, 512 ctx. Multilingual: mmBERT-base, 1024 ctx. 1.5 GB on disk |
| load | in-process `laya.load(<local dir>)`, `HF_HUB_OFFLINE=1`, no server, no network at inference |
| schema | `noul` with neutral `A`/`B` labels, and a two-option `choice`. The instruction is reused verbatim from the Jev judge (`ranking-fusion-eval/scripts/judge.py`) |
| input | plain-text state: task (≤128 tokens) + path::symbol + `render_snippet` window. Bounded to the state room measured from `build_sequence` (English 416 tokens, multilingual 929) so Laya never truncates it on its own. Asserted with the tokenizer: 0/798 English and 0/180 multilingual sequences exceed the window (`results/ops/truncation-check-*.json`). Measured English sequence length: mean 459 tokens, p50 509 |
| truncation (English, 266 CB pairs) | task cut in 206 (77%; CB issue texts are long), source cut in 181 (68%) |
| determinism | identical scores on repeat calls (21 × 3 repeats), at Laya's 4-decimal output rounding |
| failures | 0 |
| docs grounding (Context7) | Laya's own benchmark scores **"RAG passage relevance" at 0.625 (English) / 0.657 (multilingual)**, and that dataset was *in* its training mix. The README states that the base checkpoints are "a fast base to specialise, not a zero-shot decision engine" |

## 3. Operational gate O

Scoring one shortlist means one sequence per candidate: about 13 on CB,
and 8–17 in general, because the whole `kept` pool is at most 17. There
is no GPU.

### 3.1 Default configuration, all 21 CB shortlists (English, 6 threads)

| measure | value |
| --- | ---: |
| cold process → model loaded | 5.6 s (load 4.6 s) |
| first scoring call | 13.9 s |
| **warm, per shortlist: p50 / p90 / max** | **20.8 s / 25.5 s / 32.5 s** |
| warm, per candidate p50 | 1.65 s |
| peak RSS | 2.8 GB (≈34× `oxide query`) |

Multilingual (5 tasks) came in at 15.1 s p50 and 3.0 GB. On those same 5
tasks its sequences are 1.41× longer (mean 614 vs 435 tokens) because its
state room is larger (929 vs 416), so this is not a like-for-like speedup.

The first call (13.9 s) was faster than the warm p50. It ran on the task
with the shortest inputs, though (mean 698 query+source chars against a
median of 1,684), so it is not evidence of a faster clock state.
Throttling under sustained load is possible but was not measured.

### 3.2 CPU-configuration sweep (3 fixed shortlists: 13 + 13 + 12)

The sweep was added at the user's request, so that software configuration
was ruled out before the stop was accepted. The CPU is an i7-13620H: 6
P-cores with hyper-threading plus 4 E-cores, AVX2/FMA/AVX-VNNI, and no
AVX-512 or AMX. Parity is measured against the stock output.

| configuration | warm p50 / mean per shortlist | peak RSS | parity with stock |
| --- | ---: | ---: | --- |
| stock torch fp32, 6 threads, unpinned (reference) | 24.5 s / 23.8 s | 2.8 GB | — |
| stock, 10 threads, unpinned (5-task run, `laya_score ops` harness) | 25.9 s | 2.8 GB | not recorded |
| **6 threads pinned one-per-P-core** | 19.6 s / 19.4 s | 2.8 GB | identical |
| 12 threads on all P-core hyper-threads | 20.7 s | 2.8 GB | identical (Δp ≤ 1e-4) |
| pinned + `reference_compile=False` (upstream's setting) | 18.1 s / 19.2 s | 2.8 GB | identical |
| pinned + length-sorted batches of 4 (upstream feature) | 18.7 s / **16.1 s** | 2.8 GB | identical |
| pinned + `LAYA_CPU_AMP=bf16` | aborted, > ~60 s | — | — (bf16 emulated on this CPU) |
| **ONNX Runtime**, official upstream exporter + `ONNXAgent`, pinned | 19.4 s / 17.3 s | **2.1 GB** | identical (Δp ≤ 1e-4) |
| pinned + int8 dynamic quantization of the encoder (VNNI; not an official config) | **11.7 s** | 3.9 GB | **changed**: max Δp 0.36, 18/38 decisions flipped, 3/3 orders changed |

The user was right that the software mattered. Avoiding the E-cores alone
is worth 1.25×, and batching or ONNX reach about 1.4× on the mean. None of
it reduces the arithmetic, though.

The required throughput is what decides the gate:

- **Work per candidate:** roughly 0.35–0.4 TFLOP (a 395M-parameter encoder ×
  a measured mean of 459 tokens).
- **Budget:** hitting 5 s for a 13-candidate shortlist allows ≤ 0.38 s per
  candidate, which needs about 1 TFLOPS of *sustained* fp32.
- **Observed:** sustained warm runs managed about 0.2–0.25 TFLOPS
  (≈1.5 s per candidate at the mean length).
- **Headroom:** the AVX2/FMA peak on 6 P-cores is higher. A dense-GEMM
  runtime running near peak under sustained load could close part of the
  gap, but nothing measured here does.

Upstream's own CPU benchmark (`results/provenance/latency_cpu_m7a_xlarge_20260924.json`,
same revision) is consistent with this. It reports 580 ms for **one**
question over a short ticket state (a sentence repeated 6×) on 4 EPYC
cores.

int8 is the only configuration that removes arithmetic. It changes the
model's answers enough that it would need its own quality validation,
and it still stays more than 2× over the limit.

### 3.3 Verdict on gate O

**Hard stop.** The gate is defined on the warm p50. The best
official-numerics p50 is 18.1 s per shortlist; the best mean is 16.1 s
with length-sorted batching. Both are against a 5 s limit, and the ≤ 1 s
target is out of reach by more than 16×. Even the fastest single
shortlist observed, the 13.9 s first call on the shortest-input task, is
2.8× over the limit.

End to end:
- **Warm, long-lived process:** a 124 ms `oxide query` becomes about
  16–20 s.
- **One-shot CLI:** about 20 s (5.6 s load + ~13.9 s first call + 0.12 s
  query).
- **Peak memory:** 83 MB becomes 2.1–2.8 GB.

The rejected cross-encoder in `docs/reranker-eval` was disqualified at
about 36 s/query. This sits in the same regime.

## 4. Relevance quality (protocol §10; completed 2026-09-26)

All inputs are query text plus candidate code; no gold labels, gold
names or diffs reach the model. The dev labels carry two biases that pull
in opposite directions:

- **Leakage** favours a content reader. The snippets are post-commit code
  (`protocol.md` §3).
- **Incomplete commit gold** penalises a reranker that promotes relevant
  but unlabeled symbols.

§4.2's sensitivity check addresses the second.

### 4.1 Stage 0: sanity, selection, offline-judge comparison

The pairs are the 232 dev pairs already judged by Jev (TypeSafe System
One, `ranking-fusion-eval/results/judgments.jsonl`) that lie inside the
dev-plain top-20 shortlists: 28 tasks, 26 commit-gold. Each task also
gets one unrelated cross-repository control.
Raw data: `results/quality/stage0/`.

Pooled AUC: all pairs are ranked on one scale, so ranks from different
tasks mix. The pairs are Jev's top-5-per-channel selection, which
compresses the rank baseline.

| scorer | pooled AUC vs commit gold [task-bootstrap 95% CI] | AUC vs Jev labels | agree with Jev at 0.5 | judged relevant (≥ 0.5) | controls below task median |
| --- | ---: | ---: | ---: | ---: | ---: |
| frozen OXIDE fused rank | 0.611 [0.43, 0.79] | — | — | — | — |
| **Jev (existing independent judge)** | **0.924 [0.86, 0.97]** | — | — | 38% | — |
| Laya english / `noul_ab` (**selected**) | 0.626 [0.51, 0.76] | 0.652 | 0.41 | 93% | 86% |
| Laya english / `choice_ab` | 0.574 [0.44, 0.71] | 0.612 | 0.43 | 89% | 43% |
| Laya english / `choice_ba` (label swap) | 0.622 [0.50, 0.76] | 0.628 | 0.39 | 93% | 79% |
| Laya multilingual / `noul_ab` | 0.517 [0.42, 0.61] | 0.490 | 0.42 | 93% | 36% |
| Laya multilingual / `choice_ab` | 0.607 [0.51, 0.70] | 0.583 | 0.54 | 61% | 29% |
| Laya multilingual / `choice_ba` | 0.531 [0.44, 0.63] | 0.551 | 0.47 | 70% | 14% |

- **Per-task AUC on the same pairs** (`results/quality/stage1/supplementary.json`):
  - vs commit gold: Laya 0.75 against fused rank 0.657 (19 tasks with
    both classes);
  - vs Jev labels: 0.687 against 0.624 (25 tasks).

  On this pre-selected subset Laya looks *better* than the fused order.
  Stage 1, over the full top-20, did not reproduce that.
- **Label-swap mirroring (Spearman):** English 0.65, which passes;
  multilingual 0.26, which fails. The kill rule did not fire: English
  mirrored, and not every upper CI was below the fused-rank AUC
  (multilingual `noul_ab`'s upper bound of 0.6102 did fall just below
  0.6105). English `noul_ab` was selected by the pre-registered rule.
- **No calibration.** Laya calls 61–93% of candidates relevant, depending
  on the configuration.
- **Only English `noul_ab` rejects unrelated controls.** It places 86% of
  cross-repository controls below the task median. Both `choice` forms
  and the multilingual checkpoint mostly fail this basic check.

### 4.2 Stage 1: full dev, gate Q1

The setup is English `noul_ab`, fused top-20, 61 tasks × 2 regimes (2,440
states), with 0 failures. Raw data: `results/quality/stage1/`,
`stage1-eval.json`, `supplementary.json`.

Input bounding: the model's tokenizer confirmed that no state exceeds
Laya's window (0/3,660 per regime, `results/ops/truncation-check-dev-*`).
The harness's own bounding did cut the query in 20 states per regime and
the source window in 498 (plain) and 507 (masked) of 1,220.

| regime | order | nDCG@10 | MRR | R@5 | R@10 | ΔnDCG@10 vs production [95% CI] | wins / losses |
| --- | --- | ---: | ---: | ---: | ---: | --- | --- |
| plain | **frozen OXIDE** | **0.401** | **0.371** | 0.397 | **0.580** | — | — |
| plain | random permutation (10 seeds) | 0.146 | 0.131 | 0.120 | 0.329 | −0.255 [−0.342, −0.167] | 9 / 38 |
| plain | Laya only (C1) | 0.258 | 0.244 | 0.239 | 0.451 | **−0.143 [−0.249, −0.042]** | 15 / 28 |
| plain | RRF(original, Laya) (C2) | 0.369 | 0.363 | **0.425** | 0.555 | −0.032 [−0.081, +0.019] | 15 / 18 |
| masked | **frozen OXIDE** | **0.164** | 0.153 | **0.209** | **0.287** | — | — |
| masked | random permutation | 0.092 | 0.084 | 0.082 | 0.195 | −0.072 [−0.131, −0.017] | 9 / 20 |
| masked | Laya only (C1) | 0.120 | 0.108 | 0.098 | 0.242 | −0.044 [−0.104, +0.016] | 8 / 17 |
| masked | RRF(original, Laya) (C2) | 0.162 | **0.162** | 0.176 | 0.274 | −0.002 [−0.040, +0.039] | 7 / 9 |

- **Per-task AUC within the top-20** (score AUC → **C2 ordering AUC**):

  | regime | raw Laya score | C2 ordering | fused rank |
  | --- | ---: | ---: | ---: |
  | plain | 0.611 | 0.733 | **0.750** |
  | masked | 0.579 | 0.656 | **0.691** |

- **Gate Q1** required C2, the better combination, to beat production
  nDCG@10 **and** fused-rank AUC in **both** regimes. It beats neither in
  either regime, though its nDCG deltas are within noise. **Q1 fails.**
- **Post-hoc sensitivity, not pre-registered, gate unchanged**
  (`scripts/sensitivity_jev.py`, `sensitivity-jev-labels.json`). On the
  28 Jev-judged tasks, relevance was widened to commit gold ∪ Jev-relevant.
  - Jev judged exactly production's (fused) top-5, so the judged share
    of each ordering's top-10 is asymmetric: production 68%, C2 64%, C1
    43%.
  - *Treating unjudged candidates as non-relevant* is therefore biased
    toward production by construction. That view gives C1 −0.159
    (6 wins / 20 losses) and C2 −0.026.
  - *The judged-only condensed list* removes that bias and gives:

    | labels | tasks | C1 Δ nDCG@10 | C2 Δ nDCG@10 |
    | --- | ---: | --- | --- |
    | commit gold ∪ Jev-relevant | 26 | +0.036 [−0.052, +0.134] (10 / 13) | +0.012 [−0.027, +0.055] (11 / 12) |
    | commit gold only | 19 | −0.058 [−0.234, +0.129] | −0.024 [−0.125, +0.070] |

  With broader relevance labels, the sign flips to slightly positive,
  within noise. Incomplete commit gold may therefore account for part of
  Laya's Stage 1 loss. Quality under complete labels is **unresolved**:
  small, not significant, and measured on a leaky, pre-selected subset.
- **Per repository**, nDCG@10 production → C2:

  | repository | plain | masked |
  | --- | --- | --- |
  | pylint (37 tasks) | 0.389 → 0.354 | 0.189 → 0.153 |
  | pytest (22 tasks) | 0.453 → 0.417 | **0.133 → 0.181** |

  Requests and flask have 1 task each. Masked pytest is the only
  multi-task cell where Laya helps. That is one positive stratum, not
  enough to move the gate.
- **Top-10 changes vs production (false positives and negatives), plain /
  masked:**
  - C1 promotes 298 / 303 non-gold candidates into the top-10, pushes
    17 / 6 gold out, and pulls 8 / 5 gold in. It regresses 28 / 17 tasks.
  - C2 promotes 147 / 145 non-gold, pushes out 8 / 2 gold, pulls in 5 / 2
    gold, and regresses 18 / 9 tasks.
- **Failure partition.** Where each task's gold stands relative to the
  pool, plain / masked:
  - gold in the reranked top-20 (at least one gold, reachable by
    ordering): 47 / 29
  - no gold in the top-20, but gold in the top-200 channel union
    (candidate-generation loss beyond the shortlist): 8 / 20
  - gold absent from the pool (route loss): 6 / 12

  Some of the 47 / 29 tasks have *other* gold outside the top-20.

  Ordering is the only failure a reranker can fix, and on the 47 / 29
  tasks where it could, Laya makes ordering worse. Allocation (pack)
  failures are not observable on dev, because no `kept` pools exist.
- **Cost of the screen.** Mean 16.8 s (plain) and 17.0 s (masked) per
  20-candidate task; p50 17.4 s and 17.6 s. P-core pinned with sorted
  batches; peak RSS 2.8 GB.

### 4.3 What was deliberately not run

Q1 failed, so per `protocol.md` §10 the following were not run:

- **ContextBench candidate and pack evaluation.** Gold-in-pack and
  relevant tokens per 1,000 therefore **do not exist for Laya**; the
  baseline packs are unchanged.
- **The 1,024-token replay-parity check** that would have come before it.
- **The held-out set.**
- **Selective reranking.** It was gated on a measured gain, and there is
  none. This was not tested.

No held-out or ContextBench label was consulted at any point.

## 5. Dispositions

| question | disposition | basis |
| --- | --- | --- |
| **Synchronous reranking feasibility** | **Reject** (cost), with quality **insufficient evidence**. | Cost alone decides it: best p50 18.1 s / mean 16.1 s per shortlist vs a 5 s hard stop; 2.1–2.8 GB vs 83 MB (§3). Quality: Q1, pre-registered on commit gold, fails. C2 shows no improvement (CIs span zero), and C1 is significantly worse on plain. A post-hoc judged-only check with Jev-augmented labels leans slightly positive (+0.01 to +0.04, CIs span zero), so the quality question is unresolved (§4.2). |
| **Selective reranking feasibility** | **Reject (untested, gated).** | `protocol.md` §10 gates it on a measured quality gain, and none exists. |
| **Offline evaluation utility** (Laya as a retrieval-quality judge) | **Reject** on this evidence (the leaky dev subset only). | Same 232 pairs, different inputs: Jev saw ≤ 1,200 snippet chars plus kind, signature and the full query; Laya saw a 416-token state with the query capped at 128 tokens. Laya's pooled AUC vs commit gold is 0.52–0.63 against Jev's 0.92. It labels 61–93% of candidates relevant, agrees with Jev on only 39–54%, and the multilingual checkpoint fails the label-swap check. Jev remains the better offline judge; its API-key and network constraints are unchanged from `docs/evals/phase-4.2-typesafe`. |
| **Entity-alignment evidence** (B) | **Insufficient evidence (not run).** | No independently labeled identity / related / nonmatch set exists. [`entity-alignment-spec.md`](entity-alignment-spec.md) fixes how to build one from a SCIP oracle. It is a separate, budgeted task, and Laya's relevance result is not a reason to start it. |

No production code, schema, graph, symbol ID, default or dependency was
touched. The Laya venv, checkpoints and ONNX export live under
`~/.cache/oxide-laya-eval/`, outside the repository.

## 6. Smallest justified next action

**Close hypothesis A for the request path.** Record the result on #15:
rejected on cost, and Q1 failed on the pre-registered commit-gold
measure. Do no further Laya runtime work: no port, no quantization, and
no model competition. No measured gain on the pre-registered measure
supports it.

The one open quality question, whether Laya helps under complete
relevance labels, is not worth a runtime investment. It can only be
settled offline, on clean labels: the ContextBench human-labeled set,
which was gated off here. It is only worth running if someone wants
Laya as an optional offline component, and that needs your approval.

The losses that motivated A are still open, and they are not ordering
losses a learned reranker fixes:

- **Candidate generation / route:** 8+6 / 20+12 of 61 tasks (plain /
  masked) have no gold in the shortlist.
- **Allocation caps.**

They belong to the semantic-quality and allocator tracks of roadmap #9.
B stays parked behind its labeling spec until separately approved.

## 7. Limitations and uncertainty

- **Dev is commit-derived and leaky.**
  - Post-commit code in the snippets favours Laya.
  - Incomplete gold penalises it: Jev judged 27% of non-gold top-5
    candidates relevant.
  - The judged-only Jev-augmented check leans slightly positive for
    Laya. It is post-hoc, covers only Jev's top-5-per-channel pairs in 26
    tasks, and its CIs span zero, so it can neither confirm nor rule out
    a real gain.
  - ContextBench, the clean human-labeled set, was not reached because
    the pre-registered gate stopped the screen.
- **Pre-registration timing is not verifiable from mtimes.** `protocol.md`
  §10 was edited again after the runs to add their outcome. The scripts
  §10 relies on (`stage0.py`, `evaluate.py`, `laya_score.py`, modified
  08:01–08:04) predate the first score file (08:16).
- **Validity at the pin is inferred, not re-verified.** The dev dumps
  predate #14 and their indexes no longer exist. Their validity at the pin
  rests on the CB byte-identity and #14's oracle test (`protocol.md` §2).
  Their candidate text was rebuilt from cached clones whose HEAD matches
  5,671 of 5,676 dump spans; 2 (plain) and 6 (masked) candidates had
  empty source.
- **Stage 0 is small.** It has 26 positives in 28 tasks, so its CIs are
  wide. It served only for selection and the offline-judge comparison.
  Stage 1's conclusion rests on 61 tasks × 2 regimes, a CI that excludes
  zero for C1 on plain, and consistent direction in every cell.
- **Laya's inputs were bounded.** Only the pre-registered instruction
  (reused from the Jev judge) and two answer forms were tried, with the
  task capped at 128 tokens and the source at the remaining room. A
  different prompt, a fine-tuned checkpoint (the README says the base
  checkpoints need specialising), or longer context were not tried. Trying
  them without a measured signal would be the post-hoc tuning the
  protocol forbids.
- **One machine, no GPU.** Only one laptop CPU was measured.
- **ONNX run conditions.** ONNX was run through `laya.onnx_agent` (no
  batch API; ONNX Runtime chose its own thread count).
- **Sweep irregularities** are in `protocol.md` §9.2: a manual bf16 abort,
  an int8 crash and rerun, and two rows from a different harness.

## 8. Independent review

Two independent passes were run, each reviewing the bundle under
`docs/review/` with its own recomputation code.

**Pass 1 (2026-09-25, operational stage).** No BLOCKER. It recomputed
every §1–3 number and re-ran replay parity.

It raised two MAJOR findings, both fixed:
- the replay-parity provenance and its branch coverage;
- an FLOP-floor argument contradicted by the data.

It also raised MINOR findings on the disposition vocabulary, the
deviation list, the one-shot estimate, determinism resolution and a
truncation assertion.

**Pass 2 (2026-09-26, quality stage).** No BLOCKER. It independently
reproduced:
- every Stage 0 and Stage 1 metric (CIs within 0.001);
- the churn and route counts;
- the input schema (no gold leaks into model input);
- the gate logic.

It raised one MAJOR finding: incomplete commit gold biases *against*
Laya, so the dev result is not simply "conservative". That led to the
post-hoc Jev-augmented sensitivity check in §4.2. A follow-up
consultation found that check's first version biased toward production:
unjudged candidates counted as non-relevant, and Jev had judged exactly
production's top-5. The judged-only version leans slightly positive for
Laya, within noise, and the quality verdict was changed to insufficient
evidence.

MINOR findings, all addressed above:
- a false kill-rule sentence;
- C2's AUC mislabeled;
- pooled vs per-task AUC unlabeled;
- unequal Jev/Laya inputs in the offline-judge comparison;
- three soundness gaps in the entity-alignment spec;
- cost and truncation wording;
- missing per-repo strata;
- the disposition vocabulary.

## Reproduce

```
# baseline (pinned HEAD):  results/baseline/run_baseline.py  (+ examples/fusion_dump, oxide eval)
python3 scripts/prep.py cb <baseline_dir> results/inputs/cb-shortlists.jsonl
python3 scripts/prep.py dev plain|masked results/inputs/dev-<regime>-shortlists.jsonl
~/.cache/oxide-laya-eval/venv/bin/python -I scripts/laya_score.py ops <ckpt> results/inputs/cb-shortlists.jsonl out.json 6
results/ops/sweep/run_sweep.sh            # CPU sweep; int8 rerun: scripts/cpu_sweep.py torch-int8 ...
python3 scripts/evaluate.py parity <dir with extracted results/baseline/kept-and-packs.tar.gz>
~/.cache/oxide-laya-eval/venv/bin/python -I scripts/check_truncation.py <ckpt> results/inputs/cb-shortlists.jsonl
# quality screen (protocol §10)
python3 scripts/stage0.py build results/inputs/stage0-shortlists.jsonl results/quality/stage0/meta.json
results/quality/stage0/run_stage0.sh && python3 scripts/stage0.py eval results/quality/stage0/meta.json results/quality/stage0/scores-{english,multilingual}.jsonl
results/quality/stage1/run_stage1.sh && python3 scripts/evaluate.py dev results/quality/stage1/scores-english-noul-{plain,masked}.jsonl
```
