# Tiny cross-encoder probe at the selection seam (#39): REJECT

Preregistration: `PROBE.md` (sha256 in `PROBE.sha256`, frozen before scoring).
Sample: 6 cb + 4 heldout tasks, fused top-50 each (500 pairs).

## Verdict

**REJECT: off-the-shelf tiny general-purpose cross-encoder reranking.**

- Model: `cross-encoder/ms-marco-MiniLM-L6-v2`, 22.7 M params. It is trained on
  web-search passages (MS MARCO) and is not code-tuned. fp32 CPU, 2 threads.
- 512-token pair limit; the query is cut to 128 tokens and the code fills the
  rest.
- ContextBench: the point estimate is positive (Δ AUROC +0.112), but the 95 %
  CI crosses zero (−0.087, +0.315).
- Transfer is inconsistent per task: 3 of 6 cb tasks gain and 3 lose.
- Held-out is essentially flat (+0.010).
- Fused ranks 6–16 did not improve (cb B2 Δ −0.06).
- Cost: a median of 3.4 s per query (50 pairs) exceeds the 2 s budget.
- Therefore the preregistered probe fails (PROBE §6 criteria 1 and 4), and the
  full 86-task run is not done.

**Scope.** This does not disprove joint query↔code interaction. It also does
not rule out tiny rerankers in general, or code-specific rerankers. Neither
code-tuned nor larger joint readers were tested; the 0.6B attempt was not
operable on this machine (see History).

## Results (measured)

cb, all (n = 6 tasks, 25 positives; primary, leakage-free):

| scorer | AUROC [95 % CI] | Δ vs fused [CI] | pairwise | R@5 | R@16 |
|---|---|---|---:|---:|---:|
| fused_order | 0.577 [0.394, 0.725] | — | 0.595 | 0.220 | 0.399 |
| random | 0.497 | −0.080 | 0.497 | — | — |
| X | 0.689 [0.581, 0.787] | +0.112 [−0.087, +0.315] | 0.671 | 0.220 | 0.490 |
| X+F | 0.657 [0.542, 0.764] | +0.080 [−0.032, +0.188] | 0.653 | 0.220 | 0.458 |

- Per-task Δ AUROC (X): −0.06, +0.53, +0.19, −0.09, +0.30, −0.20. Three tasks
  gain and three lose, so the mean is driven by two tasks.
- **B2 (fused 6–16)**, n = 3: X 0.467 vs fused 0.526 (Δ −0.06). There is no
  gain where #32 located the problem.
- R@5 is unchanged (0.220). The R@16 gain (+0.09) comes from promotion out of
  B3, not from better ordering inside 6–16.

heldout, all (n = 4, 7 positives; leakage favors joint readers): fused is
already 0.933. Δ X = +0.010 [−0.016, +0.036], Δ X+F = +0.022 [−0.008, +0.051].
In B2 (n = 2) Δ = +0.30, which is too small a sample to read.

Cost (2 threads, batch 8, length-sorted): load 0.5 s; **median 3.4 s per
50-pair task** (max 4.1 s, 0.067 s/pair, ~420 input tokens/pair); peak RSS
688 MB (cgroup peak 493 MB). This misses the ≤ 2 s budget, so a per-query
rerank of the top-50 is too slow to ship as is.

Criteria (PROBE §6), identical for X and X+F: 1 ✗, 2 ✓, 3 ✓, 4 ✗.

## What would reopen it (inferred, not tested)

A cb-wide signal like the +0.11 point estimate would need a code-tuned small
reranker and a cheaper universe (e.g. rerank only fused 1–16) to be worth a
look. Any reopening needs its own preregistered probe.

## History

The first #39 attempts used Qwen3-Reranker-0.6B, run first via llama.cpp and
then via torch. They made the laptop unusable three times: the first two with
concurrent model servers, the third with torch at 6 threads under a 4 GB RAM
cap but no CPU cap. Their scores were discarded without evaluation. This probe runs every
model step under `MemoryMax=1G`, `MemorySwapMax=0` and `CPUQuota=200%`.

## Files

- `scripts/prep.py`: builds `results/cands.jsonl.gz` (fused top-50 with source
  and labels). Needs the task repo clones and the ContextBench parquet.
- `scripts/probe_tasks.py`: the seeded sample.
- `scripts/score.py`: scores the reranker into `results/scores-probe.jsonl`.
- `scripts/probe_eval.py`: metrics and verdict, writing `results/probe-results.json`.

```sh
systemd-run --user --scope -p MemoryMax=1G -p MemorySwapMax=0 -p CPUQuota=200% \
  nice -n 10 python scripts/score.py probe
python scripts/probe_eval.py
```
