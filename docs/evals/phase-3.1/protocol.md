# Phase 3.1 protocol — MCP vs CLI real-agent evaluation

Governing question (per the phase brief): does the real `rmcp` MCP transport
improve coding-agent context acquisition enough to justify its complexity
compared with the already-proven CLI + Skill integration, and what happens
when both are available simultaneously?

## 0. Frozen baseline

- Commit: `b353223a2c11fc08ed1143e0dfef48aad009a29c`
  (`refactor: migrate MCP transport to official rmcp SDK`), working tree
  clean before this phase's work began.
- OXIDE version: `0.1.0` (`Cargo.toml`).
- `rmcp` pinned: `=3.1.4` (`Cargo.toml:16`, `default-features = false`,
  features `server, macros, transport-io, base64`).
- Release binary: `target/release/oxide`,
  sha256 `fb5aa0a5ef7c293d6fe279f00d00d0bb3914a738ceda5a92548cc76ef652f6b0`.
- Gate, re-run fresh at the start of this phase:
  - `cargo fmt --check` — clean.
  - `cargo clippy --all-targets -j 2 -- -D warnings` — clean.
  - `cargo test -j 2` — 95 passed (0 failed), including `tests/mcp_e2e.rs`'s
    7 rmcp-parity tests (`initialize_and_list_expose_only_compact_agent_tools`,
    `malformed_parameters_and_service_failures_preserve_structured_semantics`,
    `unavailable_embedder_preserves_fallback_action`,
    `concurrent_mcp_reads_return_valid_results`,
    `context_and_search_return_service_evidence_over_real_protocol`,
    `repository_and_incompatible_index_errors_keep_service_actions`,
    `repeated_reads_are_deterministic_and_empty_search_is_valid`).
  - Canonical benchmark (`oxide eval --config fixtures/benchmark.json`):
    hybrid recall@5 `0.909`, vector-only recall@5 `0.818` — matches
    `docs/canonical-baseline.md`.
  - `git diff --check` — clean.
- **Exact MCP tool schemas**, captured live from the running `rmcp` server
  (not copied from Phase 2.1's pre-migration hand-rolled adapter — see §1):
  `tools/list` response is **590 chars / ~148 tokens** (chars/4 estimate);
  server `instructions` string is **280 chars / 70 tokens**, byte-identical
  to Phase 2.1's pre-migration string (`SERVER_INSTRUCTIONS` in `src/mcp.rs`
  was not touched by the rmcp migration). Raw capture:
  `raw/mcp_probe_result.json` (script: `raw/mcp_probe.py`).
- **Validated CLI activation snippet (E1, Phase 2.3's winner)**, used
  verbatim for condition B and (unmodified) condition E:

  ```markdown
  ## OXIDE

  For unfamiliar repository work where the implementation path is not
  already known, use `oxide context` before broad grep/read exploration.
  Use `oxide search` for focused follow-up discovery. For exact known-file
  or literal tasks, use normal tools directly. Read source before editing.
  ```

  ~70 tokens, unchanged from `docs/evals/phase-2.3/policy-variants.md`.
- **Skill**: `skills/oxide-code-context/SKILL.md`, unchanged since Phase 2.2,
  used verbatim for conditions B and E only (no MCP-specific skill exists or
  was created — MCP tools are self-describing via `tools/list`, which is the
  point being tested).

## 1. Why schema sizes were re-measured, not reused

Phase 2.1's protocol.md reports `context`/`search` schemas at 285c/302c
individually from the pre-rmcp hand-rolled `tool_definitions()` adapter.
`docs/mcp-phase-2-report.md` explicitly flags that framing/lifecycle/version
negotiation are now owned by `rmcp`, which serializes `tools/list` itself and
may reshape the wire format. `raw/mcp_probe.py` is a small scripted stdio
JSON-RPC client (not an agent) that speaks directly to `oxide mcp` and
measures the actual bytes on the wire today. Result: combined `tools/list`
size is close to Phase 2.1's combined total (590c here vs. 590c there:
285+302=587≈590), i.e. rmcp did not measurably inflate the schema — this is
a real finding, not an assumption.

## 2. Known confound: ambient `codegraph` MCP server

