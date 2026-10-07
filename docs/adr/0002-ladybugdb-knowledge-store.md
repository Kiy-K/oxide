# ADR-0002: LadybugDB as the derived knowledge store

## Status

**Proposed** — 2026-10-07. Phase 2A feasibility result. Listed as a candidate
in [ADR-0001](0001-oxide-v2-rewrite.md) (also Proposed). Evidence:
[`oxide_kernel/spikes/ladybug/README.md`](../../oxide_kernel/spikes/ladybug/README.md)
(versions, verified vs documented vs unknown) and its tests. Identity rules come
from [ADR-0010](0010-domain-identity-and-source-capture.md).

## Context

SPEC names LadybugDB as the initial `KnowledgeStore` adapter on the strength of
documentation, and requires a pinned feasibility record (BOOTSTRAP Phase 2)
before an adapter is built. The questions were: does the Rust API build and
run offline and reproducibly; can it give one-owner resource control, atomic
publication and snapshot-isolated reads; how do FTS/vector behave; and what
physical schema carries the domain model without leaking native types.

## Verdict

**Feasible, with conditions. Not a blocker.** Every graph-store requirement
needed for Phase 2B was verified at the pin on Linux x86_64: persistence and
reopen, typed adjacency with stable bounded order, mutation, transactions,
crash atomicity, snapshot reads, cancellation, single-writer ownership.
Several documented guarantees are weaker than the docs say, or the defaults are
unsafe for OXIDE. Each such gap is closed below by an OXIDE-side rule rather
than assumed away. FTS and vector work only through pinned extension files,
and remain optional accelerators (SPEC § Storage abstraction).

## Decision

### 1. Pin and upgrade policy

- `lbug = "=0.21.2"`. Native `liblbug` v0.21.2 static `compat` archive, verified by sha256. Extensions at extension version 0.21.0, verified by sha256. Digests live in `oxide_kernel/spikes/ladybug/fetch-liblbug.sh` until Phase 2B moves them to the runtime build.
- On-disk storage version 47 goes into every `DerivationId` (ADR-0010). A store written by another storage version is **rebuilt, never migrated** (SPEC § Compatibility policy). Released versions moved the format 40 → 41 → 42 → 43 → 47 between 0.16 and 0.20.
- Upgrading is a deliberate pin bump that re-runs the spike suite. LadybugDB releases weekly or faster.

### 2. Build: pinned artifact, never the default downloader

With no environment set, `lbug`'s build script runs a script fetched from
`LadybugDB/ladybug@main` and links the **latest** release, not the crate's.
That is network at build time, unpinned and mismatchable, and it executes a
remote script. OXIDE never uses it. Builds set `LBUG_LIBRARY_DIR`/`LBUG_INCLUDE_DIR` to a
sha256-verified release archive fetched by an explicit bootstrap step (verified
offline). `LBUG_BUILD_FROM_SOURCE=1` with the crate's vendored source is the
hermetic fallback. It needs CMake and C++20 and was not measured. The linked
binary is about 24 MB stripped and needs libstdc++, OpenSSL 3 and zlib at
runtime on Linux.

### 3. Extensions: pinned files, never `INSTALL`

FTS and vector are not in the core library. `INSTALL` downloads unsigned native
shared objects from `extension.ladybugdb.com` at runtime. OXIDE never calls
`INSTALL`/`UPDATE`. It loads digest-verified files with `LOAD EXTENSION '<path>'`,
and the runtime binary links with `-rdynamic`, its own `build.rs`, because lbug's
flag does not reach dependents. Loading native code into the runtime is a trust
boundary, so a file whose digest mismatches is a capability error. Without the
files, FTS/vector report unavailable and lexical/structural operation
continues.

### 4. Ownership: OXIDE enforces it, the database does not fully

Verified: a second process is refused a read-write open, but **a read-only open
by another process succeeds while the writer holds the store**. Within one
process, **a second read-write `Database` on the same directory is accepted**.
Therefore:

- The runtime takes an exclusive OS lock on a `store.lock` file in the store directory (`std::fs::File::try_lock`) before opening any database. Failure is a typed ownership-conflict error to the client (SPEC § Runtime).
- Inside the runtime, exactly one `Database` per database directory, held by one owner. Connections come from it. No other process, inspector or explorer opens a store the runtime holds.
- Every `Database` sets `buffer_pool_size` and `max_num_threads` explicitly. The default buffer pool is sized from host RAM.

