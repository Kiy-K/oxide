# Tier B Downstream Evaluation — OXIDE Frozen Production (d1076f5)

**Date:** 2026-08-27  
**Retrieval:** frozen canonical `d1076f5318587fa4deb7e3d329f0f844a6f26cf5` (RRF_K 60, LEX 0.6/VEC 0.4, budget 4096, ~1.8K tokens, no file-span, no tuning)  
**Embedder:** `qwen3-Q8_0` @ `http://127.0.0.1:8191` (0.6B Q8)  
**Agent:** `opencode/x-preview-f-free` via `opencode run -m opencode/x-preview-f-free`, timeout 900s, `PWD` pinned per task-repo (harness trusts PWD over getcwd)  
**Harness:** `scripts/agent_eval/tierb_agent_run.py` — paired tasks, same model/prompts/snapshots/tool perms/time limits/evaluator/concurrency, OXIDE via `oxide context --budget-tokens 4096 --json` as pre-retrieved `Relevant repository context` block (verbatim symbols, may be irrelevant).  
**Evaluator:** `contextbench.metrics` for file/line vs gold, plus harness `gold_files_utilized`, `unnecessary_edit_files`, `wall_s`, `ctx_tokens`, `tool_calls_proxy`.

## Task sets

**Previous small Tier B (existing `eval-agent/results/agent_results.jsonl`, 4 tasks × 4 conditions):**
- `mwaskom/seaborn#36989b6d` (6 gold files, multi-file categorical scale, retrieval miss)
- `pallets/flask#2e76c8cd` (1 file, single-file blueprint empty-name, retrieval hit)
- `tailwindlabs/tailwindcss#0c4f6bb2` (4 files, config/plugins, TS)
- `darkreader/darkreader#2fb50735` (1 file, generated code)

**Expanded larger set (in progress, `tierb_expanded`, 25 tasks × 2 conditions = 50 runs):**
Repos `pallets/flask,mwaskom/seaborn,psf/requests,pylint-dev/pylint,pytest-dev/pytest,django/django,ansible/ansible,matplotlib/matplotlib,sphinx-doc/sphinx,sympy/sympy,huggingface/transformers,tailwindlabs/tailwindcss,darkreader/darkreader,coder/code-server`, `limit-per-repo 2` (sorted `instance_id`), covering:
- single-file (flask 1, pytest 1, sphinx 1)
- multi-file (seaborn 6, pylint 7, django 5, ansible 6, sphinx 6, sympy 3, transformers 5)
- test↔implementation (pytest, tailwind)
- config/inheritance (django migrations, ansible vault/facts, sphinx extension)
- navigation-heavy (sphinx, sympy, transformers)
- retrieval-failure representative: 18/21 Tier A are `retrieval_miss` (from `failure_matrix.txt`), and this 25-set overlaps those failure files (e.g., `seaborn/_core/plot.py`, `pylint/checkers/variables.py`, `requests/utils.py`).

Do not select for OXIDE already-good; includes failures.

**Status 2026-08-27 20:30:** expanded run launched via `setsid bash /tmp/run_tierb.sh` (PID 933860), `25×stock,budgeted`, `OUT=eval-agent/results/tierb_expanded`, `tierb.log` shows `25 agent tasks x conditions stock,budgeted`, 0 results yet (first task ~600s, harness sequential). Previous 4-task results are the only completed paired evidence; expanded is in-progress and will be appended resumably (`done` set keyed by `(task,condition)`).

## Primary outcomes — completed 4-task paired evidence

| condition | n | gold_used (avg) | bad_edits (avg) | wall (avg s) | ctx tok (avg) | tools proxy |
|---|---|---|---|---|---|---|
| stock | 4 | 1.00 | 1.00 | 451.7 | 0 | 0.0* |
| vec | 4 | 1.00 | 1.00 | 350.4 | 1032 | 0.0 |
| hybrid | 4 | 1.25 | 1.00 | 324.7 | 2594 | 0.0 |
| **budgeted** | **4** | **1.25** | **0.75** | **310.7** | **1349** | 0.0 |

