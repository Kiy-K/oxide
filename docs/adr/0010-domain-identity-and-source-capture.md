# ADR-0010: Domain identity and source capture

## Status

**Accepted** — 2026-10-07, explicitly approved for Phase 2B. Phase 2A. Replaces the provisional identity policy in
`oxide_kernel/README.md` (Phase 1) once accepted; until then both are proposals.
Changes one Phase 1 rule: case-fold path collisions are reported, not rejected
(§ RepoPath). Does not depend on the storage choice ([ADR-0002](0002-ladybugdb-knowledge-store.md)).

## Context

SPEC § Identity and snapshots requires deterministic, collision-safe IDs that
are independent of database allocation, with explicit path/case rules, support
for overloads, nesting and duplicate names, and byte-exact source attribution.
It leaves the algorithm to a follow-up decision before ingestion. Phase 1
implemented structural IDs (`oxide_kernel/crates/kernel/src/id.rs`). Phase 2
ingestion needs those rules settled, plus how a snapshot is identified from
captured source.

Two pressures pull identity in opposite directions: entity IDs must be exact
and unambiguous inside one snapshot, while "the same function after a
refactor" is a fuzzy, heuristic question. Answering the second through the
first makes IDs unstable or wrong. So they are kept apart.

## Decision

### Two kinds of identity

| Kind | IDs | Construction | Scope |
| --- | --- | --- | --- |
| Structural | `FileId` (= `RepoPath`), `SymbolId`, `ModuleId`, `EntityId` | The identity inputs themselves, as a value; equal exactly when the inputs are equal | **Snapshot-local**: meaningful only with a `SnapshotKey` |
| Content-addressed | `Digest`, `SnapshotId`, `DerivationId` | `sha256:` + 64 lowercase hex over a versioned, length-prefixed canonical encoding | Global |
| Assigned | `RepoId` | Chosen once, persisted with the store | Global namespace |

No ID is a database row ID, `InternalID`, table offset, allocation counter,
timestamp, absolute path or legacy FNV hash.

### Snapshot-local entity identity and continuity

`FileId`, `ModuleId` and `SymbolId` identify an entity **within one snapshot
and derivation**. The same `FileId` text in two snapshots is a statement about
path equality, not that it is "the same file". OXIDE does not promise
continuity across edits:

- A rename or move is a delete plus an add. The moved file's symbols get new IDs.
- Inserting an earlier same-named sibling renumbers later ordinals.
- Caches, judgments and traces keyed by entity ID are also keyed by `SnapshotKey`.

If cross-snapshot continuity is needed later (incremental indexing,
judgment reuse, dataset deduplication), it is a **separate derived
`ContinuityKey`**. It would be produced by a versioned matcher from evidence
such as kind, ordinal-free declaration path, signature and body digest, with a
confidence and match reason. It is never an entity ID, a primary key, or a
join key for current knowledge. It is not designed or implemented now.

### RepoId

An opaque namespace label, assigned when a derived store is created and
persisted in that store's metadata. Every `SnapshotKey` carries it. It is not
derived from the absolute checkout path, a remote URL, or the database. One
derived store holds one repository. The runtime takes it from repository
configuration. With none configured, it generates a random 128-bit value once
and persists it (provisional: the default and the store location are Phase 2B
details). Moving or recloning a checkout without its store means a rebuild under
a new `RepoId`, which is acceptable for derived state. Grouping forks and
worktrees for dataset splits is a separate dataset concern, not `RepoId`.

### SnapshotId

`sha256:` over the canonical capture manifest (implemented by `snapshot_id` in
`oxide_kernel/crates/runtime/src/capture.rs`, `CAPTURE_VERSION =
"oxide-capture-v1"`), length-prefixed so the encoding is injective:

1. the capture encoding version;
2. the content-affecting scope: exclusion subtrees (sorted, deduplicated) and the per-file size limit;
3. every captured file: path and content digest;
4. every skipped in-scope entry: path and skip kind.

Not included: absolute root, timestamps, file modes, the git commit or branch,
OS error text, sizes of skipped files, and the total-size limit, which only
fails a capture.

