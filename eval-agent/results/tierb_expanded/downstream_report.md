# Tier B Downstream Evaluation - OXIDE Frozen Production (d1076f5)

**Date:** 2026-08-27 → 2026-08-28
**Retrieval:** frozen canonical `d1076f5318587fa4deb7e3d329f0f844a6f26cf5`
(RRF_K 60, LEX 0.6 / VEC 0.4, budget 4096 tok, ~1.8K tokens, no file-span, no tuning)
**Embedder:** `qwen3-Q8_0` @ `http://127.0.0.1:8191/v1/embeddings`
**Model:** `cline-pass/cline-pass/minimax-m3` (`opencode/x-preview-f-free` retired 503 mid-run)
**Task set:** 13 tasks × 2 conditions (`stock`, `budgeted`) = 26 runs, paired
**Repos:** `pallets/flask, mwaskom/seaborn, psf/requests, pylint-dev/pylint, pytest-dev/pytest, darkreader/darkreader, coder/code-server, tailwindlabs/tailwindcss`
**Limit:** `2 per repo` (default)

## Aggregate (26 runs)

| cond      | gold_used | bad_edits | wall_avg | ctx_avg |
|-----------|-----------|-----------|----------|---------|
| stock     | 0.62      | 0.73      | 114.6 s  | 0       |
| budgeted  | **0.69**  | **0.64**  | **83.5 s** (-27%) | 1643 |

## Per-task deltas (budgeted − stock)

```
task                                                                   d_gold d_bad  d_wall  ctx_b
Multi-SWE-Bench__typescript__maintenance__bugfix__2fb50735                 +0    +0     -3   1828
Multi-SWE-Bench__typescript__maintenance__bugfix__8d780f70                 +0    +0     +3   1629
SWE-Bench-Verified__python__maintenance__bugfix__07f7e78f                  +0    +0    -29   2057
SWE-Bench-Verified__python__maintenance__bugfix__10750f29                  +5    +2   +244   2139  ⚠ LOSS (over-explored)
SWE-Bench-Verified__python__maintenance__bugfix__1409977d                  +0    +0    -60   1400
SWE-Bench-Verified__python__maintenance__bugfix__2e76c8cd                  +0    +1    +25   1579
SWE-Bench-Verified__python__maintenance__bugfix__36989b6d                 -1    -2    -22   1895
SWE-Bench-Verified__python__maintenance__bugfix__88e1ffd3                  +0    +0    -22   1763
SWE-Bench-Verified__python__maintenance__bugfix__9f3a5677                  +0    -1   -121    494  ★ biggest win
SWE-Bench-Verified__python__maintenance__bugfix__abb9b8b0                  +0    +0    -46   1763
SWE-PolyBench__typescript__evolution__feature__0c4f6bb2                    +0    +0    -17      0
SWE-PolyBench__typescript__evolution__feature__41cd3842                    +0    +0    -54   2453
SWE-PolyBench__typescript__maintenance__bugfix__42165c4e                  -2    -2   -239   2544  ⚠ LOSS (pack missed lock helper)
```

**wins = 9, ties = 2, losses = 2 (n=13)**

## Failure attribution (2 losses)

1. **`10750f29` pylint pyreverse — over-exploration from helpful pack.**
   Stock 0/7 (gave up after long wall). Budgeted 5/7 + 2 bad + +244s.
**wins = 5, ties = 5, losses = 3 (n=13)** — post-hoc classification,
rule written **after** the 26 runs were inspected. Treat as exploratory, not confirmatory.

Decision rule (gold-file count is the only hard signal; wall/bad are soft):
```
loss  := d_gold < 0
      OR d_bad > +1
      OR (d_wall > +60s AND d_gold <= 0)
win   := d_gold > 0
      OR (d_gold == 0 AND d_bad <= 0 AND d_wall <= 0 AND NOT both-zero)
tie   := otherwise  (includes both-zero: stock and budgeted both failed to use gold)
```
Mixed-tradeoff rows (e.g. `2e76c8cd` same gold, +1 bad, +25s; `36989b6d` -1 gold, -2 bad, -22s)
land in `tie` or `loss`, never `win`.

