# Reranking experiment — verdict: reject for v0.1

Status: **rejected**. Neither evaluated candidate (BGE-reranker-v2-m3,
MS MARCO MiniLM-L6-v2) gives a Pareto improvement over the frozen
no-reranker baseline (`docs/retrieval-ceiling.md`) on the pinned Tier A
set. Qwen3-Reranker-0.6B is **inconclusive on CPU** — abandoned before
producing a single score, not rejected on quality (see "Models" below).

This is preserved as negative evidence, not live infrastructure.
`context.rs::rerank_candidates` has been reverted to the pre-experiment
no-op stub — the env-gated `OXIDE_RERANK_SCORES`/`OXIDE_RERANK_MODE`
scoring implementation this report describes existed only to run this
experiment and was removed once the verdict was reached, per this repo's
"no unrequested abstractions" / "deletion over addition" convention: a
rejected feature doesn't earn a permanent branch in production code. The
hook's doc comment points back here for anyone re-attempting this.
`OXIDE_DEBUG_DUMP_KEPT` (the candidate-pool debug dump used to make the
ceiling measurement faithful) was kept — it has general
evaluation/debugging value independent of reranking, and negligible
production complexity (one env-gated `if let` around a `serde_json`
write). Nothing here was ever enabled by default, and no MCP/API contract
changed either before or after the revert.

Raw evidence (kept, ~1.4 MB total): `results/ceiling.jsonl`,
`results/results.jsonl`, `results/bundles/*.json`, `results/scores/*/*.json`,
`results/scorer_timing.json`. The evaluation scripts
(`scripts/agent_eval/reranker_ceiling.py`, `reranker_eval.py`,
`reranker_score.py`, `summarize_reranker.py`) are also kept, unmodified in
behavior — they talk to `oxide` entirely through its existing CLI/env-var
surface (`--retrieval-mode`, `OXIDE_RERANK_SCORES`/`MODE`,
`OXIDE_DEBUG_DUMP_KEPT`) and so still reproduce the numbers below if
`rerank_candidates` is ever reinstated for a future rerun. Reproduce the
ceiling measurement (still fully live, no code dependency on the removed
scoring path) with `scripts/agent_eval/reranker_ceiling.py`; the
score/rerank measurement needs the removed implementation restored first
(needs `OXIDE_EMBED_URL` for the pinned `qwen3-Q8_0` embedder and
`OXIDE_RERANK_VENV` pointing at a venv with the scoring deps — see
`scripts/agent_eval/reranker_score.py`).

## Setup

- Pinned Tier A set: the same 21 instances as `docs/retrieval-ceiling.md`
  (`eval-agent/results/tier_a_instances.txt`).
- Embedder frozen at `qwen3-Q8_0` (llama.cpp, `scripts/embedder.sh`) —
  same as the canonical baseline. Fusion, relation expansion, retrieval
  weights, and the 4096-token context budget were untouched.
