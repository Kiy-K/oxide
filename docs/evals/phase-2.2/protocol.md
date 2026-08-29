# Phase 2.2 — Activation-layer evaluation protocol

## 0. Correction to the phase brief: no MCP exists in this repo

The Phase 2.2 brief as originally drafted assumes an MCP server, an
`initialize`-time instruction payload, and a set of Phase 2.1 findings
("OpenCode models frequently ignored OXIDE," "known-file controls
correctly avoided OXIDE," etc.) as an established baseline.

None of that exists in this repository or its history:

- `git log`/`docs/` contain no Phase 2.1 artifacts. The only prior
  real-agent evidence is `docs/compact-toolset-evaluation.md` ("section
  I"), a 6-cell, n=1-per-cell CLI experiment with `opencode` — not MCP.
- `src/` has no MCP code at all. `README.md` explicitly describes MCP as
  "a future phase" (`grep -n mcp README.md`).
- OXIDE's only real agent-facing transport today is the CLI:
  `oxide context --task ... --json` and `oxide search "..." --json`,
  exactly as documented in `docs/agent-usage-policy.md` and
  `skills/oxide-code-context/SKILL.md`.

**Every finding this phase attributes to "Phase 2.1" or to MCP behavior in
the original brief is invalid and superseded.** There is nothing to
invalidate empirically because nothing was ever measured — the six
findings listed in the brief have no artifacts backing them in this repo
and must not be cited as prior evidence. This document and
`results.jsonl` are the actual first evidence.

Per the user's correction mid-session: this phase evaluates the CLI
surface that actually exists, does not implement MCP, and treats any
future MCP work as out of scope for Phase 2.2 — a future MCP server should
use `modelcontextprotocol/rust-sdk` (`rmcp`) rather than a hand-written
protocol adapter (see `recommendation.md` §7).

## 1. Frozen core (verification gate, run before any eval)

```
commit:  d14943b1ffdb549dd7d1cdd861afc4dc7ec6d07e
release binary sha256: 3aa6cf3e13858cf249ffa914da2679a15017023158214e9002f9c075698c1de8
```

- `cargo fmt --check` — clean
- `cargo clippy --all-targets -- -D warnings` — clean
- `cargo test` — all pass
- `./target/release/oxide eval --config fixtures/benchmark.json` — hybrid
  recall@5 0.909 ≥ vector-only 0.818 (benchmark_gate invariant holds)
- `git diff --check` — clean

No product code changed during this phase (verified again in the
verification-gate re-run before the phase was declared done). Only
`docs/evals/phase-2.2/**` was added.

## 2. Conditions (CLI-transport substitution for the brief's MCP A–E)

The user's mid-session correction pins these exactly:

| Condition | Setup |
|---|---|
| **A — baseline** | `oxide` binary not resolvable on `PATH` at all; no mention anywhere. Pure native-tool baseline. |
| **B — CLI available, no instructions** | `oxide` on `PATH` via a symlinked bin dir; task prompt gets one added sentence noting it exists (`try oxide --help`), no usage guidance at all. Tests bare tool-visibility. |
| **C — CLI + existing SKILL.md** | `oxide` on `PATH` + `skills/oxide-code-context/SKILL.md` copied into the task repo at `.opencode/skills/oxide-code-context/SKILL.md` (opencode's project-skill discovery path — confirmed via string search in the installed opencode binary: `.opencode/skills`). No AGENTS.md. |
| **D — CLI + tiny AGENTS.md rule** | `oxide` on `PATH` + a repo-root `AGENTS.md` containing only the ~55-token block the user specified (see below). No skill. |
| **E — CLI + SKILL.md + AGENTS.md** | Both C and D combined. |

AGENTS.md block used verbatim for D and E (as specified by the user):

```markdown
## OXIDE

For unfamiliar multi-file coding tasks, use `oxide context` before broad
repository exploration. Use `oxide search` for focused follow-up
discovery. For exact known-file or literal tasks, use normal tools
directly. Read source before editing.
```

(≈55 words / ≈300 chars / ≈75 tokens at 4 chars/token.)

## 3. Agent / model

- Client: `opencode` 1.18.25, `opencode run --format json` (structured
  JSON tool-event stream — no stdout scraping needed).
- Model: `opencode/muse-spark-1.2-contributor-free` (the only model
  confirmed available in this environment's `opencode auth`/`opencode
  models`; matches the model used in `docs/compact-toolset-evaluation.md`
  and `scripts/agent_eval/tierb_agent_run.py`).
- Codex CLI (`codex-cli 0.150.1`) and Claude Code (`2.1.251`) are present
  and authenticated in this environment but were **not** run as
  confirmation clients in this pass — see `failures.md` /
  `recommendation.md` for why, and treat this as a scoping decision under
  §7's cost-reduction allowance, not a finding.

## 4. Task set (10 tasks — 4 Bucket A / 2 Bucket B / 4 Bucket C)

Reused fixture repos already committed and benchmark-pinned
(`fixtures/py_repo`, `fixtures/ts_repo`), each freshly `oxide index`-ed
per run copy (index meta's `root` is an absolute path baked in at index
time — copying a pre-built `.oxide/index.db` to a new path would leave
lexical body reads pointed at the wrong directory, so every run
re-indexes its own copy; this is a `<10ms` no-op cost for repos this
size). No new fixture repos were authored, per the "reuse existing
fixtures/tasks" cost-reduction guidance.

Bucket A (unfamiliar multi-file, no location given — should activate):
`A1` retry-eligibility localization (py), `A2` cache-expiry localization
(py), `A3` token-refresh localization (ts), `A4` backoff-delay
localization (ts). All are report-only ("do not edit anything") to keep
per-run cost down; see §13 note below on why this cannot license coding-
quality claims.

Bucket B (subsystem named, exact implementation unknown — optional):
`B1` find the specific retry-exhaustion test (py), `B2` find every
importer of `VersionedStore` (ts).

Bucket C (exact file given, trivial single-file edit — should NOT
activate): `C1`–`C4`, one rename/one-line edit each in a named file, py
and ts.

Full prompts: `raw/run_activation_eval.py` (`TASKS` list) — kept
alongside the harness rather than duplicated here so they can't drift.

## 5. Instrumentation

`opencode run --format json` streams one JSON event per line, including
`type:"tool"` parts with `tool` (bash/read/grep/glob/list/edit/...) and
`state.input` (the actual command/path). This is a structured tool-call
log, not a stdout-scrape proxy — no wrapper shim was needed. Per run we
extract: every `oxide context`/`oxide search` invocation (regex over bash
command text, since oxide is invoked as a shell command), native
read/grep/glob/list counts, non-oxide bash count, the first tool call
(and whether it was an oxide invocation), total tool calls, and summed
token usage from `step_finish` events. Raw JSONL logs are kept per run
in `logs/<task>-<condition>-r<rep>.jsonl`.

## 6. Known confounds (recorded, not fixed)

- **Ambient plugin/persona layer.** This machine's global opencode config
  (`~/.config/opencode/opencode.json`) loads a `ponytail` plugin that
  colors every reply's tone ("lazy senior dev" persona) regardless of
  condition. `opencode run --pure` (which disables external plugins)
  reliably hung with zero output and no stderr in this environment —
  plausibly because the same plugin layer also carries the
  `ClinePass`/`opencode` provider auth path, so `--pure` breaks
  authentication rather than giving a clean baseline. This means a truly
  "clean isolated config" arm (brief §18) was not achievable here; the
  ponytail confound is instead constant across every condition A–E, so
  differential comparisons between conditions remain valid even though no
  condition is persona-free. Global `~/.config/opencode/AGENTS.md` only
  injects a CodeGraph block gated on `.codegraph/` existing in the target
  directory; the task-repo copies never carry `.codegraph/`, so that
  global file is inert for this eval and not a source of oxide-specific
  bias.
- **Provider flakiness under burst load.** Rapid sequential `opencode run`
  calls against the free-tier `muse-spark` model intermittently hang with
  zero stdout/stderr for the full timeout, unrelated to task content or
  AGENTS.md presence (confirmed by re-running the identical repo/prompt
  pair and getting a normal ~4–20s response). The harness retries once on
  timeout and records `timed_out`/`retried`; a run still timed out after
  retry is excluded from activation-rate denominators and reported
  separately as an infrastructure failure (§17 category `INFRASTRUCTURE`),
  never silently folded into "missed activation."
- **Tiny fixture repos.** `fixtures/py_repo`/`fixtures/ts_repo` are 7
  files each. On repos this small, `grep`+`read` alone is often as fast
  as reaching for `oxide`, which can make even Bucket A tasks resolve
  without OXIDE regardless of instruction layer — this is a real
  limitation on how strongly this fixture size can demonstrate
  activation benefit, not a null result about larger repositories. Noted
  wherever it appears to explain a cell, not used to explain away an
  inconvenient one.

## 7. Coding-outcome tier (§13, reduced scope)

`eval-agent/tasks/py_bug_retry` (already in the repo, has `verify.sh`
running `python3 -m unittest discover`) has a genuine bug: `backoff_ms`
shrinks instead of growing (`# BUG:` comment already in the source), with
a pre-existing failing-until-fixed test. Reused verbatim as the one real
edit-and-verify task, run under conditions A and D only (the two poles
the phase's definition-of-done cares about), reduced reps, rather than
authoring new fixtures or running the full 5-condition matrix on it.

## 8. Deviations from the original 10-condition/high-repetition brief

Per the brief's own §7 allowance ("if cost/runtime is too high,
prioritize Bucket A activation and Bucket C false-positive activation...
do not discard failures"):

- Reps: 2 per (task, condition) for the main navigation matrix (100 runs
  total), not 3–5. Ambiguous/surprising cells get called out explicitly
  in `activation-results.md` rather than silently averaged over n=2.
- Confirmation clients (Codex CLI, Claude Code) and the OpenCode
  `codemode`/tool-exposure-mode experiment (brief §3) were not run — see
  `recommendation.md` for the concrete follow-up if a future pass wants
  them.
- Only one real coding-outcome task, not four, and only 2 of 5
  conditions.

These are documented trade-offs, not silent scope cuts.
