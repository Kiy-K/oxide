# Structural search and language-support review rules

Scope: `src/structural.rs` (ast-grep adapter), `src/parser.rs` /
`src/languages/tags.rs` (language extraction).

---

### LANG-001 — Structural search must always be bounded, never a request-time whole-repo scan
**Severity:** BLOCKER · **Scope:** any caller of
`StructuralSearchProvider::find_callers`/`find_implementors`.

**Invariant:** every call must pass a caller-supplied, explicitly bounded
file list — the files of already-retrieved symbols, capped by
`RetrievalMode::structural_budget()` — never a repo-wide scan triggered by
a live request. `FileSource`'s own doc comment states this as the contract,
not a benchmark footnote: ast-grep re-parses every file it's handed on top
of the parse OXIDE's indexer already did for that file, so an unbounded
scan is a real, structural cost, not a missing-cache problem.

**What constitutes a violation:** a new call site that passes
`store.all_symbols()`'s full file set, a repo glob, or otherwise removes the
mode-dependent seed/file caps from a request-time code path (`context.rs`'s
expansion loop or any future caller).

**Evidence required:** the measured cost differential — bounded (5 files):
17-149ms vs. unbounded (109 files): 1270-1320ms, a 10-70x gap
(`docs/astgrep-structural-search/README.md`) — plus the actual file-list
construction at the call site in question, showing it isn't bounded.

**Exceptions:** offline benchmark/example harnesses
(`examples/structural_cost.rs`) that intentionally run unbounded for
measurement are not live requests and are fine.

---

### LANG-002 — `ast-grep-core` types stay behind `structural.rs`'s abstraction
**Severity:** MAJOR · **Scope:** any module other than `src/structural.rs`.

**Invariant:** no `ast_grep_core` type (`Pattern`, `Language`, `StrDoc`,
`TSLanguage`, etc.) appears outside `structural.rs`'s internals; every
caller sees only `StructuralHit` and the `StructuralSearchProvider` trait.
This is the module's own stated design goal, and matters concretely because
`ast-grep-core` is pinned exactly at `=0.45.3` (pre-1.0, unstable API) —
letting its types leak elsewhere means an upgrade has to touch every leak
site instead of one module.

**What constitutes a violation:** `context.rs`, `retrieval.rs`, `mcp.rs`, or
`cli.rs` importing `ast_grep_core` directly; a new public item in
`structural.rs` whose signature exposes an `ast_grep_core` type instead of
`StructuralHit`.

**Evidence required:** grep for `ast_grep_core` imports outside
`structural.rs`; if any exist outside `#[cfg(test)]`/internal helpers,
that's the finding.

**Exceptions:** none — this is the entire point of the abstraction.

---

### LANG-003 — New language support should extend declarative Tree-sitter queries, not add a bespoke walker
**Severity:** MAJOR · **Scope:** adding or modifying a `LanguageProfile` /
`queries/*_tags.scm` / `queries/*_locals.scm`, vs. adding a new
`LanguageExtractor` implementation.

**Invariant:** the default extraction path (`extractor_for`) routes through
`TagsExtractor` + `LanguageProfile` (grammar + `.scm` queries) precisely so
that adding a language is "mostly grammar + `.scm` + normalization tests,
not a new procedural extractor." The handwritten, per-language AST-walking
extractors (`languages/python.rs`, `languages/typescript.rs`, reachable via
`extractor_for_handwritten`) are retained specifically for one documented
gap upstream `tags.scm` cannot express — decorator-inclusive spans — not as
a template to copy for new languages.

**What constitutes a violation:** a PR adding a new language via a new
hand-rolled procedural AST walker without first showing what `tags.scm`/
`locals.scm` cannot capture for that language (the kind of gap analysis
`docs/treesitter-tags-parity/README.md`'s Rust/Go feasibility spike did:
Go's `tags.scm` covers functions/methods/types/package vars almost
mechanically; Rust's needs one small additional query for `impl` block
containment, not a full walker).

**Evidence required:** the gap analysis itself — what specific construct
the declarative query approach cannot express for this language, and why.
Absence of that analysis in a PR adding a bespoke walker is the finding.

**Exceptions:** a language with no upstream `tree-sitter-tags` `tags.scm` at
all is a legitimate reason for a different approach — but the PR must state
that explicitly, not silently default to a walker out of familiarity.
