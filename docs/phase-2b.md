# Phase 2B — LadybugDB adapter, capture and the Python slice

Status: **complete** (2026-10-08) on `rewrite/v2`. Phase 3 has not started.
Decisions: [ADR-0002](adr/0002-ladybugdb-knowledge-store.md) and
[ADR-0010](adr/0010-domain-identity-and-source-capture.md), Accepted by
explicit approval with their substance unchanged. This file records the
implementation details those ADRs left to Phase 2B.

Production target: **Linux x86_64 only**. macOS, Windows and network
filesystems are unverified, not inferred.

## What exists

| Piece | Where |
| --- | --- |
| LadybugDB `KnowledgeStore` adapter, ownership lock, writer queue, generations | `runtime/src/storage/` |
| Source capture (`.gitignore` via the `ignore` crate, no Git) | `runtime/src/capture.rs` |
| Python syntax facts (tree-sitter) | `runtime/src/python.rs` |
| Language ownership, coverage, entity/relation assembly | `kernel/src/ingest.rs` |
| Python identity, scoping and conservative resolution | `kernel/src/python.rs` |
| Derivation components and full-rebuild derivation | `runtime/src/derivation.rs` |
| XDG data location, random `RepoId` registry, rebuild pipeline | `runtime/src/repository.rs` |
| Native preparation (pinned, SHA-256 checked) | `mise run native:prepare`, `oxide_kernel/native/prepare.sh`, `runtime/build.rs` |

Pipeline (`RepositorySession::rebuild`): capture → derive → `begin` (staging
directory) → retain derivation manifest and content-addressed source bytes →
write → validate (kernel `validate` plus graph readback) → checkpoint, close,
fsync, seal/rename → atomic `CURRENT`. A capture equal to the current
generation publishes nothing. One equal to an earlier generation still sealed
in this process is re-pointed: identical snapshot and derivation means
identical knowledge. Any failure discards the staged generation and leaves
the previous one current. Skipped entries and case-fold collisions are
returned in `BuildReport`; they are capture diagnostics, not file evidence.

## Decisions made inside the ADRs

- **Retention.** Only `CURRENT` is loaded at startup. No view can be pinned
  before startup, so every other generation directory is removed: incomplete
  staging, a seal that never reached `CURRENT`, and superseded generations.
  Within one process, every published generation stays open for pinned views
  (no in-process GC yet). This resolves the checkpoint's first-seal finding:
  a first build killed after sealing but before `CURRENT` now reopens empty
  and rebuilds. It is never promoted to current. A malformed `CURRENT`, or one
  naming a missing or unverifiable generation, still fails closed before
  anything is removed.
- **Storage format.** `STORAGE_FORMAT = 47` is checked against
  `lbug::get_storage_version()` at store creation. A current generation in
  an older format is discarded at startup and rebuilt, never migrated. A
  newer format belongs to a newer OXIDE: startup refuses it with
  `Unsupported` and deletes nothing.
- **Derivation components** (`derivation::components`): `id-namespace`
  (`oxide-id-v1`), `python` (`oxide-python-v1`), `python-parser`
  (tree-sitter 0.27.0 / tree-sitter-python 0.25.0), `tree-projection` (1),
  `storage-schema` (`oxide-ladybug-schema-v1`), `storage-format` (47), `lbug`
  (0.21.2). Each generation stores them verbatim in `DERIVATION`.
- **Capture encoding** is `oxide-capture-v2-gitignore`: ADR-0010's
  `SnapshotId` inputs plus each repository `.gitignore` path and digest, even
  when the rule file itself is ignored. Rules are parsed by the `ignore`
  crate; no Git process, global Git configuration or hooks are used. `.git`
  and `.oxide` are always excluded. Undecodable or unreadable ignore rules
  fail the capture closed.
- **Source hydration** reads the generation's retained blob named by the
  SHA-256 of its bytes, re-verifies length and digest, and never reads the
  worktree.

## Python slice

Chosen because `fixtures/py_repo` is the smallest retained fixture with
packages, relative imports, classes, methods and tests.

