# RESUME: context-pack allocator rework (in progress)

## Where we stopped
Diagnostic ran over ~292 ContextBench tasks (all py+ts, larger than the 21-task
Tier A sample) comparing `oxide search --mode hybrid --limit 10` vs
`oxide context --budget-tokens 4096`. Script: `eval-agent/diagnose_pack.py`
(BUG TO FIX: it loads ALL tasks — pass `limit_per_repo=1` semantics like
contextbench_run.py's Tier A sampling before rerunning). Partial per-task log:
`eval-agent/results/pack_diag_partial.log` (aggregate sections never printed;
kill came first). Raw log was also at /tmp/opencode/diag.log.

## Evidence (consistent across ~292 rows)
1. Budget saturates every run: used_tokens 4078–4096 of 4096. Pack is full of
   low-value content.
2. **Noise floods the pack**: typically 3–13 files per pack are non-gold AND
   absent from hybrid top-10. Sources: structural expansion (deps/tests at
   0.4× seed score, unbounded neighbor count over 5 seeds) plus weak tail
   seeds (limit 16).
3. **Direct losses exist**: rows with lost=1..2 where a gold file IS in hybrid
   top-10 but NOT in the pack — displaced during greedy fill ("over token
   budget" continue-skips big primaries then fills with tiny junk) or
   subsumed. Attribution by omission-reason never aggregated; rerun diag to
   get category counts.
4. File-F1 regresses vs hybrid almost everywhere (e.g. 0.57→0.12, 0.80→0.40);
   line-F1 mixed (occasionally better when pack includes tight spans).

## Fix plan (smallest justified changes, src/context.rs only)
1. Per-object token cap (~350 tok) + query-centered windowing: when symbol
   body > cap, cut window(s) around lines matching query terms; fallback head.
   Turns "drop entirely" into "include concentrated evidence".
2. Guarantee top-K semantic primaries: reserve share of budget, shrink-to-fit,
   never drop a top-3 primary for budget while noise exists.
3. Diversity cap: max ~2 items per file (except the top primary); prevents one
   hog file eating the pack.
4. Relevance floor: drop candidates < ~0.15 × top-seed score unless nothing
   else remains; count as omissions ("below relevance floor").
5. Cap expansion fan-out per seed (e.g. ≤2 neighbors) and total expansion
   items (≤6); expansion stays strictly additive AFTER guaranteed primaries.
6. Keep role ordering + flattened JSON contract + explicit omission reasons
   for every new drop rule.

## Gates before commit
- Unit regression tests per failure mode above (src/context.rs tests).
- cargo fmt/clippy/test green; fixtures benchmark gate unchanged-green.
- Rerun diagnose_pack.py (fixed sampler) → budgeted must Pareto-dominate raw
  hybrid (file-F1 ≥, line-F1 ≥ or ≈, tokens <) on the sample.
- Rerun scripts/agent_eval/contextbench_run.py fresh (archive old
  cb_results.jsonl first — runner resumes by (task, condition) and would reuse
  stale budgeted rows). Then Tier B unchanged, honest reporting.

## Env reminders
- llama server: scripts/embedder.sh start (was up, port 8191, Q8_0).
- eval venv: eval-agent/.venv/bin/python (3.11).
- Pack item keys are FLAT (it["file"], not it["symbol"]["file"]).
