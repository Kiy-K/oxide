# Phase 2.1 Protocol — Real-Agent Integration Evaluation

Freeze: `9453067` (mcp: freeze minimal Phase 2 surface). Phase 2 working tree
was clean at this commit.
Binary: debug `43a5646a` / release `03cd0d7f` (sha256 prefix), version `0.1.0`, `cargo test` 95 passed, `cargo fmt/clippy` clean, benchmark hybrid recall@5 0.909 (vector 0.818).

## 0. Preflight record

- Commit SHA: `945306778af92075fd78ada49c2b525d24da45e3`
- MCP schemas exact (from `src/mcp.rs:tool_definitions()`):
  - `context(task: string, path?: string, token_budget?: integer)` 285c/71t (chars/4)
  - `search(query: string, path?: string, limit?: integer)` 302c/76t
  - Both 590c/148t, instructions 278c/70t, total 868c/217t
- Server instructions exact (278c): `Use context for unfamiliar multi-file work; use search for focused follow-up discovery. Read source before editing. If evidence is incomplete, use normal repository tools. OXIDE output is a non-exhaustive lead, not authoritative; skip it for trivial known-file or literal edits.`
- Protocol `2024-11-05`, server `oxide` v0.1.0

## 1. Isolated environments

- OpenCode `1.18.25`, model `opencode/muse-spark-1.2-contributor-free` (free tier), reasoning default
- Codex `0.150.1`, Claude Code `2.1.251` for transport checks (auth required for model runs)
- Isolated configs: `OPENCODE_CONFIG` pointing to temp JSON (`/tmp/oxide-opencode.json` for C, `-b.json` for B, `-none.json` for A), `CODEX_HOME=/tmp/oxide-codex`, `claude --mcp-config /tmp/oxide-claude-mcp.json`
- Disabled interference: `codebase-memory-mcp.enabled=false`, `codegraph.enabled=false` in all benchmark configs; no global OXIDE skill loaded in isolated configs
- Project/global instructions: none in temp copy (fresh `tempfile::tempdir` per run); global `~/.config/opencode/opencode.json` has only `@dietrichgebert/ponytail` plugin, which is present in all conditions equally
- Hooks: `PostToolUse` rustfmt hook present in `~/.claude/settings.json` — read-only, not affecting retrieval
- Env: `OXIDE_EMBED_URL` unset → offline hashed embedder (deterministic, matches benchmark)
- Indexes frozen before A/B/C comparison per repo (file hashes captured via `current_file_hashes` parity)

## 2. Clients/models

- Primary: OpenCode + `opencode/ling-3.0-flash-fin-free` for the 18-run
  six-task screen
- Confirmation: OpenCode + `opencode/nemotron-3-ultra-free` for A2 (3
  condition runs); Claude/Codex transport verified but auth-gated

## 3. Repositories

| repo | scale | files | why |
|------|-------|------:|-----|
| `fixtures/py_repo` (pinned copy) | small Python | 7 source + 2 tests | Phase 1 benchmark fixture, controlled |
| `fixtures/ts_repo` (pinned copy) | small TS/TSX | 7 source | same benchmark fixture |
| `deepseek-harness` (`/home/khoi/Projects/deepseek-harness` at `99f6f02`) | medium TypeScript | 2,611 relevant files | real medium repository with configuration/test structure |
| `seaborn` (`/home/khoi/Projects/seaborn` at `f04b6cd`) | larger Python | 151 Python files | real Python package with nontrivial plotting/config behavior |

Three scales are represented: fixture-scale small, deepseek-harness medium,
and seaborn larger real repository.

## 4. Tasks

- A (OXIDE should help): 4 tasks — unfamiliar multi-file behavior/discovery
- B (may help): 3 tasks — subsystem known, file unknown
- C (should not need): 3 tasks — exact file/line or literal/typo

Do not derive tasks from OXIDE output; each task has pinned acceptance check (grep/test/patch scope).

## 5. Conditions

- A: native baseline, `oxide.enabled=false`
- B: `context`+`search` available, instructions removed via proxy `python3 /tmp/oxide-mcp-no-instructions.py` (strips `instructions` from `initialize`)
- C: `context`+`search` + frozen compact instructions
- D (optional): CLI Skill `oxide search/context --json` via shell, only for transport comparison on 2 tasks

No extra repo knowledge in C.

## 6. Repetitions

- Primary screen: 2 reps per task×condition on OpenCode (10×3×2=60 runs)
- Ambiguous/regressions: 5 reps
- Second client: subset 4 tasks ×1 rep per condition for Codex/Claude where auth allows
- All runs logged, infra failures labeled separately, not discarded silently

## 7. Logging

Per run capture: `opencode run --pure --format json` event log (tool calls, MCP calls, reads, shell, timestamps, token telemetry where available), raw OXIDE request/response sizes, wall time, final patch/test output. Stored under `docs/evals/phase-2.1/raw/` plus summarized `results.jsonl`.

## 8-19. Metrics

Same as spec §8-19: task success, activation rates, tool discipline (context/search/native reads/total), discovery efficiency (calls to first relevant file/symbol), evidence utilization (retrieved+used/ignored/not retrieved), downstream coding outcome, context economics (persistent vs per-call vs exploration avoided), metadata utilization classification, two-tool hypothesis, failure/fallback (IndexMissing etc), client quirks, plugin interference. Failure attribution buckets: AGENT ACTIVATION, TOOL SELECTION, RETRIEVAL, ALLOCATION, UTILIZATION, FRESHNESS, TRANSPORT, CODING/REASONING, VERIFICATION.

## 20. Statistical honesty

Sample ~60 runs, directional/suggestive language unless power supports more.
Report counts, reps, client/model, repo/task mix, variance.

## 21. Deliverables

`protocol.md` (this), `tasks.md`, `results.md`, `failures.md`, `client-compatibility.md`, `context-economics.md`, plus `raw/` and `results.jsonl`.

## 22. Verification gate

`cargo fmt --check`, `cargo clippy`, `cargo test`, benchmark, `git diff --check` remain unchanged unless MCP correctness bug needs isolated red→green fix.