- `ModuleId` = `python:` + the dotted path from the repository root
  (`a/b.py`, `a/b/__init__.py` → `python:a.b`). Files with a non-identifier
  segment have no module. `sys.path` and source roots are not modeled. Module
  ⊃ file is logical containment; a module `DEFINES` its file's top-level
  declarations.
- Symbols: `class`/`def` only (`class`, `function`, `method`), nested by
  declaration. Ordinals count same-named siblings in byte order. Decorated
  ranges include decorators. No synthetic names: lambdas and comprehensions
  are expressions. Python cannot put two declarations on one line.
- Coverage: syntax errors → `Partial` with byte diagnostics, keeping what
  parsed. Non-UTF-8 `.py` (PEP 263 is not decoded) and every non-Python file
  → `Unsupported`, still file evidence.
- Fact classes: syntax (`Syntactic`: containment, module membership, the
  presence of an import, call or base); resolved (`Resolved`: one declaration
  or repository module by Python scoping); ambiguous (several candidates,
  e.g. redefined functions); unresolved (`Unresolved { name }` for builtins,
  external modules, attribute calls, re-exports and anything shadowed or
  rebound: parameters, assignments, loop targets, star imports, `global` /
  `nonlocal`). Scoping skips enclosing class bodies. There is no type
  inference, and `globals()`/`setattr`/`exec` mutation is not modeled.
  `IMPLEMENTS` is never emitted: Python inheritance is a `REFERENCES` from
  class to base.
- Tests: pytest default discovery sets the test facet. A test calling a
  resolved non-test symbol yields `TESTED_BY` with `Heuristic` basis.

## Verification (measured 2026-10-08)

- Fake/LadybugDB parity: the eight shared `KnowledgeStore` cases pass on
  both. `ingestion.rs` also compares full TreeIndex-walked knowledge of the
  ingested fixture in `MemoryStore` and LadybugDB: equal.
- capture → ingest → publish → close → reopen → same knowledge: passes
  (`fixture_capture_ingest_publish_reopen`). Changed/deleted files, pinned
  history, revert → re-point, and restart retention:
  `changed_and_deleted_files_rebuild_fresh_and_history_stays_exact`.
- Adapter: 24 tests, including ownership (in-process, pinned view,
  cross-process), serialized writes, transaction rollback, concurrent
  immutable views, crash before seal, first/later seal interrupted before
  publication, bad/missing `CURRENT`, older/newer storage format, derivation-manifest
  identity and source corruption.
- `mise run verify`: pass.

Footprint, release build, Linux x86_64, rebuild into an empty store (RSS
includes the process; "—" was not measured):

| Input | Files | Source | Entities / relations | Rebuild | Max RSS | Store on disk |
| --- | --- | --- | --- | --- | --- | --- |
| `fixtures/py_repo` | 11 | 44 KiB | — | 0.63 s | 124 MiB | 5.2 MiB |
| CPython 3.14 `email/` | 117 | 1.8 MiB | — | 8.7 s | 139 MiB | 15.5 MiB |
| CPython 3.14 `asyncio/` (with `.pyc`) | 140 | 2.7 MiB | 1,283 / 5,224 | 12.3 s | 155 MiB | 18.6 MiB |

Capture plus derivation of `asyncio/` takes 0.11 s. The rest is the
adapter's per-row write and publish statements (about 2 ms per row), which
is a **negative result**. Bulk load (`COPY FROM`) or reused prepared
statements is the measured upgrade path, and ADR-0002 lists bulk-load speed
as a reconsideration criterion. Each generation is a full copy (database
plus retained source), with a ~5 MiB fixed database cost.

## Open (not Phase 2B)

In-process GC, write throughput, FTS and the adapter encoding check were
closed in Phase 3 ([phase-3.md](phase-3.md)).

- In-process GC of unpinned superseded generations and source-blob
  deduplication across generations.
- Write/publish throughput (above) before mid-size repositories.
- Incremental indexing with full/incremental parity; FTS/vector accelerators
  (Phase 3); service operations for rebuild/status (the v1 contract is
  unchanged); recovery UX for a fail-closed corrupt store (today: delete the
  repository's directory under the data location).
