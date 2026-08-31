# API surface review rules

Scope: `src/mcp.rs` (MCP tool list), `src/cli.rs` (subcommands), `--json`
output shapes across `search`/`context`/`review`.

---

### SURF-001 — MCP/CLI surface stays small unless evidence justifies growth
**Severity:** MAJOR · **Scope:** any new MCP tool or CLI subcommand.

**Invariant:** the MCP surface stays at its current minimal set (`context`,
`search`) and the CLI surface stays scoped to what's documented, unless a
change carries the same evidence bar the existing surface was held to. The
precedent is explicit: bounded structural-relation expansion (originally a
live `AstGrepProvider` AST scan, migrated to a precomputed
`RelationGraph` lookup — `docs/precomputed-relations-migration/README.md`)
and the reranker hook were both built and benchmarked before being wired
into `context.rs`, and `implementors_of`/whole structural search were
deliberately kept *out* of MCP — "Keep the isolated module, do not wire it
into `context.rs`/retrieval/MCP in this commit... revisiting production
wiring once there's evidence from real usage"
(`docs/astgrep-structural-search/README.md`, the original decision this
precedent traces to).

**What constitutes a violation:** a new MCP tool or CLI subcommand added
because it "could be useful" or "an agent might want this," without a
fixture/real-repo benchmark or a stated organic usage gap backing it — the
same standard `docs/retrieval-coordinator/README.md` and
`docs/astgrep-structural-search/README.md` were held to before their work
landed or stayed isolated.

**Evidence required:** check whether the PR includes (or points to) an
evidence doc under `docs/` in the style of the two precedents above. If the
new surface is wired in without one, that absence is the finding.

**Exceptions:** an evidence-backed addition following the same pattern
(isolated module first, evidence doc, then wiring as a distinct, later
change) is fine. The norm this rule enforces is "evidence before surface,"
not "no new surface, ever."

---

### SURF-002 — JSON output contract stability
**Severity:** MAJOR · **Scope:** `--json` output of `search`/`context`/
`review`, MCP tool results, `ErrorCode::as_str()`.

**Invariant:** pack items and search hits stay serde-**flattened** (no
nested `"symbol"` key); symbol identity stays `path#QualifiedName`
everywhere; `ErrorCode::as_str()` strings are a stable wire contract — an
existing variant's string is never renamed once shipped (see the doc
comment on `ErrorCode`: "part of the stable contract — do not rename an
existing variant's string").

**What constitutes a violation:** nesting fields under a new wrapper key;
changing the symbol-identity string format; renaming or removing an
existing `ErrorCode` string. Adding a *new* `ErrorCode` variant/string is
not a violation on its own.

**Evidence required:** diff the actual serialized shape (not just the
Rust struct) against `AGENTS.md`'s "JSON output contracts" section; for
`ErrorCode`, diff `as_str()`'s match arms directly.

**Exceptions:** adding a new optional field to an existing response type is
additive and fine — only removing, renaming, or re-nesting an existing
field is a violation.
