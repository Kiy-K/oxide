# RESUME: Tier B downstream evidence (in progress)

## What we did (2026-08-27)
- `d1076f5` frozen (canonical `0.405 R@1 / 1849 tok`, hybrid `0.310`, no file-span, no tuning)
- Tier A: 21 tasks (default `limit_per_repo=3` × 7 repos), 84 runs paused
- Tier B 4-task paired (`eval-agent/results/agent_results.jsonl`, 16 recs):
  `stock 1.00/1.00/451s` -> `budgeted 1.25/0.75/310s` (+0.25 gold, -0.25 bad, -31% wall, 1349 vs 2594 tok)
- Tier B 25-task expanded launched, **reverted** when `opencode/x-preview-f-free` was found
  to return `Model x-preview-f-free is not supported` for every run
- Switched model to `cline-pass/cline-pass/minimax-m3` (working, smoke test passed)
- Restarted Tier B 13-task small expanded (8 repos x 2; skipping large repos for now)
  -- results appending to `eval-agent/results/tierb_expanded/agent_results.jsonl`

## State
- Runner PID alive (setsid bash /tmp/run_tierb_small.sh)
- 1/26 runs done (~206s each on first run, faster than the 600s original since model is fast)
- `tierb_expanded.20260827-2055.bak/` preserved (the bad-model attempt)
- `tierb_4task_orig/agent_results.jsonl` preserved (4-task evidence on old model)

## What to do tomorrow
1. Wait for the 13-task run to finish (~26 × ~200-400s ≈ 90-180 min)
2. Aggregate `gold_used`/`bad`/`wall`/`ctx` by condition
3. Re-run `summarize_cb.py` to refresh Tier A summary
4. Compare: did the new model + new evidence change the 7 answers?
   - If expanded 25 confirms wall/bad v without gold v and lineF1 holds ->
     prioritize integration/product reliability (fix tool_calls/time-to-first-edit/
     ignored context, add test-pass evaluator)
   - If null -> investigate context consumption / prompt interface
   - If regress -> noisy context / allocation / prompt

## Evidence so far (mixed-model, comparable metrics)
- 4-task: stock 1.00/1.00/451s -> budgeted 1.25/0.75/310s (+0.25 gold, -31% wall, -0.25 bad)
- 1 new-model seaborn: stock 1/6, bad=2, wall=206.8s (faster wall, more bad than 4-task avg;
  model difference or per-task variance)
- `tool_calls_proxy` = 0.0 throughout (harness counts "\n$ " which current opencode doesn't
  emit; need to parse JSON log to fix)
- Retrieval frozen, no further tuning. Embedder live (qwen3-Q8_0 @ :8191)

## Known gaps
- Test-pass evaluator not implemented (Tier B measures diff utilization, not solve)
- 4-task sample too small for solve superiority claims
- Tool-call counting broken (cosmetic for now; doesn't affect gold/bad/wall/ctx)
- Tier A still in-progress (40/84 at last check)