### 5. Writes, reads, cancellation

- One write transaction per `Database`; a second **fails immediately** instead of waiting. The runtime serializes writes per database with a single writer queue. `enable_multi_writes` exists in the Rust API, is undocumented and is not used.
- Any failed statement aborts the whole manual transaction. The adapter treats every error inside a write transaction as a rollback of that batch.
- Read views run inside `BEGIN TRANSACTION READ ONLY`, verified as snapshots. A published generation is never written again (§ 6), so reads cannot see partial writes anyway.
- Deadlines map to `Connection::set_query_timeout` and cancellation to `Connection::interrupt`. Both are cooperative: a 200 ms deadline returned after about 385 ms in a debug build. Both return an error and leave the connection usable. The adapter reports `StoreError::Cancelled`.
- `Connection<'db>` borrows its `Database`. The runtime's store owner holds the `Database` and lends connections to blocking workers, so the synchronous `KnowledgeStore` port (Phase 1) stands. How connections are pooled is a Phase 2B detail.

### 6. Publication: one database per generation, an atomic pointer

The database offers atomic transactions, not atomic multi-transaction
generations or a "current version" switch. OXIDE builds publication on two
primitives that were verified or are standard: transactional, crash-safe
commits inside one database, and atomic `rename` within one filesystem.

```text
<store>/store.lock                 exclusive runtime ownership (§ 4)
<store>/CURRENT                    one line: the published generation's directory name
<store>/generations/<g>/           sealed generation: db/ + MANIFEST
<store>/generations/<g>.staging/   generation being built; never readable
```

`<g>` is the hex SHA-256 of the canonical `SnapshotKey` encoding.

1. **begin**: create `<g>.staging/`, open a `Database` there and create the schema. A key already staged or sealed is `Conflict`.
2. **write**: apply batches in write transactions, or `COPY FROM` files in Phase 2B.
3. **validate**: kernel `knowledge::validate` over the generation (Phase 1, shared with the fake store), plus adapter checks: entity, relation and endpoint counts read back equal what was written. `MATCH … CREATE` silently skips missing endpoints.
4. **seal**: write `MANIFEST` (snapshot manifest, derivation manifest, storage version), `CHECKPOINT`, close the database, fsync, then rename `<g>.staging` to `<g>` and fsync the parent.
5. **publish**: write `CURRENT.tmp`, fsync, rename it over `CURRENT`, fsync the directory. This is the only step readers observe.
6. **read**: a request resolves `CURRENT` once to a `SnapshotKey`, the pin, and reads that sealed generation through the owner's `Database` for it. Pinned views of older generations keep working: verified, a reader on g1 is unaffected while g3 is built and published.
7. **discard / recover**: discard deletes `<g>.staging`. On startup, after taking the lock, every `*.staging` is deleted. `CURRENT` naming a missing or unreadable generation fails closed with a typed error and a rebuild requirement (SPEC § Failure contract). It never falls back to "some" generation.
8. **rebuild from source**: always a new generation from a new capture. Published generations are immutable. GC removes sealed generations that are not current and not pinned. The retention policy is Phase 2B.

Guarantees: an incomplete generation is never `CURRENT` (verified: a builder
killed mid-transaction leaves `CURRENT` and g1 intact). Generations and
derivations are isolated by directory, so they share no tables, keys or
indexes. Every FTS/vector index covers exactly one generation, so SPEC's
"filter snapshot scope before limits" holds by construction. Limits: atomicity
relies on POSIX same-filesystem rename plus fsync. Windows, macOS and network
filesystems are unverified. Each open generation has its own buffer pool, and
each generation is a full copy on disk; both are acceptable for full rebuilds
and must be measured in Phase 2B.

Rejected: one database with generation-scoped keys, because native FTS/vector
top-K is global, which forces post-limit snapshot filtering; old generations
need large deletes; and all generations share one writer and one crash domain.
Per-generation tables in one database is a viable fallback (DDL is
transactional, verified), but it couples every generation to one catalog and
one write lock for no current gain.

