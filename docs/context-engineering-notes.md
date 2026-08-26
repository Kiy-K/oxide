# Context Engineering for Coding Agents — Research Notes & OXIDE Design

## Evidence base (external)

1. **Task-aware context selection improves success while shrinking tokens.**
   SWE-Pruner (SJTU/Douyin, 2026): task-guided pruning of code observations cut
   token consumption 23–54%, API cost 26–38%, *and* raised SWE-Bench Verified
   success 1.2–1.4 pp while reducing interaction rounds ~18–25%. Focused
   context → more decisive agent behavior. This is the strongest direct support
   for task-aware `oxide context --task`.
2. **Lost in the middle / U-shaped attention** (Stanford/MIT line of work):
   models attend most to the beginning and end of context; middle content is
   accessed less reliably; performance degrades 15–47% as relevant content is
   buried. ⇒ Order packs so primary targets lead and high-value summaries/tests
   close the pack.
3. **Explicit budgets reduce waste without accuracy loss** (TALE framework):
   stating a token budget cut CoT cost ~67% at competitive accuracy. Budget
   allocation guidance puts code generation around 1–4k tokens of context.
   ⇒ Configurable `--budget-tokens` with a sane default (~4k).
4. **Hybrid retrieval by query type**: Long Code Arena study (2025): sparse
   BM25 wins PL→PL (exact symbol) retrieval; dense encoders win NL→PL (issue/
   description) retrieval at higher latency. ⇒ Keep lexical+semantic fusion;
   dense provider materially upgrades the NL→PL half.
5. **Redundancy dilutes**: ROI-weighted views rank "redundant explanations /
   repeated code" as low-value tokens; dedup and overlap merging are standard.
6. **Measure outcomes, not calls**: cost-per-*successful-outcome* exposes waste
   that per-call metrics hide; tool-call count / interaction rounds are the
   agent-efficiency proxies.

## Design decisions for OXIDE

- **Model independence kept**: embeddings stay behind `EmbeddingProvider`.
  New `HttpEmbedder` speaks the OpenAI-compatible `/v1/embeddings` protocol —
  works against llama.cpp's server (default here) or any compatible endpoint.
  OXIDE never links model code; it POSTs JSON.
- **Qwen3 protocol**: queries get an instruction prefix
  (`Instruct: {task}\nQuery: {q}`); documents are embedded raw. The task flag
  feeds both the instruction prefix and lexical/BM25 query — one mechanism,
  task-aware end to end.
- **Index compatibility**: index meta records provider name+dim; switching
  providers triggers full re-embed on next index run (vectors are provider-
  specific; hashes still prevent redundant re-embeds of unchanged symbols when
  the provider is unchanged). Incremental behavior otherwise untouched.
- **Context packs** (`oxide context --task ... --budget-tokens N`):
  hybrid seeds → structural expansion → dedup/same-file merge → greedy
  budget packing ranked by score density → ordering: primaries first,
  dependencies next, tests last (U-shape: tests also give a natural closing
  signal). Every item carries machine-readable reasons; omissions are listed
  with why. Token estimate = chars/4 heuristic, documented as an estimate.
- **Evaluation**: same headless coding agent (`opencode run`, same model) on
  identical bug-fix/implement tasks under 4 conditions — stock tools,
  vector-only context, hybrid context, budgeted context pack. Outcomes scored
  by running each repo's verification script. Metrics: solve rate, tests
  passed, injected context size, wall latency, edit footprint, tool-call
  proxy (shell commands executed), relevant-symbol recall vs ground truth.

## Evaluation pivot: ContextBench (official)

Per user direction, SWE-bench is out (training-data contamination) and the
handcrafted task suite is demoted to harness smoke-testing. Primary evidence now
comes from **ContextBench** (Li et al., NJU/UCL, arXiv:2602.05892, Apache-2.0):
1,136 issue-resolution tasks with human-annotated gold contexts (file + block
line ranges), including 512 Python and 119 TypeScript tasks — exactly OXIDE's
language scope. Metrics are computed with their evaluator code
(`contextbench.metrics.compute_granularity_metrics`) so numbers use official
definitions: coverage (recall) and precision at file/symbol/span/line
granularity, plus tokens consumed per condition.

