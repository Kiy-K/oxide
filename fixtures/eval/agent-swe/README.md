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
- **Cost threshold.** `--max-tokens N` uses recorded billed-equivalent tokens (input, output,
  cache read and cache write, unweighted) for projected admission. Capped runs are serialized;
  a new run starts only if its estimated cost fits the remaining threshold. A single run can
  exceed the threshold: this is not a hard provider-enforced budget or a per-run token limit.
  A run whose usage may be incomplete (timeout, failure, or a stream cut before its final usage
  event) is recorded with `usage_unknown`; its tokens are a lower bound. Under a cap, no further
  run starts after one, including on resume.
  The first v2 pilot had no projection and spent 809k against a 450k cap; that file is kept as
  evidence (`results/pilot-v2-smoke.jsonl`).
- **Manifest.** Each results file writes `<results>.manifest.json`: split, task-file and task-id
  hashes, model, prompt and arm-configuration hashes, the opencode version, and a hash of the
  engine and harness source (HEAD, uncommitted edits, untracked files). Resuming under a changed
  manifest is refused, so a results file never mixes two protocols or two code states.
- **Traces.** Each row names its run directory (`trace`), the events hash, its `spend`, and
  `oxide_tools_connected`, which is read from the agent's own log. A run that never calls a
  connected tool is recorded as ignoring it; a run where the tool never connected is a different
  finding. Trace directories are kept, not pruned: events, patch, OXIDE data and OpenCode logs.
  The OpenCode database is emptied after the run, since it holds the provider login; its session
  records are not kept (the events file is the record). Treat traces as sensitive: literal
  redaction cannot remove copied, encoded or refreshed secrets.
- **Diagnosis.** `scripts/agent_eval diagnose RESULTS` classifies each OXIDE-arm run from its trace
  against the gold patch's files, with the decision rule in `diagnose.py`.

The held-out set is not used for any change until the change is fixed and recorded. Results from
the dev set are for diagnosis and must not be reported as held-out results.

## Discoverability pilot (amendment, pre-registered)

Changed: the `oxide_search` tool description and server instructions in
`src/oxide/mcp_server.py`. Nothing else: model, prompt, tools, budget and task order are the
same. Arms: `oxide` (the new description) and `oxide-base` (a snapshot of the previous source,
`run --base-src`). Their tree hashes are in the manifest. Dev tasks only (`--split dev`, which
is spent and used here only to choose and screen a change): the first 8 in the predeclared
order, 2 repetitions, 1,200 s per run, `--max-tokens 6000000`.

Decision rule, fixed before any result is read. Amended once, before any result: the first two
attempts failed at authorization and produced no resolve data. The rule first written kept a
change whose tool calls rose with flat success. That is the call-frequency target this pilot is
meant to avoid, so the rule now keys on success alone.

1. Invalid if the report's harness-error or provider-auth check fails.
2. Primary measure: the paired resolve-rate difference `oxide - oxide-base` over paired tasks,
   with its bootstrap interval.
3. Keep the change only if the point estimate is above 0. A zero or negative estimate reverts
   it, whatever the tool-call counts. Tool calls are reported to explain the result, not to
   decide it.
4. A kept change is not a success claim. Eight tasks and two repetitions cannot exclude a
   large gain or loss. Success claims need the dev repeats or the one held-out look.

Token spend is recorded, not gated. Traces are kept under the cache.

Amendment 2, before any pilot result (approved by the user): the pinned OpenAI credential in
OpenCode returns HTTP 400 for every model (`openai/gpt-5.6-luna` and `openai/gpt-6-luna` both
fail on a one-word call). The pilot therefore runs through the Codex CLI (`codex exec --json`,
ChatGPT login, isolated `CODEX_HOME` with only `auth.json` copied, `--ignore-user-config`,
`--ephemeral`, workspace-write sandbox) on model `gpt-6-luna`. The decision rule above is
unchanged. The 32 runs of attempt 2 are invalid (provider auth) and are not results. Run-3
OpenCode numbers are not comparable to this pilot; the comparison is paired within Codex.
For Codex, `oxide_tools_connected` is the configured count: Codex's JSON stream does not log MCP
startup. Tool calls are observed. `deny` is not recorded for Codex: it is an OpenCode config
policy, and Codex runs under its `workspace-write` sandbox instead. The original Codex login copy
is removed after a normal run; the agent can still copy or transmit it during execution.

