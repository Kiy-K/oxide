# Structural mode in the MCP tool: agent trial (REJECT)

**Verdict: REJECT** under the frozen rule ([preregistration.md](preregistration.md),
frozen 2026-10-18T11:49:13Z, `results/freeze.txt`). Agents used structural mode when
it was offered and used it correctly, but it bought nothing. With grep alone they were
already at 0.978 mean F1 on the shape tasks, and B reached 0.994: +0.015 against the
required +0.10. So `oxide_search` offers only `search` and `context`, and
`Repository.structural_search` stays a Python API capability (`oxide[structural]`).

## Criteria

48 scored runs (12 tasks × 2 arms × 2 repetitions), Claude Code 2.1.302,
`claude-sonnet-5-5`, all measured:

| | Criterion | Result | |
| --- | --- | --- | --- |
| V | ≤ 10% technical failures per arm | A 0/24, B 0/24 | pass |
| P1 | B uses structural mode in ≥ 8 of 16 shape runs | 9/16 | pass |
| P2 | shape F1 B − A ≥ 0.10, B worse by > 0.10 on ≤ 2 tasks | 0.994 − 0.978 = **+0.015**, worse on 0 tasks | **fail** |
| P3 | ≥ 75% of structural calls succeed | 10/12 (83%) | pass |
| P4 | control F1 B ≥ A − 0.10 | 1.000 vs 1.000 | pass |
| P5 | median tokens and wall time of B ≤ 1.25 × A | tokens 27.4k vs 23.0k (1.19×); wall 11.4 s vs 9.75 s (1.17×) | pass |

## Per task

F1 of each repetition, how many B runs used structural mode, and mean tokens per run.

| Task | A F1 | B F1 | B used structural | Tokens A / B |
| --- | --- | --- | --- | --- |
| S1 `except: pass` (flask, 13) | 1.00 / 1.00 | 1.00 / 1.00 | 0/2 | 15.1k / 80.4k |
| S2 `isinstance(x, (…))` (seaborn, 17) | 1.00 / 1.00 | 1.00 / 1.00 | 1/2 | 24.1k / 35.8k |
| S3 `open()` without `encoding=` (pytest, 26) | 0.96 / 1.00 | 1.00 / 1.00 | 0/2 | 28.7k / 29.6k |
| S4 mutable default parameters (seaborn, 11) | 0.95 / 0.84 | 1.00 / 0.90 | 2/2 | 54.0k / 55.1k |
| S5 `raise … from None` (flask, 16) | 1.00 / 1.00 | 1.00 / 1.00 | 0/2 | 32.4k / 25.2k |
| S6 `await` in a loop (code-server, 20) | 0.97 / 0.92 | 1.00 / 1.00 | 2/2 | 99.6k / 90.6k |
| S7 `console.log` (vue core, 9) | 1.00 / 1.00 | 1.00 / 1.00 | 2/2 | 26.7k / 33.5k |
| S8 empty `catch` (vue core, 12) | 1.00 / 1.00 | 1.00 / 1.00 | 2/2 | 15.3k / 24.2k |
| K1 definition (requests) | 1.00 / 1.00 | 1.00 / 1.00 | 0/2 | 14.2k / 14.8k |
| K2 callers (flask) | 1.00 / 1.00 | 1.00 / 1.00 | 0/2 | 14.4k / 15.0k |
| K3 direct subclasses (xarray) | 1.00 / 1.00 | 1.00 / 1.00 | 0/2 | 21.9k / 22.8k |
| K4 two definitions (vue core) | 1.00 / 1.00 | 1.00 / 1.00 | 0/2 | 18.1k / 22.7k |

Structural mode helped only where grep was hard: S4 (multi-line parameter lists) and
S6 (whether an `await` is in a loop of the same function). Both gains were within the
noise of two repetitions.

## Observations (descriptive, not gated)

