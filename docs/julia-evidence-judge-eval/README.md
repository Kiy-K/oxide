# Julia-1 as a local evidence judge (research)

Issue: #31.

Status: **REJECT.** Production is unchanged. Julia judged only OXIDE's
existing bounded candidate pool, in a research-only allocator switch
(`research.patch`, `context::alloc_research` arms 8 and 9) inside a detached
`/tmp` worktree. The default arm is byte-identical to production.

Every number is **measured** unless marked *inferred*.

## 0. Pins

| item | value |
|---|---|
| OXIDE baseline | `origin/main` `1abb3d7dd601aa63da06e5336b1ae8e93db75e1d`. Local `main` `b738b8e` differs only in `docs/`. |
| Worktree | `/tmp/oxide-julia-1abb3d7` + `docs/alloc-utilization-eval/research.patch` + the Julia arms (`research.patch` here) |
| Julia | `SupersonicLabs/Julia-1`, HF revision `a85b127321d580d65176c89ced8273f305745d85` (lastModified 2026-09-26T22:03Z) |
| Weights | `model.safetensors` SHA-256 `df853bf7fe424420011f3d0c47a05d7341aa9eefa7fb9f203ea4aada4ad95b72` (matches `provenance.json` / `inference-policy.json`), 577,189,056 B |
| Runtime | official Python package in the repo (`julia/`), torch 2.14.0+cpu, transformers 5.0.0, Python 3.11, `strict_encoding=True`, `max_length=8192`, `head_length=512` |
| Machine | i7-13620H (6 P-cores / 12 threads + 4 E-cores), 15 GB RAM, CPU only |

Model facts from the card: a 144.3M-parameter encoder (mmBERT-small, 22 layers,
hidden 384, 256k vocab) with a decision head. It takes 2–20 options per call,
each option at most 48 tokens, and 8,192 tokens in total. The FP32 weights are
550.5 MiB.

## 1. Phase 0: runtime feasibility (`results/t*.json`, `results/rt-c5-*.jsonl`)

Julia was loaded once per process and kept resident. Warm numbers exclude the
load. Synthetic states are real Rust source cut to exact Julia token lengths.

| config | load | 1×512 tok | 1×1024 tok | 16×400 tok (one batched call) | peak RSS |
|---|---:|---:|---:|---:|---:|
| default: 4 threads, unpinned | 6.6 s (4.2 s warm cache) | 191 ms | 438 ms | 2,922 ms | 1.77 GB (full grid) |
| 6 threads pinned to P-cores (best) | 4.2 s | 133 ms | 299 ms | **1,983 ms** | 1.35 GB |
| 12 threads, P-cores | 4.4 s | 165 ms | 388 ms | 2,233 ms | 1.33 GB |
| 8 threads, unpinned | 4.4 s | 269 ms | 644 ms | 3,686 ms | 1.27 GB |
| 16 threads | 4.4 s | 209 ms | 463 ms | 2,763 ms | 1.32 GB |
| 6 threads, P-cores, `torch.compile` | 4.4 s | 143 ms | 323 ms | 1,954 ms | 1.44 GB |

- Latency grows linearly with candidate count (about 170–190 ms per 400-token
  candidate at 4 threads) and super-linearly with length (1×2048: 1.3 s).
- RSS after load is 793 MB, steady-state RSS 1.17 GB.
- Cold first judgment (1×400 tokens) takes 188 ms after the load.
- Determinism: 5 identical runs gave bit-identical logits. Batched and single
  calls differ by at most 1.9e-6 in logits.
- CPU utilization: CPU time ≈ 4 × wall time at 4 threads, i.e. all threads saturated.
- Not tried (out of scope or not the official path): ONNX, quantization, the
  `bend` backends (they need a native Bend/C build), and CPU bf16. This CPU has
  no AVX-512-BF16 or AMX, so bf16 is *inferred* to be slower.

**Real OXIDE pools.** Fresh-set-shaped ch5 corpus (61 tasks, median pool 15,
≤19 candidates), one persistent process, nothing else running:

