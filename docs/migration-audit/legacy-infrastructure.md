# Post-rewrite infrastructure audit

Audited 2026-10-21 on `rewrite/v2`, starting at `c70786db`. This is an infrastructure
audit, not a retrieval experiment. No baselines, derivation versions, language
support, public exports or dependency pins changed. The MCP contract remains one
tool, `oxide_search`.

## Repository safety

Inspected all six registered worktrees before editing:

| Worktree | Starting state | Treatment |
| --- | --- | --- |
| `/home/khoi/Projects/oxide` | User edits/deletions in agent configuration, Go evidence, logo and skill lock; untracked user files; staging area empty | Preserved; cleanup uses separate paths |
| `/home/khoi/.cache/oxide-ci-wt` | Modified platform/API docs, distribution smoke, storage and native tests | Read-only |
| `/home/khoi/.cache/oxide-treeselect-eval/wt` | Added Rust experiment, modified Rust context | Read-only |
| `/home/khoi/.cache/oxide-ts-eval/before` | Added Rust experiment, modified Rust context | Read-only |
| `/home/khoi/Projects/oxide-eval-data/oxide-valuepack-eval/wt` | Clean | Read-only |
| `/home/khoi/Projects/oxide-treeindex` | Untracked research protocol/generator | Read-only |

In particular, the existing deletions under `.agents/skills/jev-model-router/`
and edits to `.codex/config.toml`, `.claude/settings.json` and `skills-lock.json`
are not this cleanup. Neither they nor untracked research/configuration files
were incorporated or restored. Ignored caches and external worktrees were not deleted.

## Before / after inventory

Counts describe tracked paths at the starting revision, including paths already
deleted by the user. Generated directory sizes are local observations, not package sizes.

| Component | Before | After / reason |
| --- | --- | --- |
| Production Rust/JS/TS source, root Cargo/npm/Bun manifests and toolchain pins | None | None; `src/oxide/` is Python |
| Required development tool entrypoint | Mise, pinned uv 0.12.22, Python 3.13 | Unchanged; no Cargo, Bun or Node command in Mise tasks |
| Rust files | 11 | Retained: seven historical regression tests, three conformance fixtures, one migration parity probe |
| Cargo manifests | One, `docs/migration-audit/parity/Cargo.toml` | Retained with its Rust migration reproduction, not part of the build |
| Historical evaluation | `eval-agent/`, `scripts/agent_eval/`, `scripts/perf.sh`, `scripts/gen_bench_repo.py` | Retained: SPEC Historical evidence explicitly protects them |
| Historical docs and skill | Phase reports, ADRs, `docs/legacy/`, `skills/oxide-code-context/` | Retained; the old skill explicitly identifies itself as a v1 reference, not v2 instructions |
| Committed JS dependencies | 725 files under `eval-agent/tasks/tsx_feat_button/node_modules/`, task `package.json` and `bun.lock` | Retained as historical task fixture/reproduction content, excluded from distributions |
| Active TS fixture | `fixtures/ts_repo/package.json`, `tsconfig.json` | Retained: parser/module-resolution input; no npm install or JS execution |
| Archify | Vendored `.agents/skills/archify/`, package/lock, `.mjs` renderers, `.claude/skills/archify` symlink | Retained: optional diagram tooling requiring Node >=18; not an engine dependency |
| JEV agent plugin | `.claude/skills/jev-model-router/` copy, user-deleted `.agents/` copy | Preserved: third-party agent tooling, not the removed engine judge adapter; related user work is protected |
| Agent conventions | `.agents/skills/oxide/SKILL.md` still prescribed Rust, camelCase and TypeScript imports/exports | Removed stale instructions; matched the existing Python companion under `.claude/` |
| Generated agent profile | `.claude/identity.json` Rust tag; `.claude/homunculus/instincts/inherited/oxide-instincts.yaml` camelCase, relative TypeScript import and named-export rules | Identity now Python; removed three obsolete instincts and corrected the test-directory instruction |
| Active specification wording | One remaining requirement called thresholds "Rust policy" | Corrected to "deterministic policy", consistent with ADR-0011; ADR statuses/history untouched |
| Review workflow example | Commented filters for retired TS implementation | Removed six obsolete comment lines; review action retained |
| Python storage scaffold | Empty `if sys.platform == "win32": pass / else: pass` | Removed; real platform handling remains in storage; no behavior change |
| Distribution dependency installation | Could silently build third-party sdists | Platforms now requires third-party wheels, installs only OXIDE's artifact with `--no-deps`, and checks installed dependencies with `uv pip check` |
| Installed-artifact smoke | Inherited host PATH; called MCP without checking its tool list | Child PATH restricted to installed environment scripts; MCP must advertise exactly `oxide_search`; lock-holder gets the same private environment |
| Ignored root artifacts | `target/` about 2.1 GB (rustc 1.98.0 metadata); `node_modules/` about 161 MB (Biome, TypeScript, bun-types and type dependencies) | Preserved: generated remnants, not referenced by current workflows; untracked artifacts not authorized for deletion |