Consequences: identical bytes plus identical scope give the same `SnapshotId`
on any machine or clone. A clean checkout and a dirty worktree differ exactly
when their captured bytes or scope differ, which satisfies SPEC's "commit plus
dirty/untracked content and scope must be distinguishable". The commit may be
recorded beside the manifest as provenance; it is not identity and is not
captured yet. Captures that differ only by line-ending conversion or
filesystem normalization are, correctly, different snapshots.

### DerivationId

`sha256:` over a canonical derivation manifest of named component versions.
The manifest is stored verbatim beside the generation for inspection:

- the ID namespace version (this ADR: `oxide-id-v1`);
- per-language extractor/grammar and resolver versions;
- the TreeIndex projection version;
- the storage schema and adapter versions plus the store's on-disk format version (LadybugDB storage version, ADR-0002);
- the embedding-space fingerprint (SPEC § Determinism), when vectors are part of the generation.

The exact component list is frozen in Phase 2B when those components exist.
The kernel treats `DerivationId` as opaque. A store never answers "any
derivation" (Phase 1 contract).

### Digest

`sha256:` + 64 lowercase hex of the **raw captured bytes**: no decoding, newline
or encoding normalization. The algorithm prefix is part of the value; digests
with different prefixes are unequal.

### RepoPath (FileId)