| form | threads | cold first request | warm median | warm p95 | per candidate | peak RSS (process) | CPU time / request |
|---|---|---:|---:|---:|---:|---:|---:|
| J1 noul, pointwise | 6 pinned | 1,833 ms | **2,445 ms** | 4,035 ms | 171 ms | 1.67 GB | ≈14.6 s |
| J1 | 4 | 2,849 ms | 3,364 ms | 4,885 ms | 236 ms | 1.64 GB | ≈13 s |
| J2 choice, one call | 6 pinned | 700 ms | **922 ms** | 1,246 ms | 63 ms | 1.51 GB | ≈5.3 s |
| J2 | 4 | 956 ms | 1,167 ms | 1,603 ms | 79 ms | 1.50 GB | ≈4.6 s |

For comparison, OXIDE's own search takes a median of 37.8 ms and context
assembly adds ~20–40 ms on these corpora (ch5 study). **The runtime is not
usable for synchronous OXIDE requests:** J1 is 10× over the 250 ms median gate
and J2 is 3.7× over it. Peak *process* RSS is ~1.5–1.7 GB, of which ~600 MB is
the model load (225 MB before load → 832 MB after, 6 threads). This is a
separate Python process, not an increment measured inside OXIDE. This was reported as soon as
it was measured, and quality work continued only as a cheap dev screen.

Privacy: every Julia run executed under `unshare -rn` (no network namespace)
with `HF_HUB_OFFLINE=1`. The runtime source (`julia/`, `julia/router/`) has no
network imports. The only network access was the one pinned `snapshot_download`.

## 2. Judgment formulations (fixed in `scripts/julia_score.py` before any output)

Julia's state is the task text (cut to 256 Julia tokens), provenance (file,
qualified name, kind, role and non-numeric `found via` relation reasons) and
the 350-token capped snippet the allocator would deliver. Julia **never sees**
OXIDE scores, ranks or gold. Numeric `lexical=`/`semantic=` reasons are
stripped.

- **J1** `noul`: "Would this evidence materially help solve the task?" The
  false/true descriptions are the two from the brief. Output: P(true).
- **J2** `choice` over the whole pool (≤19, so always one native call, never
  over 20): "Which candidate is the most useful evidence for solving this
  task?" Options are `cN: <file basename>#<symbol>` (≤40 tokens). The state
  lists every candidate with a 96-token snippet. Presentation order is a
  deterministic hash. Output: P(option).
- **J3** `score` with a 4-level rubric (irrelevant / weakly useful / useful /
  directly useful). Output: E[score]/3.

No other wording was tried.

## 3. Development protocol (`DEV-PROTOCOL.md`, hashed 19:54:47 before scoring)

- Selection split S: CB 21 (human line gold), #30 held-out 63 (edit-locus), dev 70 (symbol-span).
- Confirmation split C: BCD 37, CA 19 (symbol-span), ch5 fresh 61 (edit-locus).
- All 271 are OLD sets. ch0 packs reproduce the earlier studies exactly (271/271).
- Candidate label: the capped snippet span contains a gold line. There are no
  manual labels. Category proxies are gold / non-gold same-file-as-gold /
  non-gold other-file.
- Rule: pick the form with the best R1 Δ coverage on S, then pick an R2
  threshold on S, then check on C, and freeze.

## 4. Candidate-level results (`results/candidate.txt`)

2,131 candidates / 154 tasks on S (gold rate 7.7 %). 1,708 / 117 on C.

| signal | AUROC S | AUROC C | within-task AUROC S | per-file-cap drops: gold vs junk AUROC S / C |
|---|---:|---:|---:|---:|
| OXIDE fused score | 0.726 | 0.760 | 0.714 | 0.567 / 0.841 |
| OXIDE pool order (baseline A) | **0.729** | **0.797** | **0.758** | — |
| J1 P(true) | 0.493 | 0.499 | 0.502 | 0.491 / 0.461 |
| J2 P(choice), hash order | 0.598 | 0.539 | 0.617 | 0.514 / 0.559 |
| J3 E[score] | 0.456 | 0.493 | 0.468 | 0.487 / 0.505 |

- **J1 is at chance and overconfident.** 57 % of candidates get P > 0.9. At
  0.5, precision is 0.080 against a base rate of 0.077. Calibration is flat:
  the gold rate is 5–14 % in every decile.