No entire source files, dependencies or manifests were removed. Remaining tracked
legacy sources have an external-tool purpose or an explicit evidence retention
requirement; generated remnants require separate deletion authorization.

## Why Node processes were running

Measured from host process ancestry, not inferred from filenames:

- The npm-installed Codex launcher (PID 70932) runs the native Codex binary;
  its app-server (71004) launches CodeGraph (71624), GitHub MCP (npm 71627,
  Node child 72799), Memory MCP (npm 71638, Node child 72954), and the computer-use
  Node REPL (71667). These are agent-host processes, not children of OXIDE.
- A separate Claude session (54898) launches CodeGraph (55216) and
  `npm exec rea-agents@6.5.0 mcp` (55217, Node child 55328). Its configuration
  origin was not inspected outside the repository.
- CodeGraph spawns a Node watchdog (`node -e`, e.g. 72134 and 55299).
  The installed `codegraph` shell launcher explicitly executes its bundled
  `~/.codegraph/versions/v1.6.2/node` with `lib/dist/bin/codegraph.js`.
  It does not need the PATH's system Node, but still executes Node.
- Repository `.codex/config.toml` configures five `npx` launchers: GitHub,
  Context7, Memory, Playwright and Sequential Thinking. This establishes
  configuration, not proof that all five were running in the snapshot.
- User-modified `.claude/settings.json` invokes `codegraph prompt-hook` at
  `UserPromptSubmit`. `.mcp.json` and `opencode.jsonc` also enable CodeGraph.
  The repository instruction to use CodeGraph means this audit itself executed
  its Node-backed CLI. Context7's two documentation lookups also used `npx`.
- The tracked formatter hooks in `.codex/hooks.json` and `.claude/settings.json`
  run `jq` and `uv run --locked ruff format` for Python edits. No active Git hooks
  or custom `core.hooksPath` were found; `.git/hooks/` contained samples only.

No subprocess launcher was found in production `src/oxide/`. CLI entrypoints
(`oxide.cli:main`, `python -m oxide`) call the public API; the MCP command lazily
imports the Python FastMCP server. Capture reads source bytes; tree-sitter parses
Python/TS/JS/Go in-process. `core/structural.py` optionally imports `ast_grep_py`,
not an npm CLI. FTS preparation uses Python HTTPS and digest verification.
Tests and smoke scripts launch Python/installed `oxide`; corpus fetching and
benchmark provenance use Git. Historical harnesses can invoke their old binaries,
Bun or external agents, but are outside the current development/distribution path.
Specifically, the TS/TSX evaluation tasks' `verify.sh` scripts run `bun test`
(and TSX runs `bun install`); `scripts/agent_eval/contextbench_run.py` uses
`target/release/oxide`; the Tier B harness invokes `opencode run`. None was
executed by this audit. In installed FastMCP/MCP source, the only `npx` match
was a configuration example in a docstring, not the `oxide mcp` launch path.
An AST import-closure check accounts for all 25 production modules, including
package initializers; it found no orphan legacy production module. This does not
claim that every function in every imported module is exercised.

This is a process snapshot and bounded runtime verification, not continuous host
tracing or proof about arbitrary external plugins.

## Remaining runtime and build requirements

