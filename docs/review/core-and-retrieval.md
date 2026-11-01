# Core and retrieval review rules (v2)

Scope: `src/oxide/core/` (`indexing`, `python`, `typescript`, `retrieval`,
`routing`, `packing`), `src/oxide/storage` (read view), `src/oxide/api.py`
(orchestration).

---

### CORE-001 — Core is pure policy over a read view
**Severity:** BLOCKER · **Scope:** `src/oxide/core/*`.

**Invariant:** `oxide.core` holds repository intelligence and deterministic
policy. It has no filesystem or database access except through the read view
it is given; no MCP, CLI presentation, agent or product-integration
knowledge. Domain values are plain frozen data; no database-native types,
Cypher, protocol envelopes or presentation objects. Unit tests MUST run
without a model, editor, MCP server or network.

**What constitutes a violation:** importing LadybugDB or filesystem APIs
into `oxide.core`; adding presentation or protocol types to domain values;
a core function that opens its own store.

**Evidence required:** the import list and signatures of the changed core
module.

---

### CORE-002 — Retrieval finds entry points; routing finds context
**Severity:** BLOCKER · **Scope:** `Repository.search`, `Repository.build_context`.

**Invariant:** retrieval produces candidates, never final context directly.
`build_context` runs retrieval → TreeRouter → deterministic selection →
packing. Search and context construction remain distinct operations.

**What constitutes a violation:** returning a packed bundle from `search`;
collapsing routing/selection into retrieval; applying a relevance threshold
inside retrieval that decides final inclusion.

**Evidence required:** the call graph of the changed operation.

---

### CORE-003 — Budgets are never exceeded
**Severity:** BLOCKER · **Scope:** `oxide.core.packing`.

**Invariant:** a tiny or zero budget yields an empty or smaller valid
bundle; the packer never exceeds the budget to force an item. `ContextBundle.used <= budget`.

**What constitutes a violation:** rounding up to fit an item; counting units
inconsistently with `oxide-units-v1`; exceeding budget in any path.

**Evidence required:** the packing code and its tests.

---

### CORE-004 — Deterministic ordering and provenance
**Severity:** MAJOR · **Scope:** retrieval, routing, selection, packing.

**Invariant:** required algorithmic limits, stable order, score meanings and
evidence provenance are domain policy. Omissions carry stage and reason;
bundles name the snapshot they were read from. Degradations are reported, not
hidden.

**What constitutes a violation:** non-deterministic traversal order without
a stated reason; dropping omission/provenance fields; hiding an unavailable
channel as empty.

**Evidence required:** the changed ordering code and golden tests.

---

### CORE-005 — Lexical channel availability is explicit
**Severity:** MAJOR · **Scope:** lexical/FTS usage.

**Invariant:** if the lexical index or the pinned FTS extension is
unavailable, the lexical channel reports `unavailable` with its reason;
structural seeds and routing continue. The FTS extension is the only
network access OXIDE makes, and only via `oxide prepare`; nothing downloads
it implicitly. It is sha256-checked on every use.

**What constitutes a violation:** reporting an unavailable lexical channel as
empty or zero; downloading the extension implicitly; skipping the digest
check; introducing another network call.

**Evidence required:** the FTS availability check and download path.

---

### CORE-006 — Vectorless, no embeddings
**Severity:** BLOCKER · **Scope:** any proposal to add embeddings/vectors.

**Invariant:** there are no embeddings, vector indexes or model runners.
Reintroducing one requires a new ADR with paired evidence. Judges, if ever
added, are optional external services receiving bounded evidence.

**What constitutes a violation:** adding an embedding/vector dependency or
index without an ADR; running a model inside the engine.

**Evidence required:** the ADR referenced by the PR.
---

### CORE-007 — FTS extension paths must be usable
**Severity:** MAJOR · **Scope:** store/extension paths.

**Invariant:** store and extension paths must be UTF-8; on Windows they must
be ASCII. A path that cannot be used is refused before download with a named
variable, never silently mishandled.

**What constitutes a violation:** accepting a non-UTF-8 path; downloading an
extension that cannot be loaded.

**Evidence required:** the path validation code.