- UTF-8, `/`-separated, relative; no empty, `.` or `..` segments; no leading or trailing `/`; no NUL (unchanged from Phase 1).
- **Byte-exact, case-sensitive identity, never folded or normalized.** Paths are the names the filesystem reported at capture, joined with `/` (never an OS path string, so Windows separators cannot leak in). Folding would merge distinct files: case-variant files exist in real repositories (the Linux kernel ships some), git stores byte paths, and hydration must reopen exactly the captured name. Unicode normalization is not applied either: an NFD name captured on one platform and an NFC name on another are different paths and different snapshots.
- **Case-fold collisions are diagnostics.** Capture reports groups of captured paths equal under case folding (`SourceCapture::case_collisions`): they cannot coexist in a checkout on a case-insensitive filesystem. On such a filesystem they cannot occur at capture time. Rejecting them, as the Phase 1 proposal did, would make those repositories unindexable. NFC/NFD-equivalent pairs are not yet detected (needs normalization tables). Lowercasing approximates case folding.
- A non-UTF-8 name or invalid path cannot become a `RepoPath`: the entry, and for a directory its whole subtree, is skipped as `InvalidPath`. It is never mapped to a lossy name.
- Names that are valid here but cannot materialize on another OS (`\`, `:`, `CON`, trailing dot or space) are valid `RepoPath`s. That is a portability issue, not identity.
- Query hints (proposed for Phase 3): a user- or agent-supplied path resolves exactly first. Failing that, a unique case-fold match resolves with an explicit "case-insensitive match" reason, and several matches are ambiguous. Folding never happens in identity.

### ModuleId

An adapter-defined namespace string prefixed by language (for example
`rust:` or `ts:`), so namespaces of different languages cannot collide. The
per-language form is fixed with the Phase 2B language slice. A module is not a
file (SPEC § Entities).

### SymbolId

The file plus the declaration path `[(name, ordinal)]` from file scope inward
(Phase 1 structure, kept):

- **One SymbolId per declaration site**: one syntactic occurrence that introduces a name. A declaration and its definition elsewhere (C/C++ prototype and body, TypeScript overload signatures, a trait method and its impls, partial classes, Rust `impl` blocks) are separate symbols linked by relations, never merged identities.
- `name` is the adapter's canonical declared name as source text, unqualified (nesting is in the path). Anonymous declarations (closures, `impl` blocks, anonymous classes, default exports) get an adapter-defined synthetic name that cannot be a valid identifier in that language, for example `<impl Display for Foo>`. The per-language table is a Phase 2B deliverable.
- `ordinal` is the 0-based index among same-named siblings under the same parent, in source byte order of the declaration start. It separates overloads, redefinitions and conditional-compilation variants. Kind is not part of identity, so a type and a value with the same name in one scope are `#0` and `#1`.
- Same-line and nested declarations are told apart by byte order and path, never by line.
- Declarations with no source syntax in the file (macro-expanded, generated) are out of scope until a language slice defines them.

### Source ranges and digests

- `ByteRange` is half-open `[start, end)` into the captured bytes of the entity's own file, with `start <= end`. Phase 2B adds the file byte length to `FileManifest` so publication also checks `end <= length`.
- Ranges are attribution and evidence, not identity.
- Derived line view: line = 1 + number of `\n` bytes before the offset; column = 1 + bytes since the preceding `\n`. `\r` is an ordinary byte.
- `SourceRef` = file + range + that file's digest, so evidence names the exact bytes. A symbol body digest (future) is the digest of its range's bytes. It is distinct from identity and useful for change detection and continuity.

### Collision guarantees

- **Structural IDs cannot collide**: equality is equality of their inputs. Two declarations that would share an ID are impossible by construction because the ordinal disambiguates. A duplicate within one generation is an extraction bug, rejected at write (Phase 1 store contract).
- **Content-addressed IDs** rely on SHA-256 collision resistance (about 2^128 work). Encodings are length-prefixed and versioned, so distinct manifests cannot encode to the same bytes.
- **Physical keys** in a store adapter must be an **injective** encoding of the structural ID (for example a length-prefixed string), never a truncated hash. If an adapter ever hashes, it stores the full ID and treats a mismatch on read as `Corrupt`.

### Source capture

The runtime captures, the kernel consumes. Implemented minimally in
`runtime/src/capture.rs`, producing the kernel input `kernel/src/source.rs`
(`SourceCapture`, `CapturedFile`, `Skip`):

- **Root and paths**: the root is canonicalized once; entries are named relative to it and joined with `/`.
- **Scope**: excluded subtrees (`RepoPath` prefixes, segment-wise); VCS internals (`.git`, `.hg`, `.jj`, `.svn`, as a directory or a file) at any depth; a per-file size limit (default 1 MiB, larger files are `TooLarge`); a total limit (default 1 GiB, exceeding it fails the capture).
- **What is captured**: regular files only, bytes verbatim, each with its digest. **Dirty and untracked files are captured like any other**, because the worktree on disk in scope *is* the snapshot. `.gitignore` is not applied yet (open question).
- **Explicit skips**: symlinks (never followed, inside or outside the root), FIFOs, sockets and devices (never opened), oversized files, unreadable entries, invalid names. Every skip is in the result and in `SnapshotId`. None is a silent omission.
- **No execution**: capture only lists, stats and reads. It never runs git. `git status` can execute `core.fsmonitor` commands and clean filters from the repository's config, and hooks can run from other git commands. It never runs build tools or repository code.
- **Consistency**: one pass reads every file, and each read is checked against its pre-open stat (kind, size, mtime, device and inode). A second pass re-stats the whole tree. Any difference, including added or removed entries and directory changes, discards the capture and retries, up to 3 attempts, then returns `Conflict`. Bytes from different moments are never published together. Ceilings, marked `ponytail:` in code: stat-based detection misses a same-size rewrite within mtime granularity and a swap that is undone before the check; a file swapped for a FIFO between stat and open blocks the open. Walking with `openat` (cap-std) and re-hashing is the upgrade if hostile concurrent writers are in scope.
- **Memory**: the capture is held in memory up to the total limit. Streaming and persisted retention of captured bytes for hydration are Phase 2B.

## Consequences

- Kernel identity code is unchanged except for documentation. Capture lives in the runtime and adds one dependency (`sha2`, pure Rust).
- Snapshot IDs are reproducible across machines for identical content, so they can key shared caches.
- No cross-snapshot continuity: incremental indexing must recompute IDs, and any judgment reuse waits for a `ContinuityKey` design.
- Case-variant repositories are indexable; consumers on case-insensitive platforms see collision diagnostics.

## Open questions

| Question | Needed before |
| --- | --- |
| Apply `.gitignore`/`.ignore` in scope? (Proposal: yes, by parsing them, for example with the `ignore` crate, never by running git) | Phase 2B ingestion of real repositories |
| Persisted captured-byte retention for digest-verified hydration, and its GC | Phase 2B/3 hydration |
| Default `RepoId` generation and derived-store location | Phase 2B store creation |
| Per-language synthetic names and `ModuleId` forms | Phase 2B language slice |
| Recording the git commit as provenance without running git | When provenance is surfaced |
| NFC/NFD collision detection | Before claiming macOS portability diagnostics |
