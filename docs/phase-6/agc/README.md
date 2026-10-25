# Adaptive Graph Context (AGC): REJECT

2026-10-25. Two parts: a re-read of the graph-navigation study (5ed725d) from its frozen
results, then an agent trial of graph evidence and tiered context, preregistered in
[`preregistration.md`](preregistration.md) and frozen by hash in
[`results/freeze.txt`](results/freeze.txt) before any scored run.

**Decision (preregistered rule): REJECT every arm.** No tool arm met the efficiency
criterion; each used about twice the baseline's tokens. The combined arm (AGC) was the
most accurate and made the fewest false claims, but no correctness criterion can carry
that cost under the rule. Nothing in this study touches `src/oxide`, the MCP server, the
CLI or the public API.

## Part 1: graph-navigation reassessed (verdict kept: FAIL)

[`scripts/reassess.py`](scripts/reassess.py) → [`results/reassess.json`](results/reassess.json).
Measured from the frozen rows, truth and audit; nothing of the original is re-scored.

**The completeness signal.** Over 11 incomplete and 13 complete targets:

| Signal | Sensitivity | Specificity |
| --- | --- | --- |
| Preregistered: `unresolved > 0` or `stopped_by` | 8/11 = 0.73 [0.43, 0.90] | 6/13 = 0.46 |
| `unresolved > 0` alone | 0.73 | 11/13 = 0.85 |
| `stopped_by` alone | 0.09 | 0.62 |
| `unresolved > 0` or the root is a method | 0.73 | 0.62 |
| Residual: a grep owner no impact hop explains | 11/11 = 1.00 [0.74, 1.00] | 8/13 = 0.62 |

The poor specificity was mostly `stopped_by=depth`: it fired on 5 targets whose direct
dependents were complete. The residual catches every incomplete target and stays small:
it holds 1–34 entities (median 3) on incomplete targets and is empty on most complete
ones (non-zero on 5: 1, 1, 5, 9, 22). That is why the AGC graph tool lists the residual
instead of claiming completeness.

**Missing relationship types.** Impact missed 44 sites in total:
- 36 attribute calls, all on method roots: dispatch through a receiver of unknown type,
  for example `excinfo.exconly()`.
- 7 bare-name values: `wsgi_errors_stream`, and the `tmp_path` fixture.
- 1 decorator: `after_this_request`.

Also seen but not investigated: inside `Session.get`…`delete`, `self.request(...)` stays
unresolved while `self.prepare_request(...)` resolves. Impact upstream of
`Session.request` returns no hops.

**Jedi oracle discrepancies.**
- Jedi is inconsistent within one file. For `_FakeSignal.send` it found 6 of 12
  identical `<signal>.send(` sites (`appcontext_pushed.send` but not
  `appcontext_popped.send`); OXIDE's `unresolved` count, 12, was exact.
- Jedi does not model pytest fixture injection.

Of grep's 178 sites counted as false:

| Kind | Sites |
| --- | --- |
| Attribute calls | 65 |
| Comments or strings | 50 |
| Bare-name values | 30 |
| Fixture parameters | 17 |
| Other definitions | 11 |
| Imports | 3 |
| Bare calls | 2 |

Many attribute calls, values and fixtures are plausibly oracle misses. Every oracle error
found makes the true set larger, which lowers impact's recall, so the FAIL holds.

## Part 2: the AGC trial

### Design

**Tools.** Both live in [`scripts/agc.py`](scripts/agc.py), served by an experimental
MCP server, [`scripts/server.py`](scripts/server.py), which is outside `src/oxide`.
- `oxide_graph(symbol, direction, depth)`:
  - lists the callers or callees the index resolved, each labelled resolved or likely,
    with the source line that links it;
  - for callers, adds the residual: code occurrences of the name that no edge explains,
    labelled by syntax;
  - for callees, adds the calls the index could not resolve;
  - always ends with a note that the graph is not exhaustive.
- `oxide_tiers(level, target)` returns one level:
  - architecture: packages and files;
  - module: an outline with line ranges;
  - symbol: signature, docstring and members;
  - source: numbered lines.

**Arms** (B, G, T and AGC; G and T each have one tool):

