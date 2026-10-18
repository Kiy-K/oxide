# Preregistration: structural mode in the MCP tool (agent trial)

Frozen before any scored run (`results/freeze.txt`). Question: should OXIDE's one MCP
tool, `oxide_search`, offer `mode="structural"` (explicit ast-grep pattern matching over
the indexed snapshot, `Repository.structural_search`)? Specifically, do agents use it
appropriately, does it help on shape questions, and does it leave ordinary navigation
unharmed? The ast-grep feasibility study ([../astgrep-feasibility](../astgrep-feasibility/README.md))
passed its adoption gate narrowly and found structural results better than grep in only
16 of 60 replayed searches, so exposure needs agent evidence.

## Design

- **Agent:** Claude Code 2.1.302 (`claude -p`), model `claude-sonnet-5-5`, default
  effort. Isolated configuration: an empty config directory with the user's
  credentials only, and `CLAUDE.md`, auto-memory and hooks disabled
  (`scripts/trial.py` § `SETTINGS`, `ENV`). Tools are `Bash`, `Read`, `Grep`, `Glob`
  and `oxide_search` only, and `Bash` is limited to read-only commands (`rg`, `grep`,
  `find`, `ls`, `cat`, `head`, `tail`, `wc`, `sed`, `awk`, `sort`).
- **Discovery gate (passed before freezing).** The tool schema must be in the model's
  context. With MCP tool search off, the agent recited `oxide_search`'s modes and
  arguments without calling it, and answered NONE without the server. Codex CLI
  0.163.0 failed the same gate: it answered NONE with the server, and its first-turn
  input was 10,368 tokens with and without it. That repeats the agent-navigation
  follow-up's finding, so Codex is not used.
- **Arms.** Both serve the same `oxide mcp` (this commit) over the same index.
  - **A:** `ast_grep_py` is blocked, as on an install without the `structural` extra.
    The tool offers `mode` ∈ {search, context}.
  - **B:** the same server with the extra. It adds `mode="structural"` (with
    `language` and `rule`).
- **Prompt (both arms):** the question, one neutral sentence ("OXIDE's MCP tool
  `oxide_search` can query an index of this checkout. Use it if it helps."), and an
  `ANSWER:` block of `path:line` lines (`scripts/trial.py` § `PROMPT`). Nothing tells
  the agent which mode to use.
- **Tasks** ([tasks.json](tasks.json)) are realistic code-audit and navigation
  questions over pinned ContextBench corpora.
  - **Eight shape tasks (S1–S8)** ask for every instance of a syntactic shape.
    Five are Python: `except: pass` handlers, `isinstance` with a tuple, `open()`
    without `encoding=`, mutable default parameters and `raise … from None`. Three are
    TypeScript: `await` in a loop, `console.log`, and empty `catch`. They are the
    feasibility study's informative queries with 9–26 instances. They include shapes
    where regex already did well (isinstance, regex F1 1.0 on seaborn), so the
    selection is not limited to cases where structural search wins.
  - **Four control tasks (K1–K4)** are ordinary navigation: one definition lookup,
    callers of a function, direct subclasses, and two definitions.
  - **One dev task (D1)** is unscored. It was run once per arm to check the harness.
- **Ground truth.** For shape tasks it is computed by
  [scripts/truth.py](scripts/truth.py). That script uses py-tree-sitter with explicit
  checks per task, independent of ast-grep and OXIDE's structural search, over every
  file OXIDE captures in the task's language. It has a fixture self-test, and every
  item was checked against its source line before the freeze (single rater). Control
  truth is in tasks.json, checked by hand.
- **Runs:** 12 tasks × 2 arms × 2 repetitions = 48 scored runs. Each repetition runs
  in a seeded random order (`SEED`), with three runs in parallel and a 15-minute cap
  per run.

## Measures

- **Score** per run: the `path:line` answers against truth. An answer matches an
  unmatched truth item on the same path whose lines contain it. A second answer inside
  an already matched item is ignored. Precision, recall and F1 follow from that. A run
  with no parseable answer scores 0.
- **Tool use** comes from the transcript: calls per tool and per `oxide_search` mode,
  and structural calls that returned a tool error.
- **Cost:** total tokens (input, output, cache creation and cache reads) and wall time
  per run.
- A **technical failure** is a timeout, a non-zero exit, an error result or a missing
  `ANSWER:` block.

## Criteria (all over the 48 scored runs)

| | Criterion | Pass if |
| --- | --- | --- |
| V | Validity | at most 10% technical failures in each arm |
| P1 | Adoption | B calls `mode="structural"` in at least 50% of its shape-task runs (≥ 8 of 16) |
| P2 | Benefit | mean shape F1 of B − A ≥ 0.10, and B's per-task mean F1 is below A's by more than 0.10 on at most 2 of the 8 shape tasks |
| P3 | Appropriate use | at least 75% of B's structural calls succeed (no tool error) |
| P4 | No harm | B's mean control F1 ≥ A's − 0.10 |
| P5 | Cost | B's median tokens per run and median wall time, each ≤ 1.25 × A's (all 24 runs per arm) |

**Verdict.**
- **PROMOTE** (structural mode stays in `oxide_search`) if V and P1–P5 all pass.
- **REJECT** if V passes and any of P1, P2, P4 or P5 fails.
- **INCONCLUSIVE** if V fails, or if P3 alone fails.

Any verdict other than PROMOTE removes structural mode from the MCP tool, keeps
`Repository.structural_search` as a Python API capability, and documents why.

Reported descriptively, without gating: per-task tables; structural use on control
tasks; examples of errors and wrong answers; and F1 restricted to B runs that did or
did not use structural mode. The last is confounded by the agent's own choice, so it
is not causal.

## Fixed in advance

- No task, truth item, prompt, setting or threshold changes after the freeze. A
  harness defect found during the runs is fixed only if it affects both arms
  identically. It is recorded as a deviation, and the affected runs are re-run in
  full.
- The 48 runs are reported in full: nothing is dropped, re-rolled or cherry-picked.
- n = 2 per task and arm is small. The thresholds are practical decision rules, not
  significance tests.
