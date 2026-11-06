# Agent task-success evaluation (OXIDE vs no-OXIDE)

Question: does giving a coding agent OXIDE's `oxide mcp` tool raise the share of real bugs it
fixes, against the same agent without it? SPEC § Evaluation (System) asks for this: fixed
agent, model, tools and task snapshot, with a no-OXIDE control. Written before any result was
read; change it only with a new version of this file.

## Tasks

`tasks.json`: instances of [SWE-bench Verified](https://huggingface.co/datasets/princeton-nlp/SWE-bench_Verified)
(revision pinned in the file) from the repositories `scripts/agent_eval/tasks.py` can build
(`REPOS`). `verify` keeps an instance only if, in a fresh environment at its base commit, every
fail-to-pass test fails before and passes after the gold patch. Its pass-to-pass set is the
tests that pass both before and after, so an environment difference cannot count against an arm.
The agent gets a one-commit checkout (no history, no remote), the issue text, and a ready
virtualenv. Web fetch and search, skills and questions are denied in both arms.

Run order is fixed: tasks sorted by `sha256(instance id)`, the first `--tasks N` of them.

## Arms

| | tools |
|---|---|
| `control` | the agent's built-ins only |
| `oxide` | the same, plus `oxide mcp --repo <checkout>`, indexed before the clock starts |

The prompt is identical except that the `oxide` arm adds one sentence naming the tool. The
agent is `opencode run --standalone` in an isolated config directory (no user MCP servers,
skills or plugins), one pinned model for both arms. The model must come from an API-backed
provider: `claude-agent/*` runs inside the user's Claude Code and loads that account's MCP
servers (a CodeGraph tool appeared in the control arm), so it is not a clean control. The tool
list of each arm was checked before the run. Runs interleave in a shuffled but seeded order.

**Pinned for run 1:** model `openai/gpt-5.6-luna`, the first 20 tasks, 3 repetitions per task
and arm (120 runs), 1,200 s per run. A 4-run pilot (2 tasks, 1 repetition, both arms resolved)
only exercised the harness and is excluded from the results.

## Measures

A run is **resolved** when, after the agent stops, the hidden tests pass: every fail-to-pass test
and every verified pass-to-pass test, with the agent's own test edits reverted. Per task, the
resolve rate is the share of resolved repetitions in each arm. The effect is the mean over tasks of
`rate(oxide) - rate(control)`, with a 95% percentile bootstrap over tasks (10,000 resamples,
seed 0). Also reported, not gated: median wall time, output tokens and steps, whether the agent
called the tool, and the index time.

## Rule

OXIDE **improves task success** only if the lower bound of that interval is above 0. Otherwise
the record says "no demonstrated improvement" with the observed interval, whatever the point
estimate. Harness errors (checkout, install, index) are excluded and listed; more than 10% of
runs erroring voids the run. A positive result is a claim about this model, these tasks and
this tool surface, not about agents in general.

## Known limits

- The model may have seen these public issues and fixes in training; the paired design cancels
  it only if it helps both arms equally.
- Tasks are single-repository Python bugs in small to mid-size codebases, where search is
  cheapest without OXIDE.
- Repetitions are independent samples of one stochastic agent; with 20 tasks the interval
  resolves only large effects.


## Run 3 results (amendment)

Run 3 used all 29 tasks in `tasks.json`, 3 repetitions per arm, and the settings above.
The latest 174 attempts scored with no harness errors after 16 provider-auth failures were
retried. Control resolved 47/87 runs; OXIDE resolved 49/87. The task-mean paired difference
was +0.023, with a 95% task-bootstrap interval of [-0.023, +0.080] (better/worse/tied:
2/1/26). The interval includes zero: **no demonstrated improvement**. OXIDE's tool was
called in 19/87 runs (21.8%). Median wall time was 80 s in both arms; median output tokens
were 1,951 control and 2,197 OXIDE.

The nine tasks not included in the original 20-task protocol were also evaluated as a
predeclared additional subset, not newly authored tasks: control resolved 14/27 and OXIDE
15/27; paired difference +0.037, 95% interval [-0.111, +0.222] (better/worse/tied: 1/1/7).
The tool was called in 3/27 OXIDE runs. This subset is too small to establish a gain.

Raw attempts: `~/.cache/oxide/agent-eval/results/run3-reauth-20261106.jsonl`. The evaluator
isolated OpenCode configuration, task worktrees and connected MCP servers, but did not
OS-sandbox agent processes; agents could access the host filesystem. Results apply only to
this model, these public Python tasks and this tool surface. Command and aggregate verdict:
[`docs/decisions.md`](../../../docs/decisions.md).

## Protocol v2 (amendment)

Changes made after the run 3 diagnosis, recorded before any new result is read. Protocol id
`agent-swe-v2` (`scripts/agent_eval/protocol.py`).

- **Splits.** `dev` is `tasks.json`: the 29 SWE-bench Verified tasks every run to date has used,
  so it is spent and only used for tuning and diagnosis. `heldout` is `heldout-tasks.json`:
  pull requests merged on or after 2026-08-01, selected by the frozen rule in its module.
  A held-out set is opened once with `--final`; a second results file on the same set is refused.
- **Build recipes.** A task may carry `build: {python, setuptools}`. Held-out tasks do, because
  the frozen REPOS recipe fails on current PRs. Their fail-to-pass tests were verified with that
  recipe, and the agent receives the same one. Dev tasks keep the REPOS recipe.
- **Cost cap.** `--max-tokens N` caps billed-equivalent tokens (input, output, cache read and
  cache write, unweighted). Admission is projected: until a run reports its cost only one run is
  in flight; after that a run starts only if the spend so far plus every in-flight run at the
  largest observed cost fits. Realized spend can still exceed the cap by one run's cost when a
  run's cost is larger than any seen before. The first v2 pilot had no projection and spent 809k
  against a 450k cap; that file is kept as evidence (`results/pilot-v2-smoke.jsonl`).
- **Manifest.** Each results file writes `<results>.manifest.json`: split, task-file and task-id
  hashes, model, prompt and arm-configuration hashes, the opencode version, and a hash of the
  engine and harness source (HEAD, uncommitted edits, untracked files). Resuming under a changed
  manifest is refused, so a results file never mixes two protocols or two code states.
- **Traces.** Each row names its run directory (`trace`), the events hash, its `spend`, and
  `oxide_tools_connected`, which is read from the agent's own log. A run that never calls a
  connected tool is recorded as ignoring it; a run where the tool never connected is a different
  finding. Trace directories are kept, not pruned: events, patch, and OpenCode and OXIDE data.
- **Diagnosis.** `scripts/agent_eval diagnose RESULTS` classifies each OXIDE-arm run from its trace
  against the gold patch's files, with the decision rule in `diagnose.py`.

The held-out set is not used for any change until the change is fixed and recorded. Results from
the dev set are for diagnosis and must not be reported as held-out results.

## Commands

See the docstring of `scripts/agent_eval/__main__.py`. Verdicts are one row in
[`docs/decisions.md`](../../../docs/decisions.md).
