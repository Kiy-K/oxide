# Storage and generations review rules (v2)

Scope: `src/oxide/storage/__init__.py`, `src/oxide/storage/ladybug.py`, `src/oxide/storage/fts.py`, `src/oxide/storage/store.py`,
`src/oxide/capture.py`, `src/oxide/api.py` (ownership).

---

### STORE-001 — One owner per store, across processes
**Severity:** BLOCKER · **Scope:** `open_repository`, `Repository.close`.

**Invariant:** a `Repository` takes the store's exclusive OS lock on open; a
second owner in any process gets `OwnershipConflict`, never a wait or a
second writer. The database's own protection is weaker, so OXIDE takes the
lock itself. A `Repository` is not thread-safe; concurrent multi-client
access is out of scope until a product requirement justifies it.

**What constitutes a violation:** removing or weakening the lock; allowing a
second writer; making `Repository` thread-safe by assumption without
evidence.

**Evidence required:** the lock acquisition/release code path.

---

### STORE-002 — One pinned generation per request
**Severity:** BLOCKER · **Scope:** `oxide.storage`, `oxide.api`.

**Invariant:** each request pins one published generation and one effective
derivation; a request never mixes source, entities, relationships or lexical
data from different generations. Readers see a complete published generation
or an explicit error; unfinished writes are never visible. Publication is
atomic.

**What constitutes a violation:** a read that combines data from two
generations; a partial publication visible to readers; a new write path that
does not publish atomically.

**Evidence required:** the transaction/publication boundary in the changed
code.

---

### STORE-003 — Damage classification and fail-closed reads
**Severity:** BLOCKER · **Scope:** `oxide.storage`, error handling.

**Invariant:** derived state is rebuilt by `index()` because repository
identity and source are still established; this covers an unreadable,
truncated, empty, missing or foreign `graph.lbug`, a malformed or foreign
`MANIFEST.json`, and a missing or digest-mismatched retained source file.
Reads raise `store_corrupt` until then, and `index()` never reports such a
generation `unchanged`. `incompatible_index` is raised when the current
generation was built by another derivation or store schema; `index()`
rebuilds. `not_indexed` is raised before the first `index()`.

**What constitutes a violation:** waving a torn or foreign store through as
healthy; reporting `unchanged` for a corrupt store; treating missing source
bytes as empty.

**Evidence required:** the validation code and its tests.

---

### STORE-004 — Capture identity and no mixed bytes
**Severity:** BLOCKER · **Scope:** `src/oxide/capture.py`.

**Invariant:** capture lists, stats and reads only; never runs Git, hooks or
repository code. Symlinks are never followed, special files never opened,
and every skipped in-scope entry is reported and part of the snapshot
identity. A changed worktree during capture retries boundedly then raises
`capture_conflict`; a `.gitignore` that cannot be read fails closed. More
than 1 GiB in scope raises `capture_limit`. The data directory is excluded
by device and inode wherever it sits.

**What constitutes a violation:** following a symlink; opening a special
file; publishing a snapshot from mixed capture passes; a skipped entry not
recorded in identity; reading the current worktree without digest
verification for historical bytes.

**Evidence required:** the capture code path and its tests.

---

### STORE-005 — Native availability is a hard boundary
**Severity:** BLOCKER · **Scope:** LadybugDB loading.

**Invariant:** if LadybugDB's native library cannot load, OXIDE reports
`native_unavailable` and touches nothing. On Windows this includes missing
OpenSSL 3 DLLs. Nothing falls back to another store.

**What constitutes a violation:** catching a native load failure and
continuing with a degraded/empty store; introducing a second backend without
an ADR and evaluation.

**Evidence required:** the load path and its tests.
---

### STORE-006 — Storage backend is closed
**Severity:** MAJOR · **Scope:** any proposal to change the backend.

**Invariant:** the backend question is closed: LadybugDB (graph + pinned
FTS). SurrealDB and Turso were evaluated and rejected. Enhancements are
limited to features already available through the pinned LadybugDB/FTS.
Proposing a different backend requires a fresh evaluation round, not a patch.

**What constitutes a violation:** adding a new backend or storage abstraction
"for flexibility" without an ADR and evaluation.

**Evidence required:** the ADR/evaluation doc referenced by the PR.
