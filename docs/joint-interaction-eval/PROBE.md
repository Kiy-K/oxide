# Tiny cross-encoder probe at the selection seam (issue #39)

Frozen before any score is computed. Its sha256 is in `PROBE.sha256`.

## 1. Question

Does a tiny, production-shaped cross-encoder that reads the query and the
candidate code **together** order OXIDE's fused top-50 better than the current
fused order, especially inside fused ranks 6–16 (where #32 found fused order
near chance), at a CPU cost that could ship?

A negative result closes the **tiny-reranker** direction only. It does not show
that joint query↔code interaction carries no signal in general.

History: two earlier attempts with Qwen3-Reranker-0.6B made the laptop
unusable three times. They are abandoned and none of their scores are used.

## 2. Reranker (fixed)

- `cross-encoder/ms-marco-MiniLM-L6-v2` @ `233902d25c440f23af6f7d6e94d2946bac0bee0a`
  (22.7 M parameters, 91 MB). It is a general web-passage reranker, not
  code-tuned, and nothing is fitted.
- Runtime: `transformers`, fp32, CPU, 2 torch threads.
- Input pair: (`Q`, `D`), with `Q` = the task query truncated to its first 128
  tokens and `D` = `{path}\n{qualified name}\n{source lines of the symbol span}`.
  Truncation is `only_second`, at 512 tokens total.
- Score = the single relevance logit.

## 3. Universe, labels, sample

- `results/cands.jsonl.gz`: fused ranks 1–50 per task from the committed
  `docs/ranking-fusion-eval/results/` dumps, with source read via `git show` at
  the indexed revision (`scripts/prep.py`). Every non-text field was rebuilt
  from the committed dumps and the pinned ContextBench parquet (sha256
  `2f56535b…`) and matches on 4300/4300 rows. Source text matched fresh clones
  on all 800 rows checked.
- Labels: heldout positive iff the key is in the gold keys. cb positive iff the
  candidate is a non-module symbol whose span overlaps a ContextBench gold line
  (#32 §4 rule).
- Sample (`scripts/probe_tasks.py`, seed 39, round-robin over repos, eligible
  = ≥ 1 positive and ≥ 1 negative): **cb 6 tasks** (primary; base commit,
  leakage-free) and **heldout 4 tasks** (secondary; the post-commit text can
  leak the change and favors any joint reader). This is 10 × 50 = 500 pairs.

## 4. Safety caps (fixed)

There is one process. It runs in `systemd-run --user --scope` with
`MemoryMax=1G`, `MemorySwapMax=0` and `CPUQuota=200%`, under `nice -n 10`,
with a 10-minute timeout. Batch size is chosen by profiling one non-probe task
(scores discarded, never compared with labels). If a cap trips, stop; caps are
not raised.

## 5. Scorers and metrics

- `fused_order` (−fused rank), `random` (200 seeded draws), `X` (reranker
  logit), and `X+F` = 1/(60 + rank_X) + 1/(60 + fused rank), with rank_X ties
  broken by fused rank.
- These use #32's `common.py`: within-task macro AUROC, pairwise accuracy,
  R@5, R@16, and paired task-bootstrap Δ vs fused (2000 resamples, seed 32).
  AUROC inside B2 (fused 6–16) and per-task Δ AUROC are descriptive.
- Cost: load time, latency per task (50 pairs), per pair, and peak RSS.

## 6. Decision rule (fixed)

**INVESTIGATE** iff some G ∈ {X, X+F} meets all of:

1. cb: Δ macro AUROC(G − fused) ≥ +0.05 with 95 % CI lower bound > 0;
2. cb: Δ R@16 ≥ 0;
3. heldout: Δ macro AUROC > 0;
4. cost: peak RSS ≤ 1 GB (no cap tripped) and median latency ≤ 2 s per 50-pair
   task at 2 threads.

Otherwise **CLOSE** the tiny-reranker direction. The full 86-task run is not
done under CLOSE, and under INVESTIGATE it needs a separate go-ahead.

n = 6 cannot confirm a lift. The rule only asks whether the signal is clear
enough to justify paying for confirmation.