### 7. Physical schema (private to the adapter)

```text
NODE TABLE Entity(key STRING PRIMARY KEY, category STRING, kind STRING, name STRING,
                  signature STRING, file STRING, start_byte INT64, end_byte INT64,
                  digest STRING, language STRING, coverage STRING, test BOOLEAN, ...)
NODE TABLE Unresolved(key STRING PRIMARY KEY, name STRING)
REL TABLE <KIND>(FROM Entity TO Entity, FROM Entity TO Unresolved,
                 basis STRING, resolution STRING, site INT64,
                 ev_start INT64, ev_end INT64, ev_digest STRING)
  for KIND in CONTAINS (+ physical BOOLEAN), DEFINES, REFERENCES, CALLS,
              IMPORTS, IMPLEMENTS, TESTED_BY
```

- **Entities**: one `Entity` table for repository, module, file and symbol, with `category` telling them apart. Every relation is then `Entity → Entity`, avoiding a FROM/TO pair per category combination and union queries per adjacency call. `key` is the injective ADR-0010 encoding of `EntityId`, so it is unique by primary key (verified) and holds no snapshot, since the database is the generation. File coverage and language are columns on file rows.
- **Tests**: a facet (`test`, later a role column) on the entity, never a duplicate node (SPEC § Entities). `TESTED_BY` carries the association and its `basis`; a heuristic link is `basis = 'heuristic'`.
- **Resolution**: `resolution` is `resolved`, `ambiguous` or `unresolved`. An ambiguous relation is N edges sharing one `site`, which the adapter reassembles into one `Target::Ambiguous` (verified). An unresolved relation targets its own `Unresolved` node, which is not an `Entity`. It is therefore never a TreeIndex node, never matched by `(:Entity)` patterns, and no resolved endpoint is invented. Queries for resolved neighbors filter `resolution = 'resolved'`.
- **TreeIndex**: the hierarchy is `CONTAINS` with `physical = true`. Publication validation requires exactly the parent each ID implies (Phase 1 `validate`). Logical containment and all other kinds stay typed cross-edges. Regions are computed by kernel code from typed adjacency, the same code for fake and real stores (Phase 1), so there are no region tables now.
- **Order and bounds**: every adjacency query orders by domain keys (kind, endpoint keys, site) before `LIMIT n+1` (truncation flag). `InternalID` and other native values are never selected into results handed to the kernel; rows are mapped to domain types inside the adapter.
- **Uniqueness and integrity**: entity primary keys enforce uniqueness. Relations have no database uniqueness, so the adapter writes the kernel-sorted, deduplicated set (Phase 1) and verifies endpoint counts (§ 6.3).
- **Accelerators (Phase 3, when enabled)**: FTS over entity name/signature/path text and a vector property on `Entity`, per generation. FTS splits `snake_case` on `_` (verified); camelCase and ordering under `TOP` are unverified. Phase 3 decides whether native FTS meets the lexical gate or a secondary lexical index behind the port is needed (SPEC allows one).

## Consequences

- Phase 2B can build the adapter on verified behavior. Adding `lbug` to the runtime makes `mise run verify` and CI depend on the pinned archive: a bootstrap fetch with sha256 and a cache, or a source build.
- The runtime, not the database, owns ownership, write serialization and publication atomicity. Those rules get contract tests in Phase 2B.
- Kernel APIs are unchanged; no LadybugDB type is visible outside the adapter.

## Reconsider LadybugDB if

- a pinned build cannot be produced for a required target platform;
- bulk load (`COPY FROM`) of a mid-size repository is too slow or too memory-hungry at Phase 2B measurement;
- per-generation databases cost too much memory or disk, and per-generation tables do not fix it;
- extensions cannot be loaded from pinned files on a required platform and Phase 3 needs them;
- a storage-format or API break lands in a patch release.

## Unknown at the pin

Write-transaction behavior when interrupted; `enable_multi_writes`;
recovery from disk-full or corrupted files and from a crash during `CHECKPOINT`;
macOS and Windows builds, extension loading and rename atomicity; `COPY FROM`
throughput; ANN recall and determinism; FTS ordering with `TOP` and camelCase
tokenization; the full third-party license inventory of the static archive.