| Arm | Tools beyond read, grep, glob and list |
| --- | --- |
| B | none |
| G | `oxide_graph` |
| T | `oxide_tiers` |
| AGC | both |

**Tasks** ([`tasks.json`](tasks.json)): 12 tasks over flask, requests and pytest, with
truth written by hand:
- 5 impact tasks: every direct use before a signature change;
- 3 call chains;
- 2 callee lists;
- 2 locate tasks.

**Runs.** OpenCode 2.1.4 with `openai/gpt-6-luna`, 2 repetitions: 96 scored runs plus 4
unscored dev runs.

**Harness deviation.** OpenCode 2.1 reaches MCP tools only through its code-mode
`execute` tool and hides them from the model's tool list (probed: the model said twice
that no such tool existed). Tool arms therefore allow `execute`, and their system prompt
documents each tool's signature. The preregistration discloses this.

The one harness fix after the dev run: OpenCode's read tool names its argument `path`,
so repeated reads had been counted as 0. It was fixed before the freeze.

### Results (measured)

[`results/report.json`](results/report.json) and [`results/runs.json`](results/runs.json).
All 96 runs completed with no technical failures. The checkouts were unchanged
afterwards (`trial.py verify`).

| Arm | Macro F1 | False claims / run | Tokens ×B | Uncached ×B | Calls ×B | Seconds ×B | Repeated reads / run | Runs using OXIDE | Passes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| B | 0.918 | 1.08 | 1.00 | 1.00 | 1.00 | 1.00 | 0.79 | — | — |
| G | 0.944 | 0.54 | 1.80 | 1.13 | 0.99 | 1.05 | 0.58 | 7/24 | no (A, E) |
| T | 0.875 | 0.88 | 1.85 | 1.14 | 1.07 | 1.19 | 1.46 | 19/24 | no (C, E) |
| AGC | 0.940 | 0.33 | 2.09 | 1.23 | 1.15 | 1.28 | 0.58 | 17/24 | no (E) |

The ×B ratios are geometric means over tasks of the per-task median relative to B (calls
+1 on both sides). Bootstrap 95% intervals over tasks for F1 minus B:
- G: [−0.04, +0.10]
- T: [−0.14, +0.04]
- AGC: [−0.09, +0.12]

Macro F1 by task type:

| Type | B | G | T | AGC |
| --- | --- | --- | --- | --- |
| Impact (5) | 0.96 | 0.99 | 0.85 | 0.98 |
| Chain (3) | 0.74 | 0.87 | 0.83 | 0.95 |
| Callees (2) | 1.00 | 0.88 | 0.88 | 1.00 |
| Locate (2) | 1.00 | 1.00 | 1.00 | 0.75 |

Per task, each cell is F1 / false claims / median tokens:

| Task | B | G | T | AGC |
| --- | --- | --- | --- | --- |
| I1 | 1.00 / 0 / 8.6k | 1.00 / 0 / 10.6k | 1.00 / 0 / 12.1k | 1.00 / 0 / 32.3k |
| I2 | 1.00 / 0 / 12.0k | 1.00 / 0 / 13.6k | 1.00 / 0 / 20.8k | 1.00 / 0 / 30.0k |
| I3 | 0.83 / 0 / 4.1k | 1.00 / 0 / 14.0k | 0.83 / 0 / 10.9k | 1.00 / 0 / 10.9k |
| I4 | 1.00 / 0 / 17.5k | 1.00 / 0 / 26.1k | 0.50 / 1.5 / 24.4k | 0.96 / 1.0 / 29.9k |
| I5 | 0.96 / 1 / 20.3k | 0.96 / 1 / 35.3k | 0.94 / 1 / 20.8k | 0.96 / 1 / 23.5k |
| P1 | 0.69 / 6 / 25.5k | 1.00 / 0 / 65.7k | 0.80 / 2.5 / 63.9k | 1.00 / 0 / 37.0k |
| P2 | 0.90 / 1 / 22.1k | 0.94 / 0.5 / 15.1k | 1.00 / 0 / 22.1k | 1.00 / 0 / 24.4k |
| P3 | 0.63 / 5 / 47.5k | 0.67 / 4 / 72.5k | 0.68 / 4.5 / 50.2k | 0.86 / 2 / 43.1k |
| D1 | 1.00 / 0 / 6.9k | 0.75 / 1 / 24.1k | 0.75 / 1 / 16.1k | 1.00 / 0 / 25.1k |
| D2 | 1.00 / 0 / 5.9k | 1.00 / 0 / 15.0k | 1.00 / 0 / 17.6k | 1.00 / 0 / 17.3k |
| L1 | 1.00 / 0 / 4.6k | 1.00 / 0 / 9.1k | 1.00 / 0 / 14.7k | 0.50 / 0 / 13.6k |
| L2 | 1.00 / 0 / 5.2k | 1.00 / 0 / 10.4k | 1.00 / 0 / 15.1k | 1.00 / 0 / 16.2k |