- J1 mean by category (S): gold 0.751, same-file non-gold 0.739, other-file
  0.753. It **cannot separate useful from junk.**
- **Allocator cases** (same-file siblings dropped by the per-file cap, S:
  394 dropped, 35 with gold). J1 averages 0.750 on gold siblings and 0.790 on
  non-gold siblings:
  - `requests-bc7dd0fc` `_validate_header_part` (non-gold, 203 tok) gets J1 **0.998**.
  - `requests-a044b020` `build_digest_header` (the edited method) gets **0.484**.
  - `requests-d2f6bdec` `Response.iter_content` (gold) gets **0.072**.
  - `flask-a411a243` `AppContext.session` (gold) gets 0.665. `flask-dbd4c288`
    `Scaffold.__init__` (non-gold, 352 tok) gets 0.840.
- **J2 is dominated by a position effect.** Julia gives the *first presented*
  candidate ~10× the uniform share whatever the order (mean p×n at position 0:
  10.3 hash / 9.8 OXIDE order / 10.5 reversed; 0.02–1.5 elsewhere).
  - Presented in OXIDE order, J2's AUROC rises to 0.714, which only
    reproduces OXIDE's rank (candidate-order leakage).
  - Reversed, it falls to 0.479.
  - With the neutral hash order it is 0.598 on S and 0.539 on C. Excluding the
    first-presented candidate leaves 0.612 on S and 0.555 on C (Codex finding
    2), so a weak content signal remains beside the position effect. It is still
    far below OXIDE's order (0.73 / 0.80), and J2 still fails on the final pack.

## 5. Final-pack results (`results/final_pack.txt`; balanced, budget 4096, A = ch0)

Paired bootstrap, 10,000 resamples, seed 0.

| arm | split | Δ gold-line cov [95 % CI] | rel. efficiency [95 % CI] | improved / unchanged / regressed | gold rescued / suppressed | non-gold tokens added |
|---|---|---|---|---|---|---:|
| R1 J1 | S | **−0.077 [−0.138, −0.016]** | **−22.7 % [−38.5, −5.7]** | 17 / 103 / 34 | 25 / 42 | 67,859 |
| R1 J1 | C | **−0.113 [−0.198, −0.030]** | **−36.3 % [−57.6, −8.6]** | 10 / 85 / 22 | 10 / 23 | 63,352 |
| R1 J2 | S | −0.021 [−0.083, +0.041] | +2.7 % [−14.0, +23.1] | 23 / 102 / 29 | 27 / 35 | 75,515 |
| R1 J2 | C | **−0.162 [−0.245, −0.082]** | **−49.0 % [−66.3, −29.2]** | 7 / 83 / 27 | 7 / 27 | 66,086 |
| R1 J3 | S | **−0.103 [−0.162, −0.046]** | **−32.2 % [−48.6, −14.1]** | 17 / 96 / 41 | 24 / 46 | 72,762 |
| R1 J3 | C | **−0.142 [−0.229, −0.056]** | **−40.9 % [−62.4, −14.3]** | 10 / 78 / 29 | 11 / 30 | 59,981 |
| R1 random scores (control) | S | −0.096 [−0.153, −0.038] | −23.6 % [−40.8, −3.5] | 14 / 103 / 37 | 17 / 41 | 80,822 |
| R1 random scores (control) | C | −0.157 [−0.236, −0.079] | −31.2 % [−55.6, −1.6] | 6 / 86 / 25 | 6 / 26 | 66,593 |
| R2 J2, τ ∈ {0.05, 0.1, 0.2, 0.3, 0.5} | S | −0.224 … −0.298 (all CIs < 0) | −3.6 % … −38.9 % | ≤14 improved, ≥64 regressed | — | — |

- Baseline A: S coverage 0.345, efficiency 0.0907, 1,377 tokens. C coverage 0.412, efficiency 0.0421, 1,561 tokens.
- Relevant tokens ÷ 4096 budget, S: A 0.039, R1 J1 0.029.
- **Every Julia arm loses coverage on both splits.**
  - J1 and J3 do no better than reordering the pool at random. J2 is worse
    than random on C.
  - The only positive point estimate in 18 per-set cells is held-out R1 J1,
    at +0.020 [−0.077, +0.118]. That is a noise-level interval.