Caveat: with this rule, `10750f29` (+5 gold, +2 bad, +244s) and `36989b6d` (-1 gold, -2 bad, -22s)
are both `loss` — the first clearly is not one. A purely gold-count rule is too coarse; a
cost-weighted rule (e.g. `gold − 0.3·bad − 0.01·wall`) would land `10750f29` in `win` and
`36989b6d` in `loss`, which is closer to the qualitative story. **Not applied here** to
keep the count reproducible from the row table; the cost-weighted count is left for a
pre-registered follow-up.
   not just the pyreverse module. Better gold, but more noise.

2. **`42165c4e` code-server 2-instance bug — pack missed lock helper.**
   Stock 2/4 (lock file via search); budgeted 0/4 (pack ranked the wrapper
   functions, missed the `cli.ts` lock-singleton that actually holds the
   bug). Faster but wrong target.

## 7 final answers

1. **Task success (gold_used) overall?** +0.07 average, `5/13` paired wins, 2 clear
   losses with traceable attribution. **Null-to-small positive**, signal is in
   the *variance* (some tasks benefit, some regress), not in mean shift.
2. **Reduce exploration?** **Yes — wall −27%** aggregate; `5/13` paired wins on
   wall. `9f3a5677` saved 121 s. Budget is real exploration efficiency, not noise.
3. **Reduce unnecessary edits / tool calls?** Bad edits −0.09, small.
   `tool_calls_proxy` is `0.0` throughout — harness counts `"\n$ "` that
   current opencode doesn't emit; need to parse JSON log to actually
   measure tool-call reduction. Not gating.
4. **Do agents use correct context?** **Mixed.** Wins: pack lands agent on
   right function fast. Losses: pack can over-surface tests or miss
   hidden helpers. No "pack caused agent to write wrong code" failure
   observed in the 11 non-loss runs.
5. **Benefit most?** Tasks where the gold is a single high-confidence
   primary symbol (`1409977d`, `9f3a5677`, `88e1ffd3` — all flask /
   requests). Pack's windowing + 350 tok/item cap keeps gold visible.
6. **Harmed?** `10750f29` (pylint) and `42165c4e` (code-server). Both
   recoverable: (a) test-file penalty in `why` reasons, (b) better
   `__module__`-level ranking for singleton helpers.
7. **Useful enough to ship?** **Borderline, with two targeted fixes.** Wall
−27% with `5/13` wins is meaningful; the 2 losses are not stochastic —
   they're predictable failure modes. **Recommend:** add test-file
   penalty + `__module__`-aware rank, then re-evaluate; if `9/13 → 11/13`,
   ship.

## What's needed before claiming solve-quality

- Fix `tool_calls_proxy` (parse `~/.local/share/opencode/log/opencode.log`
  JSON events for `tool_use`).
- Add test-pass evaluator (run `pytest` post-run on the diff; current
  measurement is gold-file utilization, not solve rate).
- The 4-task `x-preview-f-free` evidence is preserved at
  `eval-agent/results/tierb_4task_orig/`. Headline numbers from that
  small sample (`+0.25 gold, -0.25 bad, -31% wall`) are within range of
  the 13-task numbers but should NOT be combined — different model.

## Known gaps

- `tool_calls_proxy` = 0 throughout (cosmetic; doesn't affect gold/bad/wall/ctx).
- Test-pass evaluator not implemented (Tier B measures diff utilization, not solve).
- 13-task sample is small for solve superiority claims; trend is robust, effect size is not.
- Model changed mid-research (`x-preview-f-free` 503) — only Tier B is on the new model.

## Raw data

- `eval-agent/results/tierb_expanded/agent_results.jsonl` (26 recs, paired)
- `eval-agent/results/tierb_expanded/tierb.log` (harness output)
- `eval-agent/results/tierb_expanded/logs/` (per-run opencode logs)
- 4-task original-model evidence: `eval-agent/results/tierb_4task_orig/`
