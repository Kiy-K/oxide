# AGC v2: adaptive graph context, cost per correct answer (preregistration)

Frozen before any scored run (hash in `results/freeze.txt`). Dev runs (`trial.py dev`,
one unscored task) may precede the freeze and may change only the harness, never the
tasks, truth, arms or rule below.

## Question

Does the smallest adaptive AGC variant lower an agent's cost per correct, grounded answer
against a capable grep/read baseline, once client and tool-wrapper overhead is
controlled for? And how much of AGC v1's token cost was OpenCode's code mode?

AGC v1 (7eca108, REJECT, verdict kept) found that AGC gave fewer false claims (0.33 vs
1.08 per run) and better call chains (F1 0.95 vs 0.74). Every tool arm cost about 2× the
baseline's tokens, and runs that made no OXIDE call cost the same, which points at code
mode. v1 also reported that OpenCode 2.1 hides MCP tools unless they go through code mode.

**Correction, measured during this study's setup:** that report was wrong. With
`codemode: false` the tools are listed natively, but OpenCode does not wait for MCP
servers before the first model step, so the tools are missing from step 1. v1's probe
asked in step 1. This study waits for the server, so a direct-MCP arm is feasible.

## The tool (`scripts/server.py`): one tool, adaptive activation

`oxide_context(target, relation, depth)`, an experimental MCP server outside `src/oxide`.
It reuses AGC v1's graph and outline code (`../agc/scripts/agc.py`) unchanged, merged
into one tool.
- **Relationship questions:** `relation='callers'|'callees'` gives graph evidence:
  - the resolved edges, each with the source line that links it;
  - the residual, for callers, or the unresolved calls, for callees;
  - a note that the graph is not exhaustive.
- **Orientation:** with no relation, the tool picks the tier from the target:
  - no target: the package map;
  - a file: its outline with line ranges;
  - a symbol: its signature, docstring, members and line range.
- **Exact source:** not served by the tool. The agent reads only the lines it needs
  with the built-in `read`.
- **Simple lookups:** the tool's description says to use grep and read directly
  instead.

This is smaller than v1's AGC arm: one tool instead of two, and no source tier.

## Arms

Every arm has the same agent, model, tasks, message and isolation, and the same system
prompt. That prompt describes a capable grep/read workflow: grep or glob before reading,
read only the lines needed with an offset and limit, don't re-read, cite only code seen.

| Arm | `execute` (code mode) | OXIDE | What it isolates |
| --- | --- | --- | --- |
| B | denied | none | capable grep/read baseline |
| Bcm | allowed | none | the code-mode wrapper alone (matched control for Acm) |
| Ad | denied | `oxide_context`, direct MCP (native schema) | the product shape |
| Acm | allowed | `oxide_context` through code mode; signature and description in the system prompt (as in v1) | code mode vs direct, the same tool |

## Agent and harness

- **Agent:** OpenCode 2.1.4 with `openai/gpt-6-luna`, one run per private `opencode serve`.
- **Isolation:** an isolated config per run, with no user MCP servers, plugins, skills or
  instructions, and `OPENCODE_DISABLE_CLAUDE_CODE` set. The working directory is the
  checkout.
- **Tools:** built-ins read, grep, glob and list. Shell, edits, web, subagents and todo
  are denied.
- **Startup:** the harness polls `/api/mcp` until the location is loaded and, in the tool
  arms, the `agc` server reports `connected`. It then sends the message with
  `opencode run --server`. Every arm takes this path.
- **Latency:** startup time is recorded separately as `startup_s`, a per-session
  infrastructure cost, and is not part of run latency.
- **Limits:** timeout 900 s. Rate-limited attempts are retried and not scored.

## Tasks (`tasks.json`)

12 tasks, all new: no target or question repeats v1's tasks or the graph-navigation
study's 24 targets. They are over the flask, requests and pytest checkouts of the
structural trial, indexed in the graph-navigation store. Truth was written by hand from
the source before the freeze, as OXIDE entity keys. *Neutral* items are plausible but
not asked for, and count neither way.