- **R2 fails at every threshold.** J2 probabilities average about 1/15, so
  every threshold on the preregistered grid filtered out most of the pool.
  - *Post-hoc note:* that grid was not adapted to the choice-probability
    scale, which is a protocol weakness.
  - R2 was **not run** on J1 or J3 (the protocol runs R2 only on the selected
    form). *Inferred:* their candidate AUROC is ≤ 0.50 and J1's P < 0.1 bin
    has an 8.6 % gold rate against a 7.7 % base rate, so a gain is unlikely.
    This was not measured.
- Under the preregistered selection rule, the "winning" form is **J2 + R1**
  (Δ −0.021 on S). It fails on C (−0.162), and its signal is dominated by a
  position effect (§4). No formulation passes the dev screen.

### Failure patterns (R1 J1, S; `results/R1J1-S.json`)

- **Tests over-promoted:** test-role candidates get the *highest* mean J1
  (0.789) and have a 0 % gold rate.
- **Short-snippet bias:** mean J1 by snippet-token quartile is 0.86 / 0.83 /
  0.70 / 0.62, while the gold rate is 3.4 / 9.4 / 9.4 / 8.8 %. Pearson
  correlation between tokens and J1 is −0.31.
- **Probability overconfidence:** 57 % of candidates get P > 0.9, including junk.
- **Position bias (J2):** see §4.
- **No task grounding:** gold, same-file and other-file candidates score
  identically. Julia is not matching task semantics to code.
- **Regressions are hard:** three `pylint` dev tasks drop from 1.00 to 0.00.
  - Suppressed gold examples: CB `049a7048` `pylint/lint/run.py#_query_cpu`
    (OXIDE position 1), pushed out by the primary cap. CB `1397ea97`
    `EncodedFile.write` (OXIDE position 0, J1 0.031).
  - Rescues exist but are fewer: 25 on S (e.g. CB `0eecae1e`
    `pytest_configure`, J1 1.000, OXIDE position 4) against 42 suppressed.

## 6. Ablations

- Only the cheap, decisive ones were run, because no formulation passed the
  dev screen.
- **Order control (R1 with random scores):** as bad as J1 or J3 (§5). Julia's
  reordering behaves like noise.
- **J2 presentation order:** hash 0.598, OXIDE order 0.714, reverse 0.479.
  Most of the apparent signal comes from position and order leakage. A weak
  residual remains without position 0 (0.61 / 0.56).
- The task-vs-no-task and metadata-only ablations were **not run**. They are
  meant to explain *why* a gain exists, and J1 and J3 show no signal to
  explain: mean scores are equal across gold, same-file and other-file
  candidates.

## 7. Limitation: no fresh held-out evaluation

**The fresh held-out set was never built or run.** Every quality number in
this report comes from OLD development sets. Nothing here measures Julia on
unseen commits, and no claim about fresh-data quality is made.

It was not run because two preregistered gates had already failed on their
own, each strongly enough to rule out any production follow-up:

- **Dev-quality screen.** Every formulation lost final-pack coverage and did
  no better than random reordering.
- **Runtime gate.** It failed by 3.7–10× at the best CPU configuration.

Neither outcome can be reversed by a fresh test. On approval, the fresh run
was dropped rather than deferred. Detail:


The fresh set (new parent-commit corpora, ~1–2 h of indexing) was not built.

- The brief makes a gain on the final pack the deciding criterion. On the
  271-task dev screen every R1 formulation loses coverage on both splits.
  - The losses are significant on C for all three forms, and on S for J1 and
    J3. R1 J2 on S has a CI crossing 0.
  - No form beats random reordering, and every R2 threshold tested (on J2)
    loses.
- The preregistered runtime gate (median ≤ 250 ms, p95 ≤ 500 ms, no
  multi-GB footprint) fails by 3.7–10× at the best CPU configuration, at
  1.5–1.7 GB peak RSS.
- Either failure alone forces REJECT. A fresh test could only confirm a
  REJECT, not change the disposition.
- Nothing was frozen for a fresh test, and no fresh data was generated or seen.