Amendment 3, before any pilot result under this harness (recorded by the user's request to secure
the harness): every agent process and the hidden-test pytest run are confined with bubblewrap
(`scripts/agent_eval/confine.py`). The host is read-only; `fixtures/eval`, the task caches and
bare mirrors (which hold upstream fix commits and other runs' patches), and the host's credential
stores are hidden; only the run directory is writable. Agents inherit an allowlisted environment
with no API keys. The kept OpenCode copy's credential row is deleted after each run and the
credential is redacted from events. Git on the agent's tree runs with hooks and fsmonitor off.
Both arms run under the same confinement, so the comparison stays paired. Runs before this
amendment were not confined and are not comparable to pilot runs.

Amendment 4, verified before any held-out look (credential isolation for Codex and OpenCode). Three
gaps were found and fixed: (1) deleting the OpenCode credential row left the key in SQLite free
pages; cleanup originally used `VACUUM` and now truncates the pinned database inode to avoid
agent-controlled path traversal. (2) The hidden list held a checkout's own `.git`, but a linked worktree's `.git`
is only a pointer, so the shared object store (dev answers, older held-out results) was readable. It
now hides the common git directory and every checkout's `fixtures/eval`, which also covers the
protocol worktree's copy of the held-out set. (3) The agent's tree is a standalone `git init` from
the task archive, so hiding the shared store does not touch it.

Verified without a model call: a confined process cannot read the host Codex login or OpenCode
database, sees no credential-named environment variable, and cannot read the held-out copy in either
checkout. The OpenCode copy holds 0 of 18 provider values after forgetting (2 of 18 before). Verified
live, one Codex run (control arm, `openai/gpt-6-luna`): the agent's own shell reported both host
stores denied and a credential-variable count of 0; its 5 login values were in 0 kept files and 0
events; its login copy was removed after the run. Not verified live: OpenCode. Its provider returned
HTTP 400 on the pinned OpenAI credential before any tool call (`provider.auth`), as Amendment 2
recorded, so there is no OpenCode tool run to inspect.

Accepted residuals: an agent can read its own login copy and has network access, so it could send
its own login out; a network allowlist to the model host would close this and is not implemented.
The hidden list is a denylist: the rest of the host is readable read-only, so an answer stored
outside the listed paths would be readable to an agent.

Amendment 5, before any held-out look (review of PR #52): (1) every `git` command on the agent's
tree, including `git apply` of the hidden test patch and the editable install's build hooks, runs
under the same bubblewrap confinement; a planted clean filter no longer runs on the host (tests
in `tests/python/test_agent_run.py` and `test_agent_environment.py`). (2) `/run` and `/var/run`
are masked so host sockets (docker, systemd) are unreachable; name-service files are re-bound.
(3) Run artifacts are written with `O_EXCL | O_NOFOLLOW` after unlinking, and a symlinked OpenCode
credential database is removed, never opened. (4) On the agent's tree only a failed bubblewrap
launch is a harness error; a hang, a broken `conftest.py` or a collection error is the agent's
unresolved result (gold-patch verification stays strict). (5) `report` prints `provisional` unless the
adjacent manifest's whole task x arm x
repetition matrix is scored, or when the manifest is missing. Residuals: the agent and build hooks
keep network access, the hidden-test pytest runs the agent's own `conftest.py` (it could forge
`PASSED` lines), test files are restored from the agent-writable `.git`, and sockets outside
`/run` (for example abstract or `/tmp/.X11-unix`) are not filtered.

## Commands

Before a held-out run, generate and verify its local task set with
`uv run python scripts/agent_eval/heldout.py`; this writes
`fixtures/eval/agent-swe/heldout-tasks.json` and a rejection log. The generator requires
GitHub CLI access and builds candidate repositories; its selection rule is in `heldout.py`.

See the docstring of `scripts/agent_eval/__main__.py`. Verdicts are one row in
[`docs/decisions.md`](../../../docs/decisions.md).
