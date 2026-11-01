# API surface review rules (v2)

Scope: `src/oxide/__init__.py` (public exports), `src/oxide/api.py`
(`Repository`, operations), `src/oxide/models.py` (typed results/errors),
`src/oxide/mcp_server.py`, `src/oxide/cli.py`.

---

### SURF-001 — Integrations are clients of the public API only
**Severity:** BLOCKER · **Scope:** `src/oxide/cli.py`, `src/oxide/mcp_server.py`,
and any future adapter.

**Invariant:** the CLI and MCP server call `oxide.api` only and render its
results. They MUST NOT parse source, implement TreeIndex/TreeRouter or graph
traversal, rank candidates, derive judgments, apply relevance thresholds,
choose context items, truncate source to fit budgets, or read LadybugDB
directly. Display filtering must not silently become a second selector.
The MCP server is stateless: each request opens the repository, reads it
without creating a store, and closes it, so no ownership, lock or session
state outlives a request.

**What constitutes a violation:** any import of `oxide.core` or
`oxide.storage` outside the engine; any second selector or ranking in a
renderer; any direct LadybugDB access from an integration.

**Evidence required:** the import list and call sites in the changed
integration module.

---

### SURF-002 — Public API stays small and stable
**Severity:** MAJOR · **Scope:** `src/oxide/__init__.py`, `src/oxide/api.py`,
`src/oxide/models.py`.

**Invariant:** the public API is `open_repository`, `index_repository`,
`Repository`, the result types in `oxide.models`, and `OxideError`. A new
export or operation needs a demonstrated reuse, packaging or API reason
(SPEC § System architecture). Result types are frozen dataclasses; IDs and
component versions are part of the stable contract.

**What constitutes a violation:** adding a public export "because it could be
useful" without a stated need; changing an error `code` string once shipped;
changing entity key formats or component-version strings without a
derivation/version bump.

**Evidence required:** the diff to `__init__.py`/`api.py`/`models.py` plus the
reason in the PR.

---

### SURF-003 — JSON/output contract stability
**Severity:** MAJOR · **Scope:** `--json` output of `search`/`context`/
`review` (if present), MCP tool results.

**Invariant:** pack items and search hits stay flattened (no nested
`"symbol"` key); symbol identity stays `path#QualifiedName`; `ChannelStatus`
reports `unavailable` with a reason rather than a zero score. `oxide-units-v1`
is the declared counting contract.

**What constitutes a violation:** nesting fields under a new wrapper key;
changing the symbol-identity string format; reporting an unavailable channel
as empty or zero.

**Evidence required:** diff the actual serialized shape against
`docs/public-api.md`.

---

### SURF-004 — Arguments are validated at the API boundary
**Severity:** MAJOR · **Scope:** `src/oxide/api.py`.

**Invariant:** `limit` is an int in `[0, 1000]`; `budget` an int in
`[0, 1048576]`; `paths`/`symbols`/`changed` are iterables of str, never a
bare str; `selection` is a `(str, int, int)` tuple with
`0 <= start <= end < 2**63`. Violations raise `InvalidArgument`
(`OxideError` + `ValueError`).

**What constitutes a violation:** accepting a bare str where an iterable is
expected; widening a range without updating `MAX_LIMIT`/`MAX_BUDGET`; a new
argument without a documented bound.

**Evidence required:** the validation code and its tests.