| Dependency | Purpose and stage | Toolchain requirement |
| --- | --- | --- |
| CPython >=3.12,<3.15 | Installed application runtime | Python required |
| `ladybug==0.21.0` and pinned FTS 0.21.0 extension | Native C++ graph/lexical search, runtime | Prebuilt wheel/extension; no C++ or Rust compiler; Windows OpenSSL prerequisites remain in `docs/platforms.md` |
| tree-sitter and Python/TypeScript/JavaScript/Go grammars | Native source parsing, runtime | Python wheels; no source-language runtimes or compilers |
| `fastmcp==4.1.1`, `pathspec`, transitive Python packages | MCP transport/validation and ignore matching | Python packages/wheels, not npm packages |
| `pydantic-core`, `cryptography`, `rpds-py`, `watchfiles` | Rust-backed Python validation, crypto, persistent collections and watcher dependencies through FastMCP | Prebuilt Python extensions retained; no Rust toolchain for tested installs |
| Other native transitive wheels, including `cffi`, `caio`, PyYAML, websockets | Binding support, async I/O and MCP-stack dependencies | Retained; wheel-only install verifies no local native build |
| `ast-grep-py==0.51.0` | Optional `oxide[structural]` Python API; also in dev tests | Rust-backed Python wheel, not Node; no Cargo required |
| uv, uv_build, Mise, Ruff | Dependency management/build/task/lint tools; development/build only | Prebuilt tools may be implemented in Rust; no rustup/Cargo toolchain needed |
| pytest, mypy, Git, shell | Verification, corpus fetch/provenance, Mise scripts | Development only; ordinary installed indexing/querying needs no Git or shell |
| GitHub checkout/cache/mise/upload actions | CI orchestration | Runner-provided Node, independent of OXIDE packaging |
| Claude Code GitHub actions | Optional review and `@claude` automation | Third-party action installs Bun; justified CI-only functionality, not part of `mise run verify` |
| CodeGraph, Context7, external MCPs, archify and agent hosts | Optional/contributor agent tools | Node/Bun requirements belong to those tools; retained, not distributed with OXIDE |
| Historical harness toolchains | Reproducing old evaluations only | Rust/Cargo at historical revisions, Bun for old TS/TSX task verification, Python 3.11 for the legacy `tree-sitter-languages` evaluator; not current installation or development prerequisites |

