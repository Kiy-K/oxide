# Phase 3.4a parity/regression evidence

Raw data backing the tree-sitter-tags migration decision (see `AGENTS.md`
and `src/languages/tags.rs`'s module doc for the architecture).

## Definition parity (`fixture_repo_diff.txt`)

`cargo run --example parity_report --release`, comparing
`extractor_for_handwritten` vs `extractor_for` (tags) over `fixtures/py_repo`
and `fixtures/ts_repo`. After the two fixes below, only two intentional
differences remain: `__all__` module-constant capture (Python, a gain) and
the documented decorator-span gap on `RetryPolicy.exhausted` (pinned by
`languages::tags::tests::decorator_line_is_not_included_in_span`).

Two real bugs were found and fixed via this harness, not by inspection:
- `tag.span` from tree-sitter-tags is the *name* node's line only (ctags
  "jump here" convention), not the definition's full extent — every symbol
  collapsed to a single-line span until `byte_to_line(tag.range.*)` replaced
  it.
- `content_hash`/`signature` originally hashed the raw byte slice
  (`src[d.start..d.end]`), which starts at the keyword, not the line's
  leading indentation — different from what `Symbol::span_text()` (line
  based) recomputes for the same symbol. Fixed to hash the line-reconstructed
  body, matching the handwritten extractors' own convention. Pinned by
  `languages::tags::tests::content_hash_matches_span_text_reconstruction`.

## Retrieval regression

`fixtures/benchmark.json` via `oxide eval` (offline hashed embedder, no
server needed) — before vs after the `export_statement declaration:
(lexical_declaration ...)` override in `queries/typescript_tags.scm`:

| | vector-only recall@5 | hybrid recall@5 |
|---|---|---|
| handwritten extractor (baseline) | 0.818 | 0.909 |
| tags, before the export-const override | 0.727 | 0.818 |
| tags, after the override | 0.818 | 0.909 |

The gap was one task, `ts-default-policy-const` (recall 1.000 -> 0.000 ->
1.000): `export const defaultRetryPolicy = new ExponentialBackoff(3, 150)`
wasn't indexed at all under upstream `tags.scm` alone — JS's own
`@definition.constant` pattern only matches the rare bare `export x = ...`
assignment form, not `export const x = ...`.

`tests/benchmark_gate.rs` (hybrid >= vector-only recall on
`fixtures/benchmark.json`) and the full `cargo test` suite (49 lib tests +
all 10 integration test files) pass with the tags extractor as the default,
with no other code changes to retrieval/ranking/fusion/context-budget code.
The full ContextBench sweep (21 pinned tasks x 7 repos, HTTP embedder) was
deliberately not run: it would take on the order of an hour, mutate or
require re-indexing the shared `~/.cache/oxide-contextbench/` corpus used by
the separate, paused embedding survey, and `benchmark_gate` plus the fixture
table above already demonstrate retrieval survives the extraction swap.

## Throughput / index size (real repo, not fixtures)

`~/.cache/oxide-contextbench/repos/darkreader` (197 TS/TSX files, copied to
`/tmp` — the shared cache itself was never indexed or modified):

| | symbols | index size | `oxide index` time |
|---|---|---|---|
| handwritten extractor | 993 | 2.1M | 124ms |
| tags extractor | 1355 | 2.8M | 212ms |

+36% symbols (interface method signatures, module/export constants,
constructors, JSDoc-adjacent `@doc` capture — none of which the handwritten
extractor produced), +33% index size, ~1.7x slower indexing. The compiled
`tags_query` (JS+TS concatenated, ~250 lines) is cached once per process via
`OnceLock` in `TagsExtractor`; before that fix the same repo measured ~15x
slower (query recompiled per file) — a real bug caught by this same
throughput check, not a report footnote.

## Rust/Go feasibility (Step 7 spike, not productized)

Both `tree-sitter-rust 0.24.2` and `tree-sitter-go 0.25.0` build cleanly
against the tree-sitter 0.27 core already required for this migration (no
further version bump). Both ship `queries/tags.scm`, neither ships
`locals.scm`.

- **Go**: closer to mechanical. `tags.scm` already captures functions,
  methods (receiver is a field on `method_declaration` itself — no
  containment-stack reconstruction needed the way Python/TS methods require
  it), types, package-level `var`/`const`, and even `import_declaration` and
  JSDoc-style `@doc` comments. Import coverage upstream is a genuine
  improvement over Python/TS, where OXIDE has to fill it via `collect_meta`.
- **Rust**: `tags.scm` covers struct/enum/union/type/trait/mod/macro
  definitions, distinguishes method vs function (`declaration_list`-wrapped
  `function_item` under `impl` blocks), and captures calls/macro invocations
  as references — but never captures the `impl_item` itself as a definition,
  so OXIDE's containment-stack reconstruction (used for Python/TS
  parent/qualified-name derivation) has nothing to nest a method under
  without a small additional query capturing the `impl` block's type name.
  That's the one piece that isn't purely mechanical; everything else fits
  the existing `LanguageProfile` shape. No `use`-import capture upstream
  either (same class of gap `collect_meta` already fills for TS).

Neither language has a `SymbolKind` that maps cleanly onto every construct
(Rust's `struct`/`enum`/`union` all landing on `Class`; a Rust `macro` has no
OXIDE kind at all) — a minor mapping decision, not an architectural blocker.
