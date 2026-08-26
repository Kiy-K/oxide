# RESUME: context-pack allocator rework — allocator DONE, evals finishing

## State (2026-08-25)

## FINAL RESULTS (2026-08-25, all gates run)
- Clean Tier A (idle machine, 21 pinned tasks, summarize_cb.py):
  budgeted file-F1 .374 vs hybrid .378 (summarize) / .338 vs .349
  (mean-per-task) — PARITY WITHIN NOISE, NOT A WIN. line-F1 .102 vs .078
  (win), symbol-F1 .111 vs .083 (win), tokens 1944 vs 2780 (-30%).
  Old packer baseline: .236/.083 @ 4087.
- Tier B (16 agent runs): budgeted gold_used 1.00 (= best), bad_edits 0.75
  (best), wall 311s (fastest), ctx 1349 tok (~half of hybrid).
- CAUTION: eval numbers degrade under concurrent load — the embedder drops
  requests and the indexer silently skips failed vectors. First Tier A
  attempt (written under load) is archived as
  cb_results.jsonl.bak-load-degraded (.247 file-F1); always rerun evals on
  an idle machine before trusting them.

Older paused-state note kept below for provenance.

**Allocator rework complete and verified. All acceptance gates met except the
two long-running agent-eval reruns (Tier A fresh run was in flight; Tier B not
yet rerun).**

### Final allocator design (src/context.rs)
- Per-item token cap 350 + query-centered windowing (`render_snippet`):
  whole body if it fits, else window around lines matching query terms,
  head fallback. Shrink-to-fit halves the cap until the item fits budget —
  tiny junk can no longer displace a large primary.
- Caps: `EXPANSION_PER_SEED=2`, `EXPANSION_TOTAL=2`, `MAX_PRIMARIES=5`,
  `MAX_TESTS=1`, `MAX_PER_FILE=2` (top-ranked candidate exempt).
- Relevance floor 0.15×top seed (`split_below_floor`, keeps everything when
  nothing survives).
- Modules: same-file-concrete subsumption only (original rule). Orphan
  modules stay direct hits under MAX_PRIMARIES — they CAN be gold
  (pytest skipping task proved it); blanket-dropping them loses gold.
- Role ordering, flattened JSON contract, explicit omission reasons kept.

### Measured results (pinned Tier A 21-task set, quick_eval.py + full diag)
| metric            | hybrid | budgeted (new) | budgeted (old) |
|-------------------|--------|----------------|----------------|
| file-F1           | .346   | **.353**       | .236           |
| line-F1           | .072   | **.091**       | .083/.060      |
| tokens            | ~2474  | **~1712**      | 4087           |
| gold lost by pack | —      | **0**          | many           |

Pareto dominance achieved. The single remaining "loss" row in
pack_diag_final.log is NOT_IN_CANDIDATES (hybrid never retrieved it either).

### Tuning evidence trail (don't re-litigate)
- Embedding scores cluster within ~15% → relative floors can't separate
  ranks; hard caps are the only effective cut (probe logs:
  /tmp/opencode/pack_diag_v2.log, pack_diag_final.log).
- Blanket-dropping orphan modules gained file-F1 (.274) but risks gold;
  orphan-under-cap gets .270→.353 with zero losses when combined with
  tighter expansion/tests caps.
- Iteration harness: `eval-agent/quick_eval.py` (~90 s over warm indexes).

## Remaining work
1. **Tier A**: fresh run was launched
   (`contextbench_run.py --instances eval-agent/results/tier_a_instances.txt`)
   after archiving stale results to `*.bak-pre-allocator`. Check
   `eval-agent/results/cb_results.jsonl`; compare with
   `scripts/agent_eval/summarize_cb.py`.
2. **Tier B**: archive `agent_results.jsonl` first (already backed up as
   `.bak-pre-allocator`; delete the live file if recreated), then
   `scripts/agent_eval/tierb_agent_run.py` unchanged (~16 opencode runs,
   hours). Honest reporting only.
3. Commit any remaining deltas; conventional short messages.

## Gotchas learned this session
- ALWAYS export BOTH `OXIDE_EMBED_URL` and `OXIDE_EMBED_MODEL=qwen3-Q8_0`
  for every index/retrieval command. URL-only runs label vectors
  `http:@…` → silent wipe+reembed under wrong identity.
- Upstream ContextBench dataset drifted: `limit_per_repo=3` sampling no
  longer reproduces the recorded 21 tasks (15/21 match). Always use the
  instance pin file `eval-agent/results/tier_a_instances.txt`.
- diagnose_pack.py sampler is fixed to use that pin.
- clippy 1.98 flags pre-existing `chunks_exact` lints in src/index.rs
  (fixed inline, behavior-identical).