Conditions compared per task (issue text drives retrieval):
1. lexical-only (`oxide search --mode lexical`)
2. vector-only (`--mode semantic`, Qwen3-Embedding-0.6B via llama.cpp)
3. hybrid (default fusion)
4. budgeted pack (`oxide context --task ... --budget-tokens 4096`)

A second tier re-runs a small subset through the same headless coding agent
(opencode, fixed model) under stock-tools vs injected-context conditions,
measuring tool-call count, wall time, unnecessary edits, and gold-file
utilization of the final diff.

## ContextBench results (21-task pinned sample, post-allocator-rework)

Instance IDs pinned in `eval-agent/results/tier_a_instances.txt` (upstream
dataset drift makes limit_per_repo sampling non-reproducible). Idle-machine
run; eval numbers degrade under load because failed embedding requests are
silently skipped. Mean over tasks; R=coverage(recall), P=precision, F1
harmonic; tokens = est.

| condition | file R/P/F1     | line R/P/F1     | tokens |
|-----------|-----------------|-----------------|--------|
| lexical   | .607/.195/.295  | .555/.026/.049  | 3106   |
| vec       | .631/.282/.390  | .385/.163/.229  | **1508**|
| hybrid    | .670/.264/.378  | .547/.042/.078  | 2780   |
| budgeted  | **.766**/.248/.374 | .422/.058/.102 | 1944   |

Reading (honest): budgeted reaches hybrid-level file F1 at 30% fewer tokens
and wins line/symbol F1 — the allocator rework traded tail recall for
precision and budget discipline. vec-only keeps the best precision per token.
N is small (21); treat as directional. Historical pre-rework snapshot
(20 tasks, small-repo subset: hybrid .354 / budgeted .238 @ 4087 tok) is
preserved in git history at this file's earlier revisions.

## Embedder resource profile (laptop)

Measured on this machine (16C CPU, no GPU):

| config                        | RAM    | short-text throughput | quality gate      |
|-------------------------------|--------|----------------------:|-------------------|
| Q8_0, threads=16, ub=8192     | 4.7 GB | 14.2/s                | hybrid R@5=.909   |
| **Q8_0, threads=8, ub=2048**  | **0.3 GB** | 9-16/s            | hybrid R@5=.909   |
| community Q4_K_M              | n/a    | pathological (<1/s)   | rejected          |

The win was configuration, not quantization: `--ub 2048 --parallel 1` cuts
resident memory ~94%, bounded nice'd threads keep the laptop responsive at a
modest throughput cost, and retrieval quality is bit-identical on the committed
benchmark. Manage with `scripts/embedder.sh start|stop|status` (`stop` frees
everything when idle). Third-party Q4_K_M quants showed broken batching here;
official repo only ships Q8_0/f16.

## Tier B: same-agent comparison (4 tasks × 4 conditions, opencode headless)

| condition | gold-file use | unnecessary edits | wall  | injected tokens |
|-----------|--------------:|------------------:|------:|----------------:|
| stock     | 0.67          | 1.00              | 348 s | 0               |
| vec       | 0.67          | 1.00              | 422 s | 1032            |
| hybrid    | 0.67          | **0.75**          | **290 s** | 2594        |
| budgeted  | 0.67          | 1.00              | 360 s | 2990            |

Honest finding: on these (easier) small-repo tasks, injected context did not
change whether the agent edited gold files — all conditions matched — and
**no context beat stock on outcomes**, echoing ContextBench's "scaffolding
yields marginal gains" result. Hybrid was fastest and produced the fewest
unnecessary edits. Vector-only was *slower* than stock. Claimed benefits of
the context layer are therefore grounded in Tier A retrieval quality
(coverage/precision/tokens), not end-to-end agent gains, at this sample size.
