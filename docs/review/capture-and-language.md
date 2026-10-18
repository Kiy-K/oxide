# Capture and language review rules (v2)

Scope: `src/oxide/capture.py`, `src/oxide/core/python.py`,
`src/oxide/core/typescript.py`, `src/oxide/core/indexing.py`.

---

### CAP-001 — Capture is read-only and never executes repository code
**Severity:** BLOCKER · **Scope:** `src/oxide/capture.py`.

**Invariant:** capture lists, stats and reads only. It never runs Git, hooks
or repository code. Symlinks are never followed; special files never opened;
every skipped in-scope entry is reported and part of the snapshot identity.
Unreadable or undecodable `.gitignore` rules fail the capture closed.

**What constitutes a violation:** executing repository code during capture;
following a symlink; opening a special file; silently skipping an entry
without recording it.

**Evidence required:** the capture code path and its tests.

---

### CAP-002 — Parsing failure is not an empty success
**Severity:** MAJOR · **Scope:** `src/oxide/core/*`.

**Invariant:** unsupported or partially parsed files carry diagnostics and
explicit coverage (`Coverage` complete/partial/unsupported). A file's parse
failure must not masquerade as an empty, successfully understood file.

**What constitutes a violation:** swallowing a parse error and reporting the
file as fully parsed; dropping diagnostics; claiming complete coverage for
a partial parse.

**Evidence required:** the indexing code and `FileInfo` output.

---

### CAP-003 — Language support is declarative-first
**Severity:** MAJOR · **Scope:** adding or changing a `LanguageProfile`.

**Invariant:** adding a language should extend the declarative extraction
path (grammar + tags/locals queries) rather than adding a bespoke procedural
walker, unless a gap analysis shows the declarative path cannot capture a
specific construct. New languages are registered in `src/oxide/core` and
their behavior pinned by conformance goldens.

**What constitutes a violation:** adding a hand-rolled AST walker without a
stated gap analysis; forking a language's queries into a divergent copy
without reason.

**Evidence required:** the gap analysis and conformance golden diff.

---

### CAP-004 — Entity identity is deterministic and snapshot-local
**Severity:** BLOCKER · **Scope:** `src/oxide/models.py`, indexing.

**Invariant:** entity keys are deterministic for the same identity inputs
and are never database row ids or legacy FNV ids. Symbol keys support
overloads, nesting and duplicate names. IDs are always interpreted with
repository and snapshot scope. Cross-snapshot continuity is not guaranteed.

**What constitutes a violation:** using a database-native id as an entity key;
changing key formats without a stated reason; assuming rename continuity.

**Evidence required:** the key construction code and its tests.

---

### CAP-005 — Relations retain provenance and ambiguity
**Severity:** MAJOR · **Scope:** `src/oxide/core/indexing.py`.

**Invariant:** language adapters normalize facts into common entities and
relations, distinguishing syntactic evidence from resolved relationships and
retaining ambiguous/unresolved references. Relations carry type, endpoints,
snapshot/derivation scope, provenance and source evidence.

**What constitutes a violation:** collapsing ambiguous and resolved references;
dropping provenance or source evidence; silently dropping a duplicate
qualified name.

**Evidence required:** the relation construction code and tests.
