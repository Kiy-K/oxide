# Issue #15 — local Laya as a task-aware evidence reranker

Status: **research only. Nothing in `src/`, the defaults or the dependencies
changed.** Hypothesis A is **rejected on operational cost** for OXIDE's
local CPU request path: the pre-registered hard stop held in every official
configuration tried. Its quality was **not measured**, as the protocol
requires (stop early). Hypothesis B (entity alignment) was **not run**.
Protocol, pins and deviations: [`protocol.md`](protocol.md), written before
any Laya output was produced.

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

## 4. Quality: not measured

As pre-registered, the dev screen (Q1) and the ContextBench candidate and
pack comparison (Q2) were not run after the hard stop. Everything they
need is prepared and validated, so a future run can start directly:
- shortlist inputs: `results/inputs/`, built only from query text and
  candidate code, never gold;
- the scorer (`scripts/laya_score.py score`);
- the dev evaluator (`scripts/evaluate.py`);
- the exact allocation replay (`scripts/oxide_replay.py`).

One piece of evidence bears on quality, and it is not an OXIDE
measurement: Laya's own figure for passage relevance, a task close to
this one and in-distribution for it, is 0.63–0.66 accuracy.

## 5. Dispositions

| hypothesis | disposition | basis |
| --- | --- | --- |
| **A. Task-aware evidence reranking** (local CPU, official Laya) | **Reject (cost), for the local synchronous CPU path. Quality: insufficient evidence** | Stopped at the gate O hard stop in every official configuration (best p50 18.1 s, best mean 16.1 s per shortlist vs 5 s; 2.1–2.8 GB vs 83 MB). The protocol names only "stop" for this case, so the mapping to "reject (cost)" is recorded as `protocol.md` §9.4. It is not a quality rejection. |
| **B. Entity / relationship alignment** | **Not run: insufficient labeled evidence** | No independently labeled identity / related-but-distinct / nonmatch pair set exists. Building one is its own budgeted task (issue #15 §4). A failed A is not a reason to run B. |

No production code, schema, graph, symbol ID, default or dependency was
touched. The Laya venv, checkpoints and ONNX export live under
`~/.cache/oxide-laya-eval/`, outside the repository.

## 6. Smallest justified next action

**Close A for the synchronous local path.** Re-opening it on CPU would
need a measured *sustained* ≥ 1 TFLOPS fp32 path, or a smaller checkpoint
of validated quality. The best software configuration here is 3–4× short,
and the one that gets closer (int8) is no longer the same model.

A quality screen would only be informative if a deployment exists where
~15 s per query or a GPU is acceptable. One example is an offline
evaluation judge that labels candidates, which is a different use from
request-path reranking. In that case, the cheapest discriminating step is
the prepared dev screen: run `laya_score.py score` on
`results/inputs/dev-*-shortlists.jsonl`. There is no code change.

- **Size:** 2,440 states (61 tasks × 20 candidates × 2 regimes).
- **Scoped version:** one checkpoint, one question, about 55 min of CPU.
- **Full pre-registered screen:** 3 questions × 2 checkpoints, about 5–7 h.

Separately, the reasons A was proposed are still open:
- route loss on description-style queries;
- allocation caps.

They belong to the semantic-quality and allocator tracks (roadmap #9), not
to a learned reranker.

## 7. Limitations

- The dev (70) and held-out (65) dumps predate #14 and their indexes no
  longer exist. Their validity at the pin is inferred from the CB
  byte-identity and #14's oracle test, not re-verified (`protocol.md` §2).
  They were not used for any result here.
- The sweep used 3 shortlists × 2 timed calls per configuration.
  Differences within about 10% (for example 18.1 s vs 19.6 s) are noise at
  that sample size. The conclusion rests on the gap to the 5 s limit,
  which is 3× or more.
- Measured on one laptop CPU without a GPU. Laya's published GPU figure
  (~33 ms/question on a T4) would change the operational picture. That
  is not OXIDE's deployment target.
- ONNX was exported with upstream's script at upstream HEAD `4066d5d5` and
  run through `laya.onnx_agent` from the PyPI 0.3.20 package. That agent
  has no batch API, and ONNX Runtime chose its own thread count inside the
  6-CPU mask. A tuned, batched ONNX path could close some of the gap.
  Nothing measured here indicates it reaches 5 s.
- The §6.5 model sanity controls (relevant vs. unrelated pair, label swap)
  were not run, because no quality stage ran.
- The sweep's irregularities are listed in `protocol.md` §9.2: a manual
  bf16 abort, an int8 crash and rerun, and rows from a different harness.

## Reproduce

```
# baseline (pinned HEAD):  results/baseline/run_baseline.py  (+ examples/fusion_dump, oxide eval)
python3 scripts/prep.py cb <baseline_dir> results/inputs/cb-shortlists.jsonl
python3 scripts/prep.py dev plain|masked results/inputs/dev-<regime>-shortlists.jsonl
~/.cache/oxide-laya-eval/venv/bin/python -I scripts/laya_score.py ops <ckpt> results/inputs/cb-shortlists.jsonl out.json 6
results/ops/sweep/run_sweep.sh            # CPU sweep; int8 rerun: scripts/cpu_sweep.py torch-int8 ...
python3 scripts/evaluate.py parity <dir with extracted results/baseline/kept-and-packs.tar.gz>
~/.cache/oxide-laya-eval/venv/bin/python -I scripts/check_truncation.py <ckpt> results/inputs/cb-shortlists.jsonl
```