`~/.config/opencode/opencode.json` registers a `codegraph` MCP server
globally (`enabled: true`), and — confirmed directly with `opencode mcp
list` during this phase's setup — a custom `OPENCODE_CONFIG` **merges with**
the global config rather than replacing it; explicitly setting
`"codegraph": {"enabled": false}` in the temp config is required and does
override the merge (verified: `opencode mcp list` shows `codegraph ○
disabled`, `oxide ✓ connected` under a config that disables one and enables
the other). Every condition's isolated `opencode-config.json`
(`raw/run_matrix.py:build_opencode_config`) explicitly disables `codegraph`
and a placeholder `codebase-memory-mcp` entry. `--pure` alone (Phase 2.3's
approach) was **not** used, since Phase 2.3 already found it insufficient.

## 3. Tool visibility confirmed before the matrix ran

Per the smoke-test step (advisor guidance: transport registration succeeding
is not evidence the model saw the tools), a scripted `opencode run
--print-logs` probe asked the model to list its own tools under condition C.
Model's answer: `default.bash, default.edit, default.glob, default.grep,
default.oxide_context, default.oxide_search, default.read, default.skill,
default.task, default.todowrite, default.webfetch, default.websearch,
default.write` — OXIDE's MCP tools are visible to the model under the
namespaced tool ids `oxide_context` / `oxide_search` (no `default.` prefix
in actual `tool_use` events — confirmed separately from a real MCP-activated
run's log). The harness's event classifier
(`raw/run_matrix.py:analyze`) matches on `"oxide" in tool_name and
"context"/"search" in tool_name`, which correctly catches this naming.

## 4. Conditions

| Cond | CLI on PATH | Skill | AGENTS.md | MCP `oxide` registered |
|------|:-----------:|:-----:|:---------:|:-----------------------:|
| A — native baseline | no | no | no | no |
| B — CLI production | yes | yes (`oxide-code-context`) | E1 (CLI wording) | no |
| C — MCP only, minimal guidance | no | no | no | yes |
| D — MCP + persistent guidance | no | no | E1-adapted, transport-generic wording (`D_AGENTS_MCP` in `raw/run_matrix.py`) | yes |
| E — CLI + MCP simultaneously | yes | yes | E1 (CLI wording, **unmodified**) | yes |

Design choices and why:

- **C and D have no CLI binary on PATH at all.** This is a deliberate
  isolation decision so C/D measure MCP's own discoverability/guidance in
  the absence of a CLI fallback, per the phase brief ("no CLI Skill
  activation path"). It is stronger than "CLI present but unmentioned" —
  document this as a design choice, not a hidden default.
- **E adds no MCP-specific wording** beyond what B already has. The phase
  brief explicitly asks whether *unmodified* CLI guidance plus MCP
  registration causes redundant retrieval — adding new MCP prompt text to E
  would confound that question.
- **Every condition except A pre-builds the OXIDE index** (`oxide index
  <repo> --json`, release binary, hashed offline embedder — no
  `$OXIDE_EMBED_URL` set, matching the benchmark gate's deterministic
  config) before the agent runs. MCP exposes no indexing tool by design
  (scope exclusion in the phase brief: "no automatic indexing"), so without
  a pre-built index, C/D would be testing "index missing" error behavior
  (covered separately in §16) rather than the intended context-acquisition
  question.

## 4.5. Mid-batch model switch (muse-spark went down, exactly as in Phase 2.3)

After 106 of 170 navigation runs (all of Bucket A and Bucket B fully
complete across all 5 conditions — see below), `opencode/muse-spark-1.2-
contributor-free` began returning empty output on every call (no stdout,
no stderr, silent hang to the 200s timeout, both the run and its automatic
retry) starting partway through task C1. Confirmed as a real model/provider
outage, not a harness bug, with a standalone health check
(`opencode run -m opencode/muse-spark-1.2-contributor-free "say hi in one
word"`, isolated from the matrix, no repo/config involved) that also hung
to a 40s timeout. This is the same failure signature Phase 2.3 hit
mid-batch (`docs/evals/phase-2.3/protocol.md` §-1).

**Note on data quality of the 106 muse-spark rows already collected**:
looking back at them post-hoc, timeouts were not confined to the point of
final failure — `A` condition nav runs alone show 6/21 timed out, `E`
shows 5/30 — i.e., muse-spark was intermittently flaky throughout, not
cleanly healthy-then-broken. All timeout rows are retained
(`timed_out: true`) and excluded from activation-rate denominators
(`raw/analyze.py`), per the phase brief's "infrastructure failures must be
retained and classified separately" instruction — not silently dropped or
retried away.

