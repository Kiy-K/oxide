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
