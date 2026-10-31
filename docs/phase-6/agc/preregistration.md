# Adaptive Graph Context (AGC): preregistration

Frozen before any scored run (hash in `results/freeze.txt`). Dev runs (`trial.py dev`,
one unscored task) may precede the freeze and may change only the harness, never the
tasks, truth, arms or rule below.

## Question

Does giving an agent OXIDE graph evidence that never claims to be complete, tiered
context (architecture → module → symbol → source, one level a call), or both, make it
more efficient on realistic code-navigation tasks than its built-in tools alone, without
losing correctness or adding false claims?

The goal is efficiency, not graph completeness. The graph-navigation study (5ed725d,
FAIL, verdict kept) showed impact paths are precise but silently incomplete. Its
reassessment (`scripts/reassess.py`, `results/reassess.json`) found that a *residual*
(code occurrences of the name that no edge explains) catches every incomplete target
(11/11), where `unresolved` caught 8/11. The graph tool here lists that residual
instead of asserting completeness. On-demand outlines in an agent loop are the new
hypothesis the outline study (#44, REJECT) left open.

## Arms

Same agent, model, tasks, message and isolation; arms differ only in the OXIDE tools
available (`scripts/server.py`, an experimental MCP server outside `src/oxide`) and their
documentation in the system prompt.

| Arm | Tools beyond read, grep, glob, list |
| --- | --- |
| B baseline | none |
| G graph-only | `oxide_graph(symbol, direction, depth)` |
| T tiers-only | `oxide_tiers(level, target)` |
| AGC combined | both |

`oxide_graph` (`scripts/agc.py`) lists callers or callees the index resolved, each with
the source line linking it and a resolved/likely label, then (callers) the code
occurrences of the name no edge explains, labelled by syntax, or (callees) the calls the
index could not resolve, and a fixed note that the graph is not exhaustive.
`oxide_tiers` returns one level: packages and files with sizes and docstrings; a file's
outline with line ranges and signatures; a symbol's signature, docstring and members;
or numbered source.

## Agent and isolation

OpenCode 2.1.4, `openai/gpt-6-luna`, `opencode run --standalone`, one run per process,
an isolated config per run (no user MCP servers, plugins, skills or instructions;
`OPENCODE_DISABLE_CLAUDE_CODE`), working directory the checkout. Built-in tools: read,
grep, glob, list; shell, edits, web, subagents and todo tools denied. OpenCode 2.1
reaches MCP tools only from its code-mode `execute` tool and does not show them in the
model's catalog (probed before the freeze), so tool arms allow `execute` and their
system prompt documents each tool's call signature and description; B denies
`execute`. Timeout 900 s. Rate-limited attempts are retried and not scored.

## Tasks (`tasks.json`)

12 tasks over the flask, requests and pytest checkouts of the structural trial, indexed
in the graph-navigation store. Truth was written by hand from the source before the
freeze, as OXIDE entity keys; *neutral* items (plausible, not wrong) count neither way.

- Impact (5): every direct use of a symbol before a signature change. I1
  `extract_cookies_to_jar` (graph complete), I2 `ExceptionInfo.exconly` (method
  dispatch: graph finds 1 of 7), I3 `wsgi_errors_stream` (value use), I4
  `Session.request` (graph finds none; distractor `requests.request`), I5 flask
  `redirect` (same-name method and werkzeug imports).
- Chain (3): the call chain `requests.get` → `HTTPAdapter.send`; Flask's WSGI entry to
  the view call; pytest `main` to `Session.perform_collect` through pluggy hooks.
- Callees (2): direct in-repo callees of `Session.request` and
  `Flask.full_dispatch_request`.
- Locate (2): the function that strips `Authorization` on cross-host redirects; the
  tokenizer of `-k`/`-m` expressions.

Each task's message ends with `ANSWER:` and one `path:line` per line.

## Grading (`trial.py grade`)

Each answer line maps to the innermost OXIDE entity whose span holds it (a module-level
line maps to its file). A claim matches a truth item if it is the item, a member of it,
or a function enclosing it; each item matches once; duplicates are ignored; claims
matching a neutral item are ignored. Per run: precision, recall, F1, **false claims**
(claims matching nothing). A run with an error, a timeout or no parsable answer is a
**technical failure** and scores F1 0.

Cost per run: **tokens** (all input, output, reasoning and cache tokens of every step,
from the session export; uncached reported too), **tool calls** (built-in calls, plus
the larger of `execute` calls and logged OXIDE calls), **repeated file reads** (file
opens by `read` or tiers module/source beyond the first open of that file), **latency**
(wall seconds of the run).

## Design

12 tasks × 4 arms × 2 repetitions = 96 scored runs, in a seeded random order per
repetition, 2 at a time, under one `systemd-run` scope (MemoryMax 3G, CPUQuota 200%).

## Decision rule

Per task, take the mean over repetitions (F1, false claims) and the median (tokens,
tool calls, seconds). Ratios are geometric means over tasks of arm/B (tool calls with
+1 on both sides). An arm X ∈ {G, T, AGC} **passes** if all hold:

| | Criterion |
| --- | --- |
| V | ≤ 10% technical failures in X and in B |
| A | X calls an OXIDE tool in ≥ 50% of its runs |
| C | macro F1(X) ≥ F1(B) − 0.03, and X is worse than B by > 0.25 F1 on ≤ 1 task |
| F | mean false claims per run(X) ≤ B + 0.25 |
| E | **tokens ratio ≤ 0.85** and tool-call ratio ≤ 1.00 and seconds ratio ≤ 1.10; or F1(X) ≥ F1(B) + 0.10 with tokens ratio ≤ 1.00 |

Decision:

- **PROMOTE AGC** if AGC passes and either no single arm passes, or AGC beats every
  passing single arm by ≥ 0.05 on the tokens ratio or by ≥ 0.05 F1.
- Otherwise **PROMOTE** the passing single arm with the lower tokens ratio, if any.
- Otherwise **REJECT** every arm.

PROMOTE means: recommend a Proposed ADR for an agent-facing tool built on the promoted
arm's design. This study adds nothing to `src/oxide`, the MCP server, the CLI or the
public API.

Descriptive, not gated: per task type, per task, OXIDE calls by tool, OXIDE output
size, cost in USD, bootstrap intervals over tasks.

## Not tested

Other agents or models, MCP clients that show tool schemas natively, languages other
than Python, repositories larger than pytest, editing tasks.