**Resolution**: followed Phase 2.3's exact playbook — switched the
remaining, not-yet-attempted cells to `openai/gpt-5.6-luna` (confirmed
healthy via the same standalone health check), which Phase 2.3 found
requires `--auto` to make any tool call at all (`raw/run_matrix.py`:
`EXTRA_ARGS = ["--auto"] if "gpt-5.6-luna" in MODEL else []`). The runner's
resumable design (keyed by `(kind, task, condition, rep)`) meant no
already-recorded row — success or timeout — was re-run or overwritten;
the switch only affects cells that had not yet been attempted.

**What this means for the split dataset, stated plainly**: Bucket A (4
tasks) and Bucket B (2 tasks) navigation results are **muse-spark only**,
complete across all 5 conditions at full reps (3 for A–D, 5 for E) — this
is the core "does OXIDE help when it should" data and it is a clean
single-model dataset. Bucket C (4 tasks, "should not activate") and both
real coding tasks are **gpt-5.6-luna only** (task C1 got a partial,
excluded-from-tables muse-spark contribution before the switch: 3 rows
under condition A, 1 timed-out row under condition B). **These two
sub-datasets are never pooled into one activation percentage** — Bucket
C's false-positive rate and the coding-outcome numbers are reported
separately, on their own model, exactly as Phase 2.3 did for its E0-vs-E1
comparison after its own mid-phase switch.

## 4.6. New ambient confound found: a global `~/.agents/skills/` directory

While health-checking `gpt-5.6-luna`, a trivial "say hi in one word"
prompt (no repo, no OXIDE config at all) triggered a `skill` tool call
loading `/home/khoi/.agents/skills/using-agent-skills/SKILL.md` — a large
(~700-line) generic engineering-workflow meta-skill unrelated to OXIDE,
present at the user level and independent of the isolated
`OPENCODE_CONFIG` used throughout this phase (it carries no `plugin` or
skill-path entry that would explain this). The same ambient directory
(`~/.agents/skills/*`) was also observed leaking into Codex CLI sessions
under an isolated `CODEX_HOME` (`client-compatibility.md`) — a
cross-client ambient surface neither OpenCode's `OPENCODE_CONFIG` override
nor Codex's `CODEX_HOME` override actually isolates. **This does not
corrupt the CLI-vs-MCP classification** (the analysis matches specifically
on `"oxide"` substrings in tool names, and this ambient skill's name
contains no such substring), but it is a real, previously-undocumented
context-cost and first-action confound: a `first_action: skill` entry in
`results.jsonl` may reflect this ambient meta-skill rather than
OXIDE's own skill, and neither this phase nor Phase 2.1–2.3 fully isolated
against it. Recorded here rather than silently absorbed into the
first-action tables.

## 5. Model and clients

- **Primary / full matrix: OpenCode, two models by necessity (§4.5)**.
  `opencode/muse-spark-1.2-contributor-free` (free tier) was chosen first,
  matching Phase 2.1/2.2's model for continuity and because its Bucket-A
  activation was **not saturated** in Phase 2.2 (54%, vs. gpt-5.6-luna's
  100% in Phase 2.3) — a saturated headline metric would make the core B-vs-D
  comparison uninformative (advisor guidance, confirmed against Phase 2.3's
  own numbers). It went down mid-batch on what looks like the same
  provider-side issue Phase 2.3 hit; **Bucket A/B navigation results are
  muse-spark-only, Bucket C and the coding tasks are `openai/gpt-5.6-luna`-
  only** (§4.5) — this phase's runner
  (`raw/run_matrix.py:load_done_keys`) is resumable and append-only, keyed
  by `(kind, task, condition, rep)`, specifically to survive this.
- **Claude Code and Codex CLI**: both confirmed present and authenticated in
  this environment (`claude`, `codex` on PATH). Given the full matrix's
  ~200-run scope on OpenCode alone, Claude Code/Codex are used for a
  smaller B-vs-D subset plus client-compatibility notes (§ client-
  compatibility.md), not the full A–E matrix — consistent with the phase
  brief's "at least two model families" (OpenCode/muse-spark +
  Claude/Codex's underlying models are different families) without
  attempting to replicate ~200 runs three times over. Codex has no Skill
  mechanism, so its "condition B" is AGENTS.md-only and is reported
  separately, never pooled with OpenCode's Skill+AGENTS.md B.

