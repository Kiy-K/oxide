# Phase 2B checkpoint — 2026-10-07

Status: **incomplete; stopped at the user's request for a checkpoint commit**.
Resume Phase 2B only. Do not start Phase 3. Branch: `rewrite/v2`.

ADR-0002 and ADR-0010 were accepted by explicit user approval; their decision
substance was preserved. Linux x86_64 is the only production target. macOS,
Windows and network-filesystem behavior remain unverified.

## Implemented and checked

- Production LadybugDB adapter behind the kernel-owned KnowledgeStore port,
  with private native types and domain codecs, typed graph edge tables,
  unresolved nodes, ambiguity preservation and the test facet.
- Exclusive OXIDE store lock, retained by pinned views; bounded single writer
  queue; explicit transaction rollback and immutable concurrent read views.
- Per-generation staging, shared kernel validation and graph readback,
  checkpoint/close/fsync/seal, atomic CURRENT publication and reopen.
- Content-addressed captured-source blobs named by validated SHA-256 digest;
  source retention and historical hydration verify exact bytes and range.
- Explicit native preparation at the validated v0.21.2 archive/crate pin,
  storage format 47, archive and extracted-library/header SHA-256 checks,
  controlled Cargo paths, CI preparation/cache. No native runtime downloads.
- Capture uses the ignore crate for repository .gitignore rules, including
  nested overrides; ignored files remain outside the manifest. Capture
  excludes .git and .oxide, reports unreadable/special/symlink entries and
  verifies two byte/metadata passes with bounded retries. Linux openat2
  refuses symlinks in every path component and avoids FIFO open blocking.
  This requires Linux kernel 5.6+ and mounted /proc; no portability fallback.
- FileManifest.byte_length bounds source evidence. Canonical versioned
  derivation-component hashing is in runtime/derivation.rs.
- Linux XDG application-data/configuration seam; random 128-bit RepoId is
  persisted in REPO.json. Checkout path is discovery metadata, not identity.
  Registry metadata is staged/sealed; configured storage inside source is
  rejected without writes.

Capture encoding is now `oxide-capture-v2-gitignore`: ordered repository
ignore-rule paths/digests participate even when the rule file itself is
ignored. Invalid/undecodable/unreadable ignore configuration fails closed.
No global Git configuration, Git process, repository scripts or hooks run.

## Verification at checkpoint

The unchanged eight shared KnowledgeStore cases run against MemoryStore and
LadybugStore. All pass. The adapter has 21 passing tests total, including
reopen, transaction failure, serialized writers, concurrent immutable views,
process ownership, source fidelity/corruption, derivation/storage mismatch,
staging recovery and process exit before seal.

Measured checkpoint result: `mise run verify` **PASS** (Rust formatting,
clippy, all Rust tests, frozen Bun install, Biome, TypeScript checking and
20 Bun tests). Biome reports three existing template-string warnings in
unchanged `src/boundary.test.ts`; the command exits successfully. Python
ingestion and capture → ingest → publish → reopen are **not implemented or
validated** yet.

## Resume first

1. Address the static-review recovery finding: a first build killed after
   sealing but before its initial CURRENT publication leaves a valid sealed
   directory with no CURRENT; startup rejects it as corruption and cannot
   reopen for a fresh build. Add a regression for this interruption window
   and handle the unpublished seal without selecting an arbitrary current
   generation. Preserve fail-closed behavior for malformed CURRENT and
   missing/unreadable targets of an existing pointer. The existing crash
   test covers pre-seal interruption with an already published generation.
2. Implement **one Python language slice**. No parser/ingestion product files
   exist yet. Runtime has pinned tree-sitter 0.27.0 and tree-sitter-python
   0.25.0 dependencies. Read the required architecture documents completely
   before edits; inspect pinned APIs/grammar (Context7 ID
   `/websites/rs_tree-sitter` was resolved and Rust parser docs fetched).
3. Keep parsing substrate in runtime and pure domain normalization/resolution
   in the dependency-free kernel. Write failing conformance and end-to-end
   tests first. Define Python ModuleId and synthetic-name conventions,
   duplicate/nested declaration ordinals, coverage and conservative
   unresolved/ambiguous references/imports; no compiler-resolution claim.
4. Implement runtime `derive(repo, capture)` and `derivation_components()`;
   integrate RepositorySession full rebuild through capture → derive →
   begin → retain_source/retain_derivation → write → publish. Expose skipped
   capture diagnostics. Test process/store close and reopen, changed/deleted
   fresh rebuilds, historical bytes, malformed/unsupported source, and
   shadowed/dynamic reference handling.
5. Review the complete implementation, run `mise run verify`, and document
   measured generation memory/disk footprint. No incremental indexing.

Use `docs/superpowers/plans/2026-10-07-phase-2b.md` as the implementation plan.
The storage implementation report is summarized here; temporary reports at
`/tmp/oxide-phase2b-{store-report,store-review,ingestion-report}.md` and the
ignored plan ledger may be available locally, but are not needed to resume.

## Current limitations

All sealed generations and captured bytes are retained; no GC yet. Startup
opens all retained generation handles, each configured with a 32 MiB buffer
pool and two threads. Resource footprint is not measured at repository scale.
Derivation component conventions for Python remain unfinished. The service
v1 transport is unchanged; no integration is built on its provisional
process-per-request behavior. FTS/vector, routing, models and packing remain
outside this checkpoint.
