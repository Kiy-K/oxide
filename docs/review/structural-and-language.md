# Structural search and language-support review rules

Scope: `src/structural_relations.rs` (index-time precomputation),
`src/tree_sitter_structural.rs` (the Tree-sitter query substrate),
`src/retrieval.rs`'s `RelationGraph::callers_of`/`implementors_of`,
`src/parser.rs` / `src/languages/tags.rs` (language extraction).

`src/structural.rs` (the `ast-grep-core` adapter) and its query-time
`StructuralSearchProvider` trait no longer exist — migrated to precomputed
relations, `docs/precomputed-relations-migration/README.md`. LANG-001 and
LANG-002 below describe the current architecture; if you're reviewing a
diff against an old checkout that still has `structural.rs`, read that
migration doc first.

---

### LANG-001 — A repo-wide relation lookup must always be scoped before it reaches context output
**Severity:** BLOCKER · **Scope:** any caller of
`RelationGraph::callers_of`/`implementors_of`.

**Invariant:** `callers_of`/`implementors_of` are repo-wide by construction
(`retrieval.rs`) — they answer for every indexed symbol, not a bounded
subset. Every call site that feeds context output back to a request must
intersect the result with an explicit, bounded file scope — the files of
already-retrieved symbols, capped by `RetrievalMode::structural_budget()` —
before using it. This is not a style preference: a 902-file synthetic-repo
measurement showed an unscoped lookup returning **60x more results** than
the same lookup scoped to a realistic seed pool
(`docs/precomputed-structural-relations/README.md` "Reach/noise") — that
much fan-out is noise, not context, for a budgeted agent response.

**What constitutes a violation:** a new call site that uses
`graph.callers_of(name)`/`graph.implementors_of(name)`'s return value
without filtering by `scope_files` (or an equivalent explicit bound) before
it reaches `Candidate`/`ContextItem` output; a change that removes the
mode-dependent seed/file caps from `context.rs`'s bounded-expansion loop.

**Evidence required:** the measured reach differential — unfiltered vs.
scoped, 60x on the 902-file synthetic repo
(`docs/precomputed-structural-relations/README.md`) — plus the actual
`scope_files` construction and filter at the call site in question, showing
it's actually applied before the result is used.

**Exceptions:** offline benchmark/example harnesses that intentionally run
unscoped for measurement are not live requests and are fine.

---

### LANG-002 — Attribution (`structural_relations::enclosing`) is a tie-break contract, not an approximation to relax casually
**Severity:** MAJOR · **Scope:** `src/structural_relations.rs`'s
`enclosing()` and `compute_file_relations()`.

**Invariant:** mapping a raw call-site/base-clause line back to the
`Symbol` it belongs to is genuinely ambiguous in real source — three
distinct ties were found and fixed empirically, each pinned by a regression
test in `structural_relations.rs`'s own test module:

1. A single top-level definition's span is numerically identical to the
   file's Module fallback symbol's span — fixed by excluding Module from
   the span competition entirely (a pure fallback, not a competitor).
2. Two functions nested on one physical line have byte-identical spans —
   fixed by a secondary tie-break on qualified-name length (longer name =
   more deeply nested = correct target).
3. A class and its own single-line member/base-list attribution needs the
   class's own declared identity, not span containment, because a
   same-line member can share the class's exact span — fixed by keying
   `all_bases_in_file` on the class node's own start line plus a
   `Class`/`Interface` kind filter, not on containment or bare name (name
   alone over-attributes across differently-nested same-named classes).

A change to the tie-break ordering, the Module exclusion, or the bases
join key without re-running (and, if behavior changes, updating)
`call_inside_a_one_line_nested_function_attaches_to_the_inner_function`,
`top_level_calls_attach_to_the_module_fallback_symbol`, and
`same_bare_name_classes_in_different_scopes_do_not_cross_attribute_bases`
(all in `structural_relations.rs`) is very likely reintroducing one of
these three bugs, not simplifying dead code.

**What constitutes a violation:** any edit to `enclosing()`'s sort key,
the Module-exclusion `filter`, or `all_bases_in_file`'s line/kind join
without those three tests passing unmodified, or a PR that reverts to
name-based or pure-containment attribution for bases without new evidence
that the fanning/mis-attribution bugs those approaches had are actually
fixed some other way.

**Evidence required:** `cargo test --lib structural_relations` (5 tests as
of this migration) passing unmodified, or an explicit accounting of which
of the three tie-break cases a proposed change affects and why.

**Exceptions:** none for the three specific ties above; a genuinely new
tie-break case is welcome as a fourth regression test, not a reason to
loosen the existing three.

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
a template to copy for new languages. The same declarative-first bar
applies to `queries/*_{callers,implementors}.scm`
(`tree_sitter_structural.rs`) for any new language's structural relations.

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