Upstream action definitions were inspected on the audit date:
[checkout v7](https://github.com/actions/checkout/blob/v7/action.yml),
[cache v6](https://github.com/actions/cache/blob/v6/action.yml) and
[mise-action v5](https://github.com/jdx/mise-action/blob/v5/action.yml) declare
`node24`; [upload-artifact v5](https://github.com/actions/upload-artifact/blob/v5/action.yml)
declares `node20`. The
[Claude action v1](https://github.com/anthropics/claude-code-action/blob/v1/action.yml)
is composite and installs Bun 1.3.14 through `oven-sh/setup-bun`, then runs
`bun install --production`. These are mutable upstream major tags, not locally
observed hosted executions. Replacing useful Actions to eliminate their internal
runtime would add infrastructure without improving OXIDE installation.

## Verification receipts

All local checks below are Linux x86_64. Windows/macOS jobs were inspected, not
executed in this audit; their updated CI behavior remains pending hosted runs.

| Check | Result |
| --- | --- |
| Canonical `mise run verify`, final source | Python 3.13.16: Ruff, strict mypy (48 files), 265 passed, seven opt-in TS-gate cases skipped; 194.49 s |
| Frozen Phase 3/4 parity and Python ContextBench quality | Passed inside canonical verification; no oracle or baseline edits |
| Frozen TypeScript gate (`OXIDE_CONTEXTBENCH_TS=1`) | All 17 passed, including the seven opt-in corpus-gate cases; 988.24 s under two-core/3 GB limits; no baseline edits |
| Python 3.12.15 and 3.14.8 endpoint typing/tests | Strict mypy passed on each; 235 passed on each (77.89 s / 79.41 s), with explicit version assertions and pytest headers |
| Wheel and sdist installs, each on 3.12.15 / 3.13.16 / 3.14.8 | All six passed dependency checks and installed-artifact smoke; third-party dependencies restricted to registry wheels |
| Fresh wheel-metadata resolution without lock, each Python version | All three passed `uv pip check` and the installed smoke with wheels only |
| Final-source rebuild and wheel/sdist smoke on all three versions | All six passed, including exact single-tool MCP advertisement, indexing/edit/reopen, all four language fixtures, lexical search, context and cross-process lock |
| Optional `oxide[structural]`, all three versions | Wheel-only installation and Python API structural query passed without Node or Cargo |
| Actual `mise run bootstrap` into fresh `/tmp` dev environment | Passed with host toolchains absent from PATH; validated existing pinned FTS and found cached corpora; no fresh FTS/corpus download repeated |
| Distribution content | Wheel and sdist contain the Python package and metadata, no agent tools, `target`, npm packages, Cargo sources or historical harnesses |
| Additional static checks | Smoke-script Ruff lint/format passed; all four workflow YAML files parse and embedded scripts pass `bash -n`; all production source files <=500 lines |
| User-change preservation | Original unrelated tracked diff remained byte-identical; no staged user changes were present or included |

The first endpoint *development* attempts used `uv sync --python` followed by
`uv run`, which reselected `.python-version` (3.13). Those results are not endpoint
evidence. The rerun selects explicit interpreters, asserts their minor versions
and records the pytest platform header. Artifact smoke always used the target
environment's interpreter directly. The first systemd scope attempt lacked the
session bus variables and did not start tests; selecting the existing user bus
enabled the documented two-core/3 GB limits.

Raw local receipts live under `/tmp/oxide-infra-audit/` (individual step logs,
artifact inventories, native runtime wheel inventory, dependency requirements), `/tmp/oxide-infra-verify.py`,
`/tmp/oxide-infra-endpoint-verify.py`, `/tmp/oxide-infra-final-artifacts.py`,
`/tmp/oxide-infra-bootstrap.py`, `/tmp/oxide-audit-final-verify.log` and
`/tmp/oxide-audit-ts-gate.log`. They are temporary audit artifacts, not new frozen
research baselines. The locked dependency tree and import graph are
`/tmp/oxide-audit-dependency-tree.txt` and `/tmp/oxide-audit-import-graph.json`.

## Authorized generated-artifact removal (follow-up)

Starting from `905102de`, the user authorized removal of the two previously
preserved generated directories. This supersedes only the earlier inventory's
decision to leave those two artifacts on disk; historical evidence remains intact.

Removed exactly:

- `/home/khoi/Projects/oxide/target/`
- `/home/khoi/Projects/oxide/node_modules/`

Both were ignored, contained no tracked paths, resolved to real directories
directly under the main checkout, and had no nested mounts or cross-device entries.
The Cargo directory had 8,437 descendant entries and no symlinks. The npm directory
had 1,146 entries and two symlinks (`.bin/biome`, `.bin/tsc`), both pointing within it.
Device/inode identities were checked again immediately before descriptor-relative,
symlink-safe deletion. No recursive operation targeted another directory.

Before deletion, repeated same-user `/proc` inspection checked executables, working
directories, open descriptors, mapped files, arguments and environment references.
`lsof +D` also found no open users of either target. The only references were nine
npm-launched tools' inherited `PATH` entries for the root `node_modules/.bin`;
there was no open file, mapped library, executable, working directory, launch
argument or dedicated configuration selecting either target. Current development,
CI, packaging and Python benchmark scripts do not use those retired Biome/tsc/Cargo
outputs. The two protected processes that could not be fully inspected were the
user systemd manager and its PAM helper, not development/research workers. This is
a bounded process snapshot, not a claim about future commands in other sessions.

All six registered worktrees were rechecked read-only. Research worktrees,
CodeGraph, external MCP servers, optional agent tools, existing user edits/deletions
and untracked files were preserved. The 725 tracked files under
`eval-agent/tasks/tsx_feat_button/node_modules/` remain present and unchanged.

### Disk accounting

| Measurement | Bytes |
| --- | ---: |
| `target/` allocated blocks represented by removed entries | 2,236,022,784 |
| Root `node_modules/` allocated blocks represented by removed entries | 168,534,016 |
| Filesystem available space immediately before deletion | 326,822,391,808 |
| Filesystem available space immediately after deletion and sync | 327,848,165,376 |
| Observed free-space increase | **1,025,773,568 (about 978 MiB)** |

The removed paths represented about 2.24 GiB of allocated blocks, which is not the
same as physical space recovered on Btrfs. Cargo's multiple hard links were all
within its removed directory. All 1,066 regular-file inodes under root
`node_modules/` had surviving hard links outside it (168,525,824 allocated bytes),
so their cached data was not reclaimed or deleted. Btrfs shared/compressed extents
and metadata also affect accounting. The free-space delta is the observed net
filesystem change during the deletion window; concurrent filesystem activity can
affect it. No claim of reclaiming the entire directory allocation is made.

### Post-deletion verification and retained debt

Verification runs with only Python workflow tools on `PATH`, without Node, npm,
npx, Bun, Cargo, rustc/rustup or native compilers. Every runner step asserts that
both deleted directories remain absent. Tests use private data locations; the
pinned FTS extension and existing corpora are read-only prerequisites.

| Check | Follow-up result |
| --- | --- |
| Fresh `mise run bootstrap` and `mise run verify`, CPython 3.13.16 | Passed: Ruff, strict mypy, 265 tests passed and seven opt-in TS cases skipped; 189.87 s |
| Frozen Phase 3/4 and Python ContextBench gates | Passed inside canonical verification |
| Separate frozen TypeScript gate | All 17 passed, including the seven opt-in corpus-gate cases; 941.61 s under two-core/3 GB limits |
| Fresh wheel/sdist build and dependency checks | Passed; third-party runtime dependencies installed as wheels; all six environments passed `uv pip check` |
| Installed CLI/index/query/context/MCP/lock smoke on Python 3.12.15, 3.13.16 and 3.14.8 | All six wheel/sdist smokes passed; MCP advertises exactly `oxide_search` |
| Preserved CodeGraph tooling | CLI exploration still works after deletion, using its own bundled Node |
| User changes and historical fixture preservation | Tracked user diff byte-identical after deletion; no fixture/source/dependency changes |

The active Python documentation/scripts review found no command using the removed
outputs. `docs/agents/workflow.md` had an obsolete future-Phase-3 sentence about
retiring evidence and an imprecise Cargo-manifest statement; these now describe
continued historical retention and the absent **root** Cargo manifest. No code,
dependency, baseline, derivation version or public MCP contract changed.

Remaining Rust/Node-related files retain the purposes in the inventory above:
11 Rust files and the migration parity Cargo manifest are historical fixtures,
regressions or reproduction probes; the historical npm fixture and Bun runners
are evidence; `fixtures/ts_repo/package.json` is parser input; Archify, CodeGraph,
external MCPs and agent hosts are optional tools; JavaScript GitHub actions and
Claude's Bun action are CI orchestration. Native Python libraries are retained.
These are not outstanding obsolete production infrastructure. Hosted Windows/macOS
reruns and a new FTS download remain outside this local follow-up's verification.

Retained source/manifests, explicitly:

- `tests/benchmark_gate.rs`, `tests/embedding_staleness.rs`,
  `tests/full_incremental_parity.rs`, `tests/interrupted_index_recovery.rs`,
  `tests/language_conformance.rs`, `tests/precomputed_relations_conformance.rs`,
  `tests/provider_migration_recovery.rs`: historical semantic regression evidence.
- `fixtures/conformance/rust/src/backend.rs`, `broken.rs`, `store.rs`: preserved
  conformance source inputs, not a current compiled Rust implementation.
- `docs/migration-audit/parity/Cargo.toml` and `main.rs`: historical migration
  reproduction probe, requiring the pinned old kernel checkout.
- `eval-agent/tasks/tsx_feat_button/package.json`, `bun.lock`, `node_modules/**`
  and the TS/TSX task `verify.sh` scripts: historical evaluation task reproduction.
- `fixtures/ts_repo/package.json` and `tsconfig.json`, plus JS/TS fixture source:
  active parser/resolution input, never installed or executed by OXIDE.
- `.agents/skills/archify/package.json`, `package-lock.json`, `.mjs` tooling and
  `.claude/skills/archify`: optional third-party diagram tooling and its symlink.
- `.claude/skills/jev-model-router/`: third-party TypeScript agent plugin;
  the user's related `.agents/` deletions remain untouched.
- `.codex/config.toml`, `.claude/settings.json`, `.mcp.json`, `opencode.jsonc`
  and `.github/workflows/*.yml`: preserved external-agent or CI configuration.

Deletion/path/process/hard-link receipts are under
`/tmp/oxide-generated-cleanup/`; post-deletion verification logs and artifacts are
under its `verification/` subdirectory; `summary.json` records successful command
receipts and built-artifact SHA-256 digests. Both removed directories remained
absent through all checks. The auditable local runners are
`/tmp/oxide-cleanup-inspect.py`, `/tmp/oxide-cleanup-delete.py` and
`/tmp/oxide-cleanup-verify.py`. Nothing was pushed, published or released.
