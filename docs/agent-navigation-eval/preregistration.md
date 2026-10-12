# Preregistration: agent-guided navigation (A/B/C/D), held-out split

Written after the dev runs and before any held-out agent run. The held-out
split is run once under this protocol; dev results are not part of the
decision. Frozen by the SHA-256 of this file, written to `results/freeze.txt`
with the hashes of `scripts/harness.py`, `scripts/analyze.py`,
`scripts/nav_mcp.py`, `tasks.json` and
`oxide_kernel/crates/runtime/examples/agent_nav.rs` and a timestamp, before the
first held-out agent run (git does not attest the order: the files are
committed together with the results).

Commands: `harness.py run heldout ABCE R 2` for R = 1, 2, 3, then
`harness.py grade heldout R`, then `analyze.py heldout 1 2 3`.

## Hypothesis

Information: a NavigationBundle gives the agent ranked, source-backed
locations and typed relations (C); the interactive arm also lets it ask OXIDE for callers,
references, imports, tests and containment, and to re-route.
Decision improved: where the agent looks next.
Metric that should move: SWE-bench resolution, or the same resolution with
fewer input tokens or tool calls, against both the agent alone (A) and the
packed ContextBundle (B).

## Fixed configuration

- Agent: Codex CLI 0.162.1, `gpt-6-luna`, reasoning effort `high`, `codex exec`
  with an isolated `CODEX_HOME` (auth only, plugins/apps/hooks/memories/
  multi-agent/browser/web search off), sandbox `workspace-write` with network
  disabled, minimal environment (empty `HOME`, fixed `PATH`), 30-minute
  wall-clock cap. Identical in every variant.
- Source: the task's tracked files at `base_commit` (`git archive`), in a fresh
  one-commit repository; no upstream history. OXIDE indexes the same bytes.
- B: `ContextBundle.payload()` of `build_context` (Phase 4 baseline config,
  heuristic judge, no JEV, no embeddings) at 4,096 `oxide-units-v1`.
- C: the NavigationBundle rendered by `agent_nav bundle` from the same
  `ContextRun` (at most 12 locations; previews ≤120 characters; up to 3
  distinct targets per relation group; no source bodies).
- Interactive arm, stored as variant **E**: C plus the same `find`, `expand`
  and `route` operations over the same pinned store, exposed as read-only MCP
  tools (`oxide_find`, `oxide_expand`, `oxide_route`; `scripts/nav_mcp.py`).
  On dev, the shell-command form (variant D) was called in 0 of 18 runs, and
  E was the one interface iteration, also 0 of 18 (README § Dev). E is
  run on held-out because MCP is the integration OXIDE would ship; D (CLI) is
  not run on held-out. Wherever the original task says "D", read E.
- Prompts: `BASE_PROMPT` plus the variant suffix in `scripts/harness.py`.

## Sample

The 19 `heldout` tasks of `tasks.json` (DecisionBench v1 calibration and test
repositories, SWE-bench Verified, Python): 21 minus two matplotlib tasks
excluded before the freeze because their gold patches could not be graded
here (the images do not unpack under rootless podman; `tasks.json` records
the reason). Variants A, B, C, E.
3 repetitions per (task, variant),
reps 1–3, variant order randomized per task and repetition by the seeded
order in `harness.py`, two tasks in parallel.

## Outcome measures

- Primary: resolved, by the SWE-bench harness 5.0.2 on
  `SWE-bench/SWE-bench_Verified` images (podman). An empty patch, a run that
  timed out without a patch, or an ungradable patch counts as unresolved.
- Secondary: input tokens (Codex-reported, cached included), uncached input,
  output tokens, tool calls (commands plus file changes), wall time, files
  named in commands, gold-file coverage and files inspected outside gold and
  patch (ContextBench gold files; a proxy, not a read log), OXIDE prompt
  units, E's tool calls and their latency and peak RSS. OXIDE index cost
  (cold, per task) is reported separately from warm query cost.

## Analysis

The task is the unit: repetitions are averaged within a task, then paired
across variants. Success: mean paired difference. Tokens, tool calls, wall:
geometric-mean paired ratio. 95% percentile bootstrap over tasks, 10,000
resamples, fixed seeds (`scripts/analyze.py`).

## Decision rule (coded as `gates()` in `scripts/analyze.py`)

For v in {C, E}, against both b in {A, B}:

- superior: success difference CI lower bound > 0;
- non-inferior: success difference CI lower bound ≥ −0.10;
- efficient: input-token ratio CI upper bound < 0.85, or tool-call ratio CI
  upper bound < 0.85;
- reliable: v's rate of timed-out, errored or non-zero-exit runs ≤ b's + 0.10.

**PROMOTE v** if v is reliable and (superior, or non-inferior and efficient)
against both A and B. Otherwise **REJECT** if neither C nor E beats
max(A, B) on success and neither reduces input tokens by ≥5% (point
estimate) against both. Otherwise **INCONCLUSIVE**.

Reporting rules, independent of the verdict: if A's success ≥ both C's and
E's, the report states that OXIDE's navigation value has not been
demonstrated. If B's success > both C's and E's, one-shot packing stays the
preferred mode for this workload.

## Reruns and exclusions

A run is repeated only for infrastructure failure: no `turn.completed` event
and a service-side error (authentication, usage limit, transport) in its
transcript or stderr. Its directory is renamed `<task>.attempt-N` (kept on
disk) and the run command is repeated. A task whose gold patch does not
resolve in the grading environment is excluded before the freeze (the gold
validation runs before `freeze.txt` is written). Model failures (false
claims of completion, timeouts, wrong patches) are outcomes, never rerun.
No task is excluded after this file is frozen.

## Known limits, stated in advance

- 19 tasks: a success difference smaller than about ±0.2 cannot be resolved;
  INCONCLUSIVE is a likely outcome if effects are small.
- The tasks are ContextBench's SWE-bench Verified subset; a strong agent may
  be near its ceiling, which leaves efficiency as the only movable measure.
- Files named in commands are a proxy for reads (grep over directories and
  reads inside scripts are missed).
- One agent and one model; no claim transfers to other agents.