### Why the tool arms cost more (measured, then inferred)

**Measured.**
- Code mode alone roughly doubles tokens. The first step's prompt is 841 tokens in B and
  2,235–2,385 in the tool arms, and that ~1.4k is resent on every step.
- Tool-arm runs that never called OXIDE still used a median 1.8–2.0× B's tokens (G: 17
  such runs; AGC: 7). G on the locate tasks is an example: no OXIDE call, about 2× the
  tokens.
- Calling OXIDE added more on top. AGC runs that used it took a median 2.6× B's tokens,
  against 2.0× for AGC runs that did not.
- The uncached ratios (1.13–1.23) are well below the totals, so most of the extra is
  cached prompt, but it is not all overhead.

**Inferred.** A client that shows MCP schemas natively would cut the overhead per step,
but OXIDE's output would still be added. No arm came near the 0.85 tokens ratio the rule
requires.

### What the tools did buy (descriptive, not gated)

- **Fewer false claims.** AGC averaged 0.33 per run against B's 1.08, and was never worse
  than B on any task.
- **Better call chains.** AGC's chain F1 was 0.95 against B's 0.74. On P1, B listed the
  `prepare_*` helpers as chain members (6 false claims); G and AGC listed none. On P3, B
  confused the hookspec and plugin `pytest_collection`.
- **No help where grep already works.** On impact, callee and locate tasks, baseline
  grep and read were already near F1 1.0 at a quarter to a half of the tokens.
- **The graph tool went mostly unused alone.** G called it in 7/24 runs, which fails
  criterion A. Paired with tiers it was called 42 times in 17/24 runs.
- **AGC's locate miss** was one run (L1 rep 2) that named `rebuild_auth`, the caller,
  instead of `should_strip_auth`. That is an answer-choice slip, not a tool error.

## Limitations

- 12 tasks and 2 repetitions: every F1 interval includes zero.
- One agent and one model. Results come through code mode, which is not how most MCP
  clients expose tools.
- Python only, repositories no larger than pytest, and no editing tasks.
- The provider reported $0 per run, so cost is measured in tokens only.
- Truth was written by hand by one author; neutral items soften borderline answers.
- I1's truth set is the case where the graph is complete; the other four impact targets
  test the residual.

## Decision

**REJECT** under the preregistered rule. All arms passed V and F. Then:
- G failed A (adoption) and E (efficiency).
- T failed C (F1 0.875 against B − 0.03) and E.
- AGC failed E alone.

No Proposed ADR is recommended.

A follow-up worth considering, not run here, is to measure AGC's chain-task accuracy and
false-claim gains in a client with native tool schemas, to check whether the efficiency
loss is mostly code-mode overhead. It would need a new preregistration, because this
study's held-out tasks are now spent.

## Reproduce

```sh
cd docs/phase-6/agc/scripts
python3 reassess.py                                 # Part 1
uv run --project ../../../.. python trial.py check   # task keys, checkout digests
uv run --project ../../../.. python trial.py dev     # unscored harness check
uv run --project ../../../.. python trial.py run 1 2 && uv run --project ../../../.. python trial.py run 2 2
uv run --project ../../../.. python trial.py verify && uv run --project ../../../.. python trial.py grade
python3 analyze.py                                  # the preregistered rule
```

Run records are in `~/Projects/oxide-eval-data/oxide-agc-eval/runs/`. The index is the
graph-navigation store, and the checkouts are the structural trial's.