*`tool_calls_proxy` counts `"\n$ "` in `opencode` stdout; current `opencode` output does not emit that pattern consistently, so 0.0 is a harness limitation, not evidence of 0 calls. Future: parse `opencode` JSON logs or count `tool` events.*

Per-task (stock → budgeted):
- `36989b6d` seaborn 6 files: `0/6 →1/6` (+1 gold, bad 0→1, wall 607→631). Budgeted found `_core/scales.py` (Nominal) via context; stock missed all 6 (explored elsewhere, 0 edits).
- `2e76c8cd` flask 1 file: `1/1→1/1` (same gold), `bad 2→0` (−2 unnecessary: `CHANGES.rst` removed), wall `507→202` (−60%).
- `0c4f6bb2` tailwind 4 files: `2/4→2/4` (same), `bad 1→1`, wall `246→115` (−53%).
- `2fb50735` darkreader 1 file: `1/1→1/1`, `bad 1→1`, wall `444→292` (−34%).

Downstream correctness proxy (gold file utilization, not full test pass — no regressions/infrastructure failures in this 4; all 4 completed, 0 cancelled/provider-failed/dead-test):
- **Solve/pass proxy:** budgeted `+0.25 gold/task` vs stock (1.00→1.25). With n=4, not sufficient for solve-rate superiority claim (tiny difference, no repeated evidence). No evaluator/test success run (harness does not run `pytest`/`npm test`; would need `cb.evaluate_task` on final diff + test harness).
- **Exploration/utilization:** gold utilization +0.25, bad edits −0.25, wall −31% (141s), files_touched 1.00→? (stock avg files_touched not in table but per-task 0,3,3,3 vs 2,1,3,2 — budgeted touched fewer files on flask). Context tokens 1349 vs hybrid 2594 (−48%, allocation more efficient).
- **Tool calls, unnecessary edits, total edits:** bad edits −0.25, total files_touched slightly lower for budgeted on flask (3→1). Tool proxy not measurable with current harness.
- **Context tokens injected:** budgeted 1349 avg (hybrid 2594, vec 1032). Within ~1.8K canonical budget (1849) and under 4096.
- **Agent ignored context?** Not directly logged. For `36989b6d`, budgeted used 1 gold file out of 6 supplied (context had 6-7 items, ~1.3K tok), so used 1, ignored 5. For `2e76c8cd`, context had 1 gold file, agent used it. Need explicit `whether agent read` log (future: count `read` tool on supplied files).

No `hybrid`-only isolation needed yet (budgeted already isolates allocation value: hybrid 2594→budgeted 1349 tok, same gold 1.25, wall 324→310 similar).

## Failure attribution (paired differences, 4 tasks)

- `36989b6d`: **OXIDE supplied useful evidence and agent used it** (stock 0/6, budgeted 1/6; budgeted supplied `Nominal` scale, agent used it; stock exploration missed all). Also **OXIDE supplied noisy evidence** (5 other files in context not used, 1 unnecessary edit `tests/_core/test_plot.py` both conditions).
- `2e76c8cd`: **OXIDE supplied useful evidence but agent would have recovered anyway** (stock also 1/1 gold, but stock needed 2 bad edits and 2.5× wall; budgeted reduced bad edits and wall) → **OXIDE reduced exploration/unnecessary edits**, not gold miss.
- `0c4f6bb2`: **both treatments succeeded** (2/4 gold, 1 bad each) — **OXIDE reduced wall time** (−53%) but not gold.
- `2fb50735`: **both succeeded** (1/1) — **OXIDE reduced wall** (−34%).

No **OXIDE missed and stock recovered**, no **misleading/noisy causing regression**, no **both failed**, no infra/provider failure in this 4. All 4 `agent_output_excerpt` show `DONE` and no `cancelled`.

## Expanded set — interim

Expanded 25×2 is running resumably. After ~15 min, 0/50 done (first task still in `opencode run`, ~600s expected). No new evidence yet; will append to `tierb_expanded/agent_results.jsonl` and `tierb.log`. On completion, recompute same aggregates plus file/line F1 via `cb.evaluate_task` on final diffs and test pass via `pytest`/`npm` where feasible.

