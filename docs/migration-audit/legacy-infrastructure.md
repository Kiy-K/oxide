# Python rewrite infrastructure: decision summary

Historical audit, 2026-10-21, beginning at `c70786db`; generated-artifact follow-up began at `905102de`. This is infrastructure evidence, not a retrieval experiment or current dependency/version inventory. Current installation requirements are in [platforms](../platforms.md), commands in [workflow](../agents/workflow.md), architecture in [SPEC](../spec/SPEC.md) and [ADR-0011](../adr/0011-python-engine-vectorless.md).

## Decisions

- Keep the Python engine/distribution independent of Node, Bun and Cargo. Native libraries arrive as wheels; CI verifies wheel-only third-party installs and installed-artifact smoke with a restricted PATH. MCP must advertise only `oxide_search`.
- Retain historical semantic fixtures, regression tests, evaluation harnesses and migration parity probes. Their Rust/Bun/Python-3.11 requirements apply to reproducing old results, not current OXIDE installation. `fixtures/ts_repo` is active parser input, not an npm application to install.
- Keep optional agent tooling (CodeGraph, Context7, archify, external MCPs) and GitHub Actions distinct from product dependencies. Measured Node process ancestry belonged to agent hosts/tools; this was a bounded process snapshot, not continuous tracing or proof about arbitrary plugins.
- Remove stale Rust/TypeScript agent instructions and strengthen packaging checks. Preserve user edits, research worktrees and tracked historical npm fixtures.
- Follow-up removed only ignored root `target/` and `node_modules/`, after separate authorization, descriptor/path checks and bounded process inspection. No source, dependency, baseline, public export or derivation version changed.

## Measurements and verification limits

Reported Linux x86_64 checks: canonical Ruff/strict mypy/pytest passed (265 passed, seven opt-in TS cases skipped); separate frozen TS gate 17 passed (~942 s in follow-up), under two-core/3 GB limits. Wheel/sdist installed smokes passed on Python 3.12.15, 3.13.16 and 3.14.8, including single-tool MCP, indexing/edit/reopen and locking. Original unrelated tracked diff remained byte-identical. Hosted Windows/macOS execution and a fresh FTS download were not verified locally.

Removed entries represented 2,236,022,784 bytes (`target/`) plus 168,534,016 bytes (`node_modules/`) of allocated blocks. Observed free-space increase was **1,025,773,568 bytes**, not the summed allocation: npm files had surviving hard links, and Btrfs sharing/compression and concurrent activity affect accounting. No claim of reclaiming all directory allocation is justified.

## Provenance and historical access

The [full original audit](https://github.com/Kiy-K/oxide/blob/eb43ac1070d8724d31a0be49bc7357c7132fb90f/docs/migration-audit/legacy-infrastructure.md) records inventories, exact test receipts, process ancestry and limitations. Raw receipts were temporary/external (`/tmp/oxide-infra-audit/`, `/tmp/oxide-generated-cleanup/` and associated runners/logs); they were not committed, so this summary is not a self-contained rerun bundle. Retained [migration benchmarks and parity probes](README.md) have separate provenance.

Legacy v1 invariants (SQLite, persisted symbol IDs, embedding-space compatibility, bounded hydration, atomic publication) are [pinned historical reference](https://github.com/Kiy-K/oxide/blob/eb43ac1070d8724d31a0be49bc7357c7132fb90f/docs/agents/invariants.md), **not v2 requirements**. Original v1 code is pinned at `ac985b28fcff7663aedadf2db5b40aa2dc325580`; the replaced Rust/Bun v2 tree is recoverable at `f79808a`. Reproduce historical claims in those trees; port semantic lessons only where current SPEC requires them.