- **Agents never used OXIDE's search or context modes.** There were 0 calls in 48
  runs, in either arm, on any task, controls included. Every task was answered with
  the built-in `Grep` (84 calls) and `Bash` (`rg`, `sed`; 33 calls). This repeats the
  agent-navigation finding ([../../agent-navigation-eval/](https://github.com/Kiy-K/oxide/blob/f79808a/docs/agent-navigation-eval/README.md)
  in git history) with the schema provably in context.
- **Structural use was appropriate.** It appeared on 9 of 16 shape runs and on none of
  the 8 control runs. Both errors (S4 r1, S8 r1) had one cause: the agent put a prose
  description in `query`, which is the pattern, and the real constraint in `rule`, so
  the description failed to parse as a pattern. Each agent retried successfully. A
  `kind`-only rule has no natural pattern, a weakness of the pattern-plus-rule shape.
- **What the model saw.** Claude Code passed the structured result (JSON) to the
  model, not the compact `path:line: preview` text the tool also returned.
- **Cost.** The extra schema cost about 0.6–0.9k tokens per run, visible on the
  control tasks. Mean cost per run was $0.026 (A) and $0.031 (B).
- B shape runs averaged F1 0.989 with structural mode (9) and 1.000 without it (7).
  Agents chose the mode themselves, so this is not causal.

## Discovery and isolation (before the freeze)

- **Codex CLI 0.163.0 cannot be used.** It never put the MCP schema in the model's
  context: it answered NONE, and its first-turn input was 10,368 tokens with and
  without the server. Claude Code with `ENABLE_TOOL_SEARCH=false` recited the tool's
  modes and arguments.
- **Isolation needed three passes.** A fresh `CLAUDE_CONFIG_DIR` still loaded
  account-synced plugins, and a plugin's SessionStart hook injected its prompt. The
  user's `~/.claude/CLAUDE.md` was also read, despite a separate `HOME`. The fixes are
  `disableAllHooks`, `CLAUDE_CODE_DISABLE_CLAUDE_MDS=1` and auto-memory off
  (`scripts/trial.py` § `SETTINGS`, `ENV`). A probe then confirmed no skills, hooks,
  memory or extra instructions reached the model. The first dev runs were made with
  the hook active and are kept apart, unscored (`runs-dev-hooks-leaked/` in the data
  directory). The scored runs were all made after the fix.

## Deviations

- **The requests checkout gained `requests/__pycache__/`.** Each MCP server ran as
  `python -m oxide mcp` with the checkout as its working directory. `-m` puts that
  directory first on `sys.path`, and FastMCP imports `requests` at startup, so the
  server loaded the checkout's own `requests` package. Both arms were affected the
  same way, no source changed, and no agent called the server on that task (K1). The
  `oxide` console script does not do this; `docs/public-api.md` § MCP now warns
  against `python -m oxide` inside a checkout.
- `scripts/analyze.py` was written after the runs. It computes the preregistered
  criteria exactly as written.

## Limitations

- The trial covers one agent and one model, with n = 2 per task and arm. The
  thresholds are decision rules, not significance tests.
- The tasks are audits of 9–26 instances in small repositories, sized so a complete
  list fits in a reply. On a repository the size of VS Code (hundreds of matches),
  grep's comment and multi-line misses would be larger, but so would the output a
  single structural call returns. That case is untested here.
- Ground truth was audited by one rater.

## Reproduce

```bash
uv run python docs/phase-6/structural-mcp-trial/scripts/truth.py --selftest
uv run python docs/phase-6/structural-mcp-trial/scripts/trial.py prepare
uv run python docs/phase-6/structural-mcp-trial/scripts/trial.py run 1 3   # and 2
uv run python docs/phase-6/structural-mcp-trial/scripts/trial.py grade docs/phase-6/structural-mcp-trial/results/runs.json
uv run python docs/phase-6/structural-mcp-trial/scripts/analyze.py docs/phase-6/structural-mcp-trial/results/runs.json docs/phase-6/structural-mcp-trial/results/report.json
```

`results/runs.json` holds every graded run, dev included (`rep: "dev"`, excluded from
the criteria). Transcripts are in `$OXIDE_TRIAL_DATA/runs/`, outside git.