| Type | Tasks | Content |
| --- | --- | --- |
| Lookup (4) | L1–L4 | one function: the default User-Agent, the URL adapter, `--lf` deselection, `no_proxy` bypass |
| Relationship (5) | R1, R2, R5 | library callers of `get_auth_from_url` (4), `Flask.ensure_sync` (11, mostly dispatch through `current_app` and other receivers) and `Node.iter_markers` (12) |
| | R3 | the pytest call chain from the runner's `pytest_runtest_protocol` to `pytest_pyfunc_call` through hooks (8) |
| | R4 | the direct in-repo callees of `Session.send` (6) |
| Architecture (3) | A1–A3 | Flask sessions (6), requests authentication (7), pytest assertion rewriting (6) |

Each task's message ends with `ANSWER:` and one `path:line` per line.

## Grading (`trial.py grade`, AGC v1's scoring unchanged)

**Claims and matching.** Each answer line maps to the innermost OXIDE entity whose
span holds it. A claim matches a truth item if it is that item, a member of it, or a
function enclosing it. Each item matches once, and claims that match a neutral item are
ignored.

**Per-run correctness.**
- F1 and **false claims**: claims that match nothing.
- **Technical failure**: an error, a timeout or no parsable answer. It scores F1 0.
- **Success**, which is correct and grounded: not a technical failure, F1 ≥ 0.8 and
  0 false claims.

**Per-run cost.**
- **Cost units:** a price proxy, because the provider reports $0. The weights are the
  ratios of GPT-5-class list prices:

  `input + 0.1 × cache_read + 1.0 × cache_write + 8 × (output + reasoning)`

- **Tokens:** all of them, with uncached tokens reported too.
- **Tool calls:** built-in calls, plus the larger of wrapped calls (`execute` or
  `agc_*`) and logged OXIDE calls.
- **Repeated file reads:** `read`s of a file already read.
- **Latency:** the run's wall seconds.
- **Tool substitution:** grep, glob and read calls per run against B.
- **Infrastructure:** first-step prompt tokens and `startup_s`.

## Design

12 tasks × 4 arms × 3 repetitions = 144 scored runs. Each repetition runs in a seeded
random order, 2 at a time, under one `systemd-run` scope (MemoryMax 3G, CPUQuota 200%).

## Decision rule

**Definitions.**
- **CPS (cost per success):** the arm's total cost units over all its runs, divided by
  its number of successes.
- **CPS ratio:** CPS(X) / CPS(B).
- **Per-task ratios:** tokens, seconds and calls, as geometric means over tasks of
  arm/B, taking the per-task median (calls +1 on both sides).
- **Success rate:** over runs.
- **Macro F1:** the mean over tasks of the per-task mean.

An AGC arm X ∈ {Ad, Acm} **passes** if all of these hold:

| | Criterion |
| --- | --- |
| V | ≤ 10% technical failures in X and in B |
| A | X calls OXIDE in ≥ 50% of its relationship and architecture runs |
| Q | success rate(X) ≥ B − 0.05, and macro F1(X) ≥ B − 0.03 |
| F | false claims per run(X) ≤ B |
| E | tokens ratio ≤ 1.50 and seconds ratio ≤ 1.25, and CPS ratio ≤ 1.00; **premium:** CPS ratio ≤ 1.20 is allowed when success rate(X) ≥ B + 0.10, or when B makes ≥ 0.25 false claims per run and X makes ≤ half as many |

**Decision.**
- **PROMOTE** the passing arm with the lower CPS ratio. PROMOTE means: recommend a
  Proposed ADR for an agent-facing `oxide_context` tool of that shape. This study adds
  nothing to `src/oxide`, the MCP server, the CLI or the public API.
- Otherwise **REJECT**.

**Descriptive, not gated:**
- the wrapper overhead (Bcm/B), and code mode against direct (Acm/Ad);
- per task type and per task;
- OXIDE calls by kind and output size;
- whether OXIDE is used on lookups;
- the CPS ratio's bootstrap interval over tasks;
- startup time, and the index build time and store size for these repositories.

## Not tested

- Other agents, models or clients.
- Languages other than Python, and repositories larger than pytest.
- Editing tasks.
- Real billing: cost is a token-price proxy.