## 8. Codex review

Read-only review after the results were frozen (`results/codex-review.txt`).
Codex reproduced R1 J1 on S (−0.0772 [−0.1382, −0.0163], −22.7 %), R1 J2 on C
(−0.1623, −49.0 %) and J1 AUROC 0.4932. It verified one R1 ordering against the
saved J1 scores, and found no task overlap between S and C and no scored
candidate missing from its pool. Julia was not retuned.

| # | sev | finding | resolution |
|---|---|---|---|
| 1 | MAJOR | the bootstrap resamples tasks, but tasks share worktrees (dev: 70 tasks / 5 worktrees). With worktree-cluster resampling, R1 J1 on S is [−0.127, +0.016]. | Accepted. S significance for J1 depends on the resampling unit. R1 J2 on C stays < 0 under clustering ([−0.224, −0.065]). Disposition unchanged: runtime alone fails. |
| 2 | MAJOR | "J2 signal is only a position artifact" overstated. Without position 0, AUROC is 0.612 / 0.555. | Corrected in §4, §6, §9. |
| 3 | MINOR | 4 CB tasks have gold references missing from their worktrees (3 files, 1 line beyond EOF). These labels come from the pre-existing `cb_gold.json`. | Disclosed. Excluding them: R1 J1 S Δ −0.0770, rel. eff −24.2 %. |
| 4 | MINOR | the R2 claim for J1/J3 was untested; "significant on every formulation" was wrong for R1 J2 on S | Corrected in §5 and §7. |
| 5 | MINOR | "extra RSS" should be "peak process RSS"; load time and import should be separate | Corrected in §1 (load 4.21 s + import 0.67 s at 6 threads). |
| 6 | NOTE | raw inputs not pinned | Added SHA-256 of tasks, gold, scores and ch0/R1 dumps to `results/SHA256SUMS`. Corpora remain machine-local. |

## 9. Disposition

**REJECT.** Four criteria from the brief each fail on their own:

- No final-pack gain.
- Efficiency falls significantly (−23 % to −49 %).
- The runtime and memory cost fails the synchronous gate.
- J2's above-chance signal is mostly a position/order effect.

OXIDE's own deterministic order (candidate AUROC 0.73–0.80) is far better
than any Julia judgment (0.46–0.60).

What this establishes:

- Julia-1 is **rejected as a synchronous OXIDE evidence judge**.
- ONNX, Rust or quantization work is **not justified**. Runtime is not the only
  failed gate: the judgments themselves are at chance (J1, J3) or dominated by
  presentation position (J2), and a faster model would reproduce them.
- This experiment provides **no evidence for a learned evidence judge at this
  seam**, i.e. reordering or filtering OXIDE's bounded post-dedup pool before
  the allocator.
- It does **not** prove that every future model or judge architecture will
  fail. It tests one 144M decision model, three formulations and two
  integration shapes on dev data only.

**Smallest justified next action:** none for Julia. Keep retrieval and the
allocator frozen. The ONNX, quantization and Rust follow-up is **not**
justified: speed cannot fix a judgment at chance.

## Reproduce

```bash
git worktree add --detach /tmp/oxide-julia-1abb3d7 1abb3d7
git -C /tmp/oxide-julia-1abb3d7 apply docs/julia-evidence-judge-eval/research.patch
CARGO_TARGET_DIR=<dir> cargo build --release -j 2 --example alloc_dump
# Julia: snapshot_download('SupersonicLabs/Julia-1', revision='a85b1273…'); pip install -e ./Julia-1 (CPU torch)
S=docs/julia-evidence-judge-eval/scripts   # run_j.sh, dev_ch0.sh, score_dev.sh, r1_dev.sh, r2_dev.sh
$S/dev_ch0.sh; $S/score_dev.sh; $S/r1_dev.sh J1 J2 J3; $S/r2_dev.sh
python3 $S/eval_cand.py cb,heldout,dev J1; python3 $S/eval_pack.py cb,heldout,dev R1J1
```

The scripts expect `~/.cache/oxide-julia-eval/` (tasks, gold, dumps, scores),
which is machine-local. Task files and corpora are those of the earlier studies.