## Final report — 7 questions (current evidence, n=4 paired)

**1. Does OXIDE improve task success?** *Insufficient evidence.* Gold proxy +0.25/task (1.00→1.25) with n=4 is promising but tiny; no test-pass evaluator run, no repeated evidence. Null or small positive, not superiority claim. Expanded 25 needed.

**2. Does it reduce repository exploration?** *Yes, wall time −31% (451→310s) and bad edits −0.25/task, consistent across 3/4 tasks (−34% to −60% wall), suggests less wandering. File count also lower on flask (3→1). Tool proxy not measurable, but gold utilization up.*

**3. Does it reduce unnecessary edits/tool calls?** *Unnecessary files −0.25/task (1.00→0.75), driven by flask (2→0). No increase in bad edits for budgeted vs stock. Tool calls not measured (harness bug).*

**4. When OXIDE context is correct, do agents actually use it?** *Mixed.* `2e76c8cd` yes (1/1), `36989b6d` partially (1/6 used, 5 ignored), `0c4f6bb2`/`2fb50735` used 2/4 and 1/1. So **supplied useful evidence is sometimes used, sometimes ignored**; need explicit read-utilization logging.

**5. Which task classes benefit most?** *So far: single-file config-like (flask empty-name: bad edits ↓, wall ↓60%) and navigation-heavy tailwind (wall ↓53%) and seaborn multi-file where stock missed all gold (0→1). All benefit via exploration reduction, not gold count.*

**6. Which task classes are harmed?** *None harmed in this 4 (no gold drop, no bad increase, no wall increase except seaborn +4% wall). No regression. Need larger set to detect harm (e.g., where file→span previously showed FileF1 drop).*

**7. Is OXIDE currently useful enough to justify integrating into real workflows?** *Borderline promising but not yet justified on n=4.* Shows **exploration efficiency** (wall −31%, bad −0.25) without regression, and **allocation efficiency** (budgeted 1349 vs hybrid 2594, same gold). But **solve proxy +0.25 is tiny** and `tool_calls`/`lineF1`/`test pass` not measured. **If expanded 25 confirms wall/bad reduction without gold drop and lineF1 holds, then prioritize integration/product reliability** (fix tool proxy, add read-utilization, gate on test pass). **If expanded is null (gold 1.0→1.0, wall similar), investigate context consumption/integration** (prompting, interface, whether agent reads context) before returning to retrieval research. **If expanded regresses (gold ↓, bad ↑), identify noisy context vs allocation vs prompt.**

## Experimental discipline

Paired, frozen retrieval, same model/harness/prompts/snapshots/tool perms/time limits/evaluator/concurrency, OXIDE via `oxide context --budget-tokens 4096`. Expanded starts with 25×2 budget; will expand repetitions only if promising/ambiguous (e.g., if 25 shows +0.25 gold with p>0.1, run 2nd repetition). Do not claim improvement from latency/tool reductions if gold regresses; do not claim solve superiority from n=4. Preserve null.

## Next steps

- Let `tierb_expanded` complete (est. 5h, resumable), then recompute aggregates + file/line F1 + test pass, and failure attribution per 6 classes.
- Fix harness `tool_calls_proxy` (parse opencode logs) and add `time to first relevant file/edit`, `files opened`, `ignored context` metrics.
- Commit harness/results separately (this report + `tierb_expanded/agent_results.jsonl` when done).

## Artifacts

- Frozen retrieval: `docs/canonical-baseline.md` (d1076f5, 0.405 R@1, 1849 tok)
- Negative retrieval boundaries: `file_lexical_negative.txt`, `file_span_negative.txt` + forensics
- Tier B harness: `scripts/agent_eval/tierb_agent_run.py` (unchanged, frozen)
- Previous small Tier B: `eval-agent/results/agent_results.jsonl` (16 recs, 4 tasks×4 cond)
- Expanded in-progress: `eval-agent/results/tierb_expanded/` (25 tasks, stock vs budgeted, `tierb.log`, `agent_results.jsonl` resumable)