## 6. Tasks

Reused verbatim from Phase 2.2's pinned set (`docs/evals/phase-2.2/raw/
run_activation_eval.py`) for continuity: 4 Bucket-A, 2 Bucket-B, 4 Bucket-C
navigation tasks over `fixtures/py_repo` / `fixtures/ts_repo`. Two real
coding tasks with acceptance tests are added from `eval-agent/tasks/`
(`py_bug_retry`, reused from Phase 2.2/2.3's coding-outcome tier; `ts_bug_store`,
new to this phase, chosen because its bug — `VersionedStore.set()` never
advances the version counter — needed for a second, independent real bug
task with a runnable test suite). Both were confirmed to fail their test
suite (`verify.sh`) on the unmodified fixture before any agent touched them.
Full text: `tasks.md`.

## 7. Repetitions

- Navigation tasks: 3 reps × condition, except **condition E: 5 reps**, per
  advisor guidance — E is the phase's primary novel contribution (transport-
  selection behavior), while A–D's individual behavior is largely re-
  confirming Phase 2.1 (MCP) and Phase 2.2/2.3 (CLI).
- Coding tasks: 3 reps × condition (5 for condition E, same rule).
- All runs are logged to `logs/<task>-<condition>-r<rep>.jsonl` (raw
  `opencode run --format json` event stream) and summarized append-only to
  `results.jsonl`, one JSON object per run, resumable by
  `(kind, task, condition, rep)` key.

## 8. Metrics carrying the B-vs-D verdict

Following advisor guidance that raw Bucket-A activation can saturate at
100% on some models (Phase 2.3), this phase's B-vs-D comparison leans on:
first meaningful discovery action (`first_action`), discovery-call counts to
first relevant evidence, context economics (§0/§1 sizes plus
`context-economics.md`), transport-selection outcome in condition E
(`transport-selection.md`), and downstream coding-task success
(`results.md`). Raw Bucket-A/C activation percentages are still reported for
continuity with Phase 2.1–2.3, but are not treated as the sole verdict.

## 8.5. Post-review correction: discovery-efficiency classification

`raw/analyze.py`'s original `native_explore_calls` field (used for the
first draft of `results.md` §5/§6 and `recommendation.md`) treated every
native `read` before an OXIDE call as equally avoidable — which wrongly
scored `read AGENTS.md → load OXIDE skill → oxide context` as a delayed-
activation failure, and wrongly scored condition E's higher raw tool-call
count as an efficiency defect when it was mostly legitimate post-
retrieval source reading. Corrected via a second analysis pass,
`raw/analyze_discovery_quality.py`, which re-parses the raw per-run event
logs (`logs/*.jsonl` — `results.jsonl` itself is untouched) and classifies
every pre-OXIDE tool call into `INSTRUCTION_READ` (AGENTS.md/SKILL.md, or
a `skill` tool call), `PROJECT_ORIENTATION_READ` (README/manifest/bare-
directory reads), `DIRECT_TARGET_READ` (a file the task names explicitly),
`IMPLEMENTATION_EXPLORATION_READ` (genuine avoidable exploration), and
`OTHER_NATIVE_DISCOVERY` (grep/glob/bash, unchanged). Only the last two
count as "avoidable exploration." This also surfaced a real artifact
(documented in `results.md` §5): 18/68 valid Bucket-A runs, spread evenly
across all five conditions, are a single-call degenerate session where
muse-spark calls `read` on `/` (the filesystem root), gets permission-
denied, and gives up — a model quirk unrelated to OXIDE, not genuine
project-orientation reading. `results.md` §5/§6 and `recommendation.md`
§1/§2 were revised accordingly; §20's overall verdict (MCP wins the B-vs-D
comparison) did not change, but the specific case made against condition
E (previously: less efficient; now: same efficiency, higher persistent
cost) did.

## 9. Verification gate (repeated at end of phase)

`cargo fmt --check`, `cargo clippy --all-targets -- -D warnings`, `cargo
test`, canonical benchmark, `git diff --check`. This phase changes no
product code — the gate is expected to be byte-identical to §0's baseline
run; any diff is a bug in this phase's work, not an expected side effect.