- `--retrieval-mode quality` on every `oxide search`/`oxide context` call.
- Candidates are evidence bundles, not bare names: path, qualified name,
  bounded source (the pack's own snippet), and relation context (the
  pack's own `reasons`, e.g. `calls←foo`) — see `bundle_text()` in
  `scripts/agent_eval/reranker_eval.py`.

## Phase 1 — candidate ceiling (the gate)

Two ceilings, because they answer different questions and neither is what
a huge token budget alone gives you (a huge budget only defeats the "over
token budget" drop in `context.rs`'s greedy-fill loop — the per-file and
role diversity caps are budget-independent and still remove pool members
regardless of budget size). The real pre-rerank pool (`kept`, right before
`rerank_candidates`) was captured exactly via a new env-gated debug dump,
`OXIDE_DEBUG_DUMP_KEPT` (context.rs), added specifically for this reason.

| metric | result |
|---|---|
| discovery R@5 (`search --limit 50`) | 17/21 (81%) |
| discovery R@10 | 19/21 (90%) |
| discovery R@20 | 19/21 (90%) |
| discovery R@50 | 21/21 (100%) |
| **rerankable ceiling** (gold anywhere in the real `kept` pool) | **19/21 (90%)** |
| avg `kept` pool size | 13.1 items |
| rank of first gold item in `kept`, when present (19 tasks) | eleven 0s, then 1,1,1,2,3,3,3,6 |

**Gate verdict: proceed.** Evidence is not generally absent — 90% of
tasks have gold reachable in the exact pool a reranker would see. But this
also sets expectations: gold is *already ranked first* in 11/19 of those
tasks before any reranking. The reorderable opportunity is real but
narrow — roughly 8 of 21 tasks, not a wholesale reshuffle. The other 2/21
(discovery rank 18+ and 21+, both beyond the ~16-item seed cap and
unrescued by bounded structural expansion) are a discovery/allocation gap
no reranker can fix — consistent with `docs/retrieval-ceiling.md`'s
"route loss" finding on the same set.

## Models

| model | params | license | arch | disk |
|---|---|---|---|---|
| BAAI/bge-reranker-v2-m3 | 568M | Apache-2.0 | XLM-RoBERTa-large classifier cross-encoder, multilingual | 2.2 GB (fp32 safetensors) |
| cross-encoder/ms-marco-MiniLM-L6-v2 | 22.7M | Apache-2.0 | 6-layer MiniLM classifier cross-encoder, English, MS MARCO-trained | 88 MB (ONNX) |

BGE-reranker-v2-m3 was the task's named primary candidate. The second
candidate changed mid-run: **Qwen3-Reranker-0.6B** (Apache-2.0, decoder/
causal-LM yes-no-logit scoring) was tried first per the original shortlist,
but under plain CPU fp32 `transformers`/`torch` it was both far slower
than the classifier rerankers and hung/crashed mid-batch on this hardware
— not a realistic ask of a user without a dedicated GPU, and the whole
point of the second slot was a *practical* alternative. **Qwen3-Reranker
was abandoned on operability before it produced a single score** — it was
never quality-tested and does not appear in the Verdict table below; do
not read its absence as a rejection on merit. Replaced with MiniLM-L6, run
via `fastembed`/ONNX Runtime instead of torch: still a
genuinely different point on the Pareto frontier from BGE-v2-m3 (25x
fewer params, different lineage, classic MS MARCO reference model,
English-only vs. multilingual), and one that actually runs fast on a CPU.
Both final models are Apache-2.0 — correcting the earlier note that only
**Jina** (`jina-reranker-v2-base-multilingual`, CC-BY-NC-4.0, kept only as
prior reference evidence per the task) was non-permissive.

## Two integration modes (`context.rs::rerank_candidates`)

- **`transplant`** (order-only): permute candidates by reranker score,
  then reassign them the *original* fused score values in that new order.
  The relevance floor sees the same multiset of scores as baseline — same
  count clears it, only *which* candidates can change. This isolates
  ordering from magnitude.
- **`raw`** (score-rewrite): overwrite `Candidate.score` with the
  reranker's own score, unscaled. The floor is anchored to the original
  top *seed* score (untouched, still on the fused BM25/cosine scale) — so
  comparing rewritten, arbitrarily-scaled scores against it is close to
  meaningless. This is the closest reproduction of how the earlier Jina
  regression likely happened (3 relevant symbols / 383 tokens collapsing
  to 1 / 36 tokens).

## Phase 2 — results (21 tasks, mean per task unless noted)

| arm | file F1 | symbol F1 | line F1 | tokens | items | relevant items | relevant tokens |
|---|---|---|---|---|---|---|---|
| baseline (no rerank) | 0.328 | 0.114 | 0.099 | 1756 | 7.0 | 1.0 | 316 |
| bge-v2-m3 : raw | 0.323 | 0.119 | 0.109 | 1809 | 7.0 | 1.0 | 332 |
| bge-v2-m3 : transplant | 0.330 | 0.119 | 0.109 | 1811 | 7.0 | 1.0 | 332 |
| minilm-l6 : raw | 0.340 | 0.097 | 0.082 | 1278 | 5.0 | 0.8 | 244 |
| minilm-l6 : transplant | 0.327 | 0.109 | 0.100 | 1802 | 7.0 | 1.0 | 316 |

**Quality is flat.** Both `transplant` arms sit within noise of baseline on
every granularity — consistent with the ceiling finding that gold is
usually already top-ranked pre-rerank, so there's little for a reranker to
fix. Neither model earns its cost on quality alone.

**Complementary-evidence loss — the Jina signature reproduces, but only
in `raw` mode:**

| arm | lost | tasks affected | gained |
|---|---|---|---|
| bge-v2-m3 : raw | 0 | 0/21 | 1 |
| bge-v2-m3 : transplant | 0 | 0/21 | 1 |
| minilm-l6 : raw | **8** | **6/21** | 3 |
| minilm-l6 : transplant | 0 | 0/21 | 0 |

`minilm-l6:raw` drops gold-overlapping symbols baseline kept in 6 of 21
tasks (e.g. `pylint/lint/run.py#_cpu_count` and `#_query_cpu` both lost in
one task) — the same shape as the original Jina incident, now reproduced
under controlled conditions with a different reranker. This confirms the
mechanism is the floor/score-scale interaction described above, not
something specific to Jina. Notably, `bge-v2-m3:raw` does *not* show this
collapse — BGE's score card documents a sigmoid-mappable [0,1] output,
which apparently lands close enough to the fused scale to avoid disaster
here (its scores are almost always well above the fused-scale floor
threshold, so the floor stops discriminating rather than over-filtering);
MiniLM's raw classifier logits (seen as large as ±11 in testing) do not.
**Conclusion: `raw` score-rewriting is reranker-scale-dependent and unsafe
as a general integration mode; `transplant` avoids the failure class by
construction, at the cost of also avoiding most of the (already narrow)
potential upside.**

*Verification note:* `bge-v2-m3:raw` producing near-baseline aggregates
while genuinely reordering (e.g. one task's top-5 by fused score reorders
completely under BGE's raw scores — `Nominal` moves rank 2→1,
`_adjust_cat_axis` re-enters the top 5) is surprising enough that it was
worth confirming the reranker score was actually applied rather than the
lookup silently missing (an absolute- vs. relative-path bug in
`OXIDE_RERANK_SCORES` resolution — `oxide` runs with `cwd` set to the
checked-out task repo, not the OXIDE project root — would produce exactly
this false-negative "no effect" signature). Spot-checked directly: with
`OXIDE_RERANK_MODE=raw`, the pack's per-item `score` field matches the
reranker's own score bit-for-bit, confirming the overwrite is real and the
near-baseline aggregate is a genuine result, not a silent no-op.

`transplant` composition stability: the relevant-evidence set changed in
1/21 tasks for bge-v2-m3 and 0/21 for minilm-l6 — reordering happened
within role/floor buckets but essentially never changed *what* made the
final pack, matching the "already ranked first" ceiling finding.

## Latency, RAM, and footprint (CPU only — no GPU used or required)

| model | load (one-time) | per-query scoring (mean, ~13 candidates) | peak RSS during a 21-task run |
|---|---|---|---|
| bge-v2-m3 (torch fp32) | 13.7s | **36.0s** (27–43s range) | 2.86 GB |
| minilm-l6 (ONNX Runtime) | 0.5s | **0.6s** (0.4–1.4s range) | 1.18 GB |

This is decisive on its own, independent of the quality results:
**BGE-reranker-v2-m3 costs ~36 seconds of CPU time per `oxide context`
call.** `oxide context`/`oxide search` run fully synchronously with no
tokio runtime (see AGENTS.md) — a 36-second stall is not acceptable
latency for a CLI tool an agent calls inline, and most OXIDE users do not
have a dedicated GPU to make it fast. MiniLM-L6 is practical (sub-second,
~230MB working set outside the one-time ~1GB ONNX Runtime baseline) but
buys no measured quality improvement over baseline in the only integration
mode (`transplant`) that doesn't risk the complementary-evidence collapse.

## Verdict

| model | verdict | why |
|---|---|---|
| bge-reranker-v2-m3 | **reject** | Quality flat vs. baseline; ~36s/query CPU latency and 2.2GB model make it impractical for a synchronous CLI tool regardless of quality. |
| ms-marco-MiniLM-L6-v2 | **reject** | Fast and light enough to actually ship, but zero measured quality gain in the safe (`transplant`) integration mode; the fast, naive (`raw`) mode reproduces the Jina-style evidence-loss failure. |
| Qwen3-Reranker-0.6B | **inconclusive on CPU — not evaluated on quality** | Hung/crashed under plain CPU fp32 `transformers`/`torch` before producing a single score. Never compared against baseline; its absence from the two rows above is not a rejection. A GPU run, or a proper CPU-optimized (quantized/ONNX) path, could still answer the quality question this experiment didn't reach. |
| jina-reranker-v2-base-multilingual | **reject** (prior evidence, not rerun) | Non-permissive license (CC-BY-NC-4.0) in addition to the original complementary-evidence collapse. |

No evaluated candidate clears the bar. The root cause isn't a bad reranker
choice — the ceiling gate shows the baseline candidate pool is *already*
well ordered for this benchmark (gold at rank 0 in 11/19 tasks with any
gold present at all), so there is little ordering upside left to capture.

**Durable integration warning, independent of the "reject" verdict above**
(now codified as RET-005 in `docs/review/retrieval-and-config.md`): a
reranker's output must never overwrite a calibrated fused score unless
every downstream consumer of that score is recalibrated to the new score
space. Here, that consumer is `build_context`'s relevance floor, anchored
to the original top *seed* score on the fused BM25/cosine scale — `raw`
mode empirically reproduced the original Jina complementary-evidence
collapse with a completely different reranker (MiniLM, 8 gold-relevant
symbols lost across 6/21 tasks) purely from comparing an unrecalibrated
score against that floor. This is a property of *any* future reranker
integrated by naive score overwrite, not a defect in Jina or MiniLM
specifically, and it holds regardless of whether a future model clears the
quality bar this experiment didn't.

Nothing is enabled by default, before or after this report's code was
reverted. `context.rs::rerank_candidates` is back to its pre-experiment
no-op; only `OXIDE_DEBUG_DUMP_KEPT` (general candidate-pool diagnostic, no
reranking dependency) was kept, pinned by
`context::tests::debug_dump_kept_writes_exactly_when_env_var_set`. A
future rerun — if the candidate pool's ordering quality regresses, a
cheaper/better reranker becomes worth testing, or Qwen3-Reranker gets a
real CPU-viable path — restores the scoring implementation described
above, informed by this report and bound by RET-005.
