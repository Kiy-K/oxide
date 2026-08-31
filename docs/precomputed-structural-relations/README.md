# Precomputed structural relations vs query-time structural search

> **Migrated.** The narrow-hybrid recommendation below shipped — see
> `docs/precomputed-relations-migration/README.md` for the migration
> itself, its acceptance-criteria evidence, and final numbers (the index-time
> figures below used this experiment's unoptimized second-pass shape,
> since folded into `update_index` directly). Kept as-is below for
> historical record.

Evidence for whether OXIDE's callers/implementors relations (currently
answered at query time by `StructuralSearchProvider` —
`structural.rs::AstGrepProvider`, `tree_sitter_structural.rs::TreeSitterStructuralProvider`,
see `docs/treesitter-structural-eval/README.md`) can instead be precomputed
at index time and served as cheap graph lookups, per the task brief. Purely
additive: `context.rs:224` still calls `AstGrepProvider` directly, unchanged;
neither structural backend was removed.

## What was built

- **`Symbol` gained two experimental fields**, `calls: Vec<String>` and
  `bases: Vec<String>` (`symbols.rs`), `#[serde(default)]` so every existing
  serialized `Symbol` still deserializes. Deliberately excluded from
  `index::embed_text` — confirmed by reading `embed_text`'s body before
  writing any code — so `content_hash` and embeddings are provably unaffected
  (the task's "keep embeddings frozen" requirement, verified structurally,
  not just asserted).
- **Two new unfiltered extraction functions** in `tree_sitter_structural.rs`
  (`all_calls_in_file`, `all_bases_in_file`), reusing that module's compiled
  `Query`/`OnceLock` cache — every match a file produces, not just ones
  matching one caller-supplied name the way `find_callers`/`find_implementors`
  (query-time) filter.
- **`structural_relations.rs`** (new): `index_structural_relations(store,
  root)` re-reads each indexed file, runs both extraction functions, and
  attributes each match to its enclosing already-parsed `Symbol` (see
  Attribution below), then writes `calls`/`bases` per symbol via two new
  `IndexBackend` trait methods. `load_symbols_with_relations(store)` is the
  read-side counterpart, merging `symbol_relations` back onto `Symbol`
  objects for `RelationGraph::build`.
- **A new SQLite side table**, `symbol_relations(symbol_id, kind, target)`
  — not new columns on `symbols`. `CREATE TABLE IF NOT EXISTS` is a no-op
  against an already-created table, so new columns on `symbols` would never
  appear on this repo's own existing `.oxide/index.db` (caught before
  writing any migration code, by reasoning about what `IF NOT EXISTS`
  actually does — see Storage below for the concrete failure mode this
  avoided). A new table name has no such problem.
- **`RelationGraph` gained two lazily-built reverse indexes** (`retrieval.rs`):
  `callers_of(name)` and `implementors_of(base_name)`, behind `std::cell::OnceCell`
  fields built only on first call — never by `build()` itself, so the
  frozen path (`neighbors()`, called on every `RelationGraph::build()` in
  `context.rs`/`retrieval.rs`/`review.rs`) pays nothing. Measured, not
  assumed — see RelationGraph::build cost below.
- **Never wired into `update_index`, `context.rs`, or the MCP surface.**
  `index_structural_relations` is an explicit **second pass** a caller runs
  after normal indexing — chosen specifically so nothing in the default
  path changes, at the cost of re-reading every file a second time (see
  Index-time cost below for why that's an honest but pessimistic number,
  not a floor).

## Attribution: two real bugs found via the conformance suite, not assumed

`tests/precomputed_relations_conformance.rs` translates `tests/structural_conformance.rs`'s
12 cases (originally `AstGrepProvider`-vs-`TreeSitterStructuralProvider`,
`FileSource`-only) into the real pipeline: write files, run `update_index` +
`index_structural_relations`, build a `RelationGraph`, query
`callers_of`/`implementors_of`. 10 of 12 translate; 2 do not (see the file's
own doc comment for why: `empty_file_list_returns_empty_instead_of_panicking`
has no analog for a name-keyed lookup, and `malformed_source_returns_empty_instead_of_panicking`
is vacuous — no symbols exist to attach relations to, so "empty" is true for
the wrong reason).

The first attempt at the 10 translatable cases found two real attribution
bugs — both fixed by changing the attribution rule, not by loosening the
test:

1. **A class and its own single-line member tied on span.** `class Square
   implements Shape { area() { return 2 } }` — the class and its nested
   `area` method have byte-identical line spans, so
   smallest-span-containment (the same idea
   `examples/structural_backend_compare.rs::enclosing_symbol` and
   `context.rs`'s own hit resolution use) ties, and the tie broke toward
   `Square.area` instead of `Square` — meaning the class's own base list was
   silently attributed to the wrong symbol. **First fix**: a base list is a
   property of the class *declaration*, not of whatever line it happens to
   share, so `all_bases_in_file` returned `(class_name, base_name)` keyed by
   the class's own captured `@name`, matching that name against
   `Class`/`Interface`-kind symbols. This intermediate fix had its own gap,
   found by the Codex review below (name collisions across differently-nested
   classes) — see that section for the final form, keyed by exact start line
   instead of name.
2. **A single top-level function tied with the file's Module fallback
   symbol.** `def f(...): ...` as a file's only definition gives the Module
   symbol (`parser.rs`: spans `1..=line_count`) the *exact same numeric span*
   as `f` itself, so `min_by_key` ties and can pick Module over `f` depending
   on `HashMap` iteration order — non-deterministic in principle, and it
   picked wrong on the first run. **Fix**: `enclosing()` now excludes
   Module-kind symbols from the span competition entirely, falling back to
   Module only when no non-Module symbol contains the line — Module is a
   pure fallback, not a competitor.

Both are pinned by `structural_relations.rs`'s own unit tests
(`calls_and_bases_attach_to_the_enclosing_symbol`,
`top_level_calls_attach_to_the_module_fallback_symbol`) and by the
conformance suite now passing 10/10.

One deliberate cardinality difference, stated rather than hidden:
`StructuralHit`s are per call-site; graph edges are per (caller-symbol,
callee-name) pair. `examples/precomputed_relations_compare.rs`'s
`py-callers-should-retry` row shows this concretely — query-time providers
report 5 raw hits (one test function calls `should_retry` twice), the
precomputed graph reports 4 distinct caller symbols. Recall is unaffected
(both are deduplicated to symbol-id sets before scoring), but the raw counts
differ and that's real, not a bug.

## Codex review: 3 more real bugs found and fixed, before any of the numbers below were trusted

Before measuring cost/latency, this pass's production-file surface
(`symbols.rs`, `index.rs`'s schema/trait, `retrieval.rs`'s `RelationGraph`,
plus the seven mechanical `Symbol {...}` literal edits needed to compile)
went through a Codex review focused on the `content_hash`/`embed_text`
frozen-path invariant, transaction correctness, a third attribution edge
case beyond the two already found, the relations merge, `OnceCell`
laziness, and reachable panics. `content_hash`/`embed_text`, the merge, the
`OnceCell` laziness, and panic-reachability all came back clean — confirmed
independently, not just by this pass's own tests. Three real bugs did not:

1. **`put_symbol_relations` (as originally written) opened one SQLite
   transaction per symbol**, not per file or per run — correct per-symbol
   (crash-atomic for that one row set), but a process interrupted mid-run
   left a silently partial relation cache with no completion marker, and
   large repos paid many more commits than necessary. **Fixed**: replaced
   with `put_symbol_relations_batch`, one transaction per file (matching
   `index_structural_relations`'s existing per-file loop structure) —
   narrows the interrupted-mid-run blast radius from "one symbol" to "one
   file" and cuts commit count accordingly. Full run-level atomicity (a
   completion marker, mirroring `set_meta_all`'s pattern for the main
   index) is deliberately still out of scope — see the migration plan's
   item 4.
2. **A third attribution tie, beyond the two the conformance-suite pass
   already found**: two functions nested on the same physical line
   (`function outer() { function inner() { target(); } }`) give `outer`
   and `outer.inner` byte-identical spans, so smallest-span-only
   containment ties and can (non-deterministically, by `HashMap` iteration
   order) attribute `target()` to `outer` instead of `inner`. Reproduced
   empirically before fixing — this was a real, not hypothetical, gap.
   **Fixed**: `enclosing()` gained a secondary tie-break on qualified-name
   length; a more deeply nested symbol's qualified name is strictly longer
   (`outer.inner` vs `outer`), so the longest name among span-tied
   candidates always identifies the innermost enclosing scope. Pinned by
   `call_inside_a_one_line_nested_function_attaches_to_the_inner_function`.
3. **Base attribution fanned onto every same-bare-name class in a file.**
   The name-based join key chosen to fix the *first* attribution bug (class
   vs. its own single-line member, see above) had its own gap: two
   *differently-nested* classes sharing a bare name — `Outer1.C extends A`
   and `Outer2.C extends B` — both matched `s.name == "C"`, so each got
   *both* bases `A` and `B`, producing false implementors. **Fixed**:
   `all_bases_in_file` now returns the class declaration's own start line
   instead of its name, and attribution matches on exact line equality
   (not containment) filtered to `Class`/`Interface`-kind symbols — a
   class's own start line is unique within a file and doesn't collide with
   a same-line member's start line for anything but that member, which the
   kind filter excludes. This single change fixes the name-collision bug
   without reintroducing the original containment-tie bug it replaced.
   Pinned by `same_bare_name_classes_in_different_scopes_do_not_cross_attribute_bases`.

All three fixes were verified empirically (a standalone repro before the
fix, confirmed fixed after) before being folded into permanent regression
tests in `structural_relations.rs`'s own test module — 4 attribution tests
total now. Every number in this report below was measured *after* these
fixes, not before; the conformance suite (10/10) and fixture benchmark were
re-run afterward and were unaffected (same recall, same hit counts — the
tiny fixture repos don't happen to exercise any of these three edge cases).

## Correctness: parity confirmed, on both the conformance suite and the benchmark

`cargo test --test precomputed_relations_conformance`: **10/10 pass**, translated
1:1 from the shared suite. `examples/precomputed_relations_compare.rs`
(new) runs all four backends — query-time `AstGrepProvider`, query-time
`TreeSitterStructuralProvider`, precomputed-unfiltered, precomputed
intersected with the same file scope the query-time providers were given —
through the same store on `fixtures/structural_benchmark.json`'s 4 gold
tasks in one process:

```
task                               ag       ts   pre_uf   pre_sc    ag_rec    ts_rec    uf_rec    sc_rec
ts-implementors-retrypolicy         3        3        3        3     1.000     1.000     1.000     1.000
ts-callers-shouldretry              2        2        2        2     1.000     1.000     1.000     1.000
py-implementors-notifier            2        2        2        2     1.000     1.000     1.000     1.000
py-callers-should-retry             5        5        4        4     1.000     1.000     1.000     1.000
```

Identical recall across all four backends, zero reach-diff lines printed
(unfiltered and scoped agree exactly on these 4 tasks) — but these fixture
repos are 6-8 files each, too small to say anything about whether unfiltered
reach adds noise at real scale. That question needed the synthetic repo
below.

## Index-time cost: real, and this pass's number is a pessimistic upper bound

`examples/precomputed_relations_scale.rs` on the same 300-module/902-`.py`-file
synthetic repo `structural_backend_scale.rs` used for query-time latency:

```
base update_index: 343ms, 5112 symbols
+ index_structural_relations: 252ms  (0.7x base index time)
  files=1204 symbols_with_relations=2103 calls=3904 bases=1
```

(Reproduced twice, post-fix numbers shown; 283ms/0.8x pre-fix — the fixes
above changed attribution correctness, not the cost profile.) An extra
~250-280ms on top of ~340-360ms — a real, non-trivial addition (roughly
+70-80% to indexing time as measured here), because this pass re-reads and
re-parses
every file a **second** time (module doc comment: this is the deliberate,
unoptimized shape chosen to keep the default `update_index` path completely
untouched). A real adoption folding extraction into `update_index`'s
existing per-file loop — reusing the file source and tree that loop already
has open for `extract_references`, rather than opening the file again —
would not pay that second full read+parse; this number is what the
second-pass shape costs, not a floor on what precomputation costs
architecturally.

## Storage impact: negligible, and the schema choice avoided a real hazard

```
db size: 13,836,440 -> 14,024,856 bytes (+188,416 bytes, +1.4%)
```

For context: `symbol_relations` holds 3,904 call edges + 1 base edge across
2,103 symbols with any relation at all, out of 5,112 total symbols. +1.4% is
close to noise.

The schema design itself avoided a hazard that would only have shown up
against a real, pre-existing `.oxide/index.db` — invisible against every
`:memory:` example and fresh-temp-dir test in this repo (including this
pass's own), which is exactly why it needed reasoning about, not just
testing. `CREATE TABLE IF NOT EXISTS` is a no-op against a table that
already exists; adding `calls_json`/`bases_json` as new columns on `symbols`
would never retrofit an existing on-disk index — `replace_file`'s INSERT and
`all_symbols`'s SELECT both name columns explicitly, so every symbol-table
statement would start failing with "no such column" against this repo's own
`.oxide/` the moment such a change shipped, silently, since no test in this
codebase indexes against a pre-existing on-disk DB. Using a **new table**
instead (`symbol_relations`) sidesteps this: `CREATE TABLE IF NOT EXISTS` on
a new name is picked up cleanly by any existing database, no
`SCHEMA_VERSION` bump, no migration code, nothing else changes. Also true,
and worth naming rather than skating past: `all_symbol_relations()` reads
from that table via a plain `SELECT`, which — like every table — is only
ever *created* by `SqliteStore::open()`'s schema-init (`SCHEMA_SQL`), never
by `open_read_only()`. That's an existing, pre-dating-this-pass property of
every table in the schema, not something new to this feature; it only
matters at all because nothing calls `all_symbol_relations()` outside this
experiment's own fresh stores.

## `RelationGraph::build` cost: zero measurable delta

```
RelationGraph::build: 0.728ms (no relations loaded) vs 0.688ms (relations loaded, still unused — OnceCell not yet touched)
```

Within measurement noise of each other (the "with relations" run is if
anything faster, pure jitter) — confirming the `OnceCell` design does what
it was built for: `build()` itself does no extra work regardless of whether
`Symbol.calls`/`bases` are populated, and the reverse indexes are built only
the first time `callers_of`/`implementors_of` is actually called. Combined
with `tests/benchmark_gate.rs`'s 19/19 and `context.rs`'s own tests passing
unchanged throughout this pass, this is the frozen-path proof the task asked
for — cited as evidence, not just asserted.

## Query latency: precomputed lookup is ~400-270,000x faster than query-time

Same synthetic repo, same target name (`normalize_key`, called by every one
of 902 Python modules), same whole-repo scope:

```
precomputed callers_of("normalize_key"): 600 hits, 0.118ms first call (builds+queries reverse index), 0.0031ms warm (lookup only)
query-time whole-repo callers_of("normalize_key"): ast_grep=600 hits in 844.51ms, tree_sitter=600 hits in 45.38ms
```

Identical hit count (600) confirms correctness at this scale, not just on
the 4-task benchmark. Even the *first* precomputed call — which pays the
one-time cost of building both reverse-index `HashMap`s over all 5,112
symbols — is ~385x faster than the query-time Tree-sitter provider and
~7,150x faster than the query-time ast-grep provider for the same
whole-repo answer; every call after that first one is a plain `HashMap`
lookup (~3 microseconds), effectively free.

## Reach/noise: precomputed's repo-wide scope is real, and it's too wide to use unscoped

This is the axis the task named as central, and where the picture stops
being one-sided:

```
reach: unfiltered precomputed = 600 callers across the whole repo; the same lookup scoped to a realistic 6-file seed pool = 10 callers (60x fewer)
```

`context.rs`'s bounded expansion caps the file scope it hands
`AstGrepProvider::find_callers` to 3-6 files
(`RetrievalMode::structural_budget()`) specifically because a caller in a
file the seed search never surfaced is an accepted, documented ceiling
(AGENTS.md), not a bug — and then caps hits taken per seed to 2
(`AST_GREP_HITS_PER_SEED`). An unfiltered precomputed answer dissolves that
ceiling entirely: 600 callers for one function is not a context-budget-sized
answer for an agent, it's most of the repo. The bounded scope was doing
real, load-bearing filtering work, confirmed here with a number (60x) rather
than assumed. Precomputation doesn't remove the need for that scoping — it
changes *where* the scoping happens: query-time file-list filtering over a
live AST scan (current), vs query-time file-set intersection over an
already-built in-memory reverse index (what a hybrid would do). Both are
cheap; only the latter also gets the latency win above, because the
expensive part (parsing and matching) already happened once, at index time,
for every symbol in the repo — not per query.

## Resolver complexity

- `structural_relations.rs` is ~305 lines including its own tests (4
  attribution regression tests after the Codex-review fixes) — attribution
  logic (`enclosing()`) plus the index/load functions.
- Two new `tree_sitter_structural.rs` functions reuse that module's existing
  compiled-query cache; no new query files, no new grammar plumbing.
- `RelationGraph`'s addition is two `OnceCell` fields plus two methods,
  mirroring `neighbors()`'s existing `HashMap`-of-`&str`-keyed-to-`&Symbol`
  shape exactly — same provenance tier as the existing `uses` edge (bare-name
  keyed, no scope analysis; `retrieval.rs`'s own doc comment already
  classifies `uses` as "Heuristic" for the same reason). Precomputation is
  not more precise about *which* callee a name resolves to than the
  existing heuristic tier — it's more precise about *what counts as a call*
  than `references`'s token-matching (an AST call-expression vs. any
  identifier occurrence), which is a real but separate finding, not a claim
  about resolution accuracy.

## Recommendation: narrow hybrid — precompute, but keep query-time scoping

Not a clean win for either extreme:

- **Keep pure query-time** loses on latency for no correctness benefit —
  the precomputed lookup (with query-time file-scope filtering, matching
  today's bounded contract exactly) is correctness-identical on every case
  tested and ~400-270,000x faster once the one-time reverse-index build is
  paid.
- **Replace with unfiltered precomputed** loses on the axis the task named
  as central: 60x more results than a bounded query would ever have
  surfaced, at real repo scale — noise, not signal, for a context-budgeted
  agent response.

The hybrid that reaches parity cleanly: **precompute `calls`/`bases` at
index time (fold into `update_index`'s existing per-file loop rather than
this pass's second-read shape), and at query time keep `context.rs`'s
existing bounded-file-scope contract exactly as today — just serve it from
`RelationGraph::callers_of(name)` intersected with `scope_files`, an
in-memory `HashMap` lookup, instead of a live `AstGrepProvider`/
`TreeSitterStructuralProvider` AST scan.** This keeps everything that makes
the current bounded expansion safe (the file-scope cap, the
`AST_GREP_HITS_PER_SEED` cap, the sort-then-truncate determinism) and
replaces only the expensive part underneath it.

### Minimal migration plan (not executed in this pass, per the task brief)

1. Fold `structural_relations::index_structural_relations`'s extraction into
   `update_index`'s existing per-file loop (same place `extract_references`
   runs today), removing the second file-read/parse this pass's measured
   ~250-280ms includes.
2. In `context.rs`'s bounded-expansion loop (`context.rs:224`), replace
   `AstGrepProvider.find_callers(lang, &file_sources, &seed.symbol.name)`
   with `graph.callers_of(&seed.symbol.name)` filtered to `scope_files` —
   `graph` (a `RelationGraph`) is already built one line above at
   `context.rs:148`.
3. Once nothing calls `AstGrepProvider`/`TreeSitterStructuralProvider`
   (confirm via `cargo build` with both modules temporarily `#[allow(dead_code)]`-checked,
   the same way this pass measured `tree_sitter_structural`'s binary-size
   delta), delete `src/structural.rs`, `src/tree_sitter_structural.rs`, the
   four `queries/*_{callers,implementors}.scm` files, the
   `ast-grep-core` dependency from `Cargo.toml`, and their three test
   files (`structural_conformance.rs` and both `structural_backend_*`
   examples) — `tests/precomputed_relations_conformance.rs` becomes the
   sole conformance suite for callers/implementors at that point.
4. Give `symbol_relations`/`calls`/`bases` a real migration story before
   this ships to existing users: either an explicit `ALTER TABLE`-based
   backfill path (none exists in this codebase for any prior schema change —
   this would be the first) or documenting that adopting this feature
   requires a full reindex, consistent with this repo's existing
   version-bump-forces-reindex precedent for non-additive schema changes.
5. Narrow scope reduction to flag in that migration's own write-up (not
   fixed here): `StructuralSearchProvider`'s query-time providers accept
   arbitrary `FileSource` text, including source outside the indexed
   corpus; a precomputed-only replacement can only answer for what's
   actually been indexed. Not a real limitation for `context.rs`'s current
   usage (`scope_files` always comes from indexed seeds already), but a
   genuine reduction in what the public trait could theoretically be used
   for.
