# Language support

What OXIDE extracts per language, what it costs, and what it still misses.
Behavior is pinned by `tests/language_conformance.rs`'s committed goldens
(`fixtures/conformance/<lang>.golden.json`) — those files, not this page,
are the source of truth; regenerate them with `UPDATE_GOLDEN=1` and read
the diff.

Every language goes through the same path: a `LanguageProfile` (grammar +
`queries/*_tags.scm`) feeds `tree-sitter-tags`, and `languages/tags.rs`
normalizes the flat tag list into OXIDE's `Symbol` IR. There is no
per-language extractor and no language-specific retrieval behavior.

## Coverage matrix

| Dimension | Python | TypeScript | TSX | JavaScript / JSX | Rust | Go | Java |
|---|---|---|---|---|---|---|---|
| grammar | python | typescript | tsx | **tsx** (shared) | rust | go | java |
| definitions + stable ids | yes | yes | yes | yes | yes | yes | yes |
| kinds | class/function/method/constant | + interface/type_alias/enum | same as TS | class/function/method/constant (TS-only kinds never match) | class/enum/type_alias/interface(trait)/module(mod)/method | class/interface/method/function/constant | class/interface/enum/method |
| qualified names | containment | containment | containment | containment | containment (incl. `impl` blocks) | receiver (`Store.Get`) | containment **+ parameter signature** (`Store.get(String,String)`) |
| parent/child containment | yes | yes | yes | yes | yes | interface members only | yes (incl. inner/nested classes) |
| imports | `import` / `from … import` | `import` / `export … from` | same as TS | same as TS **+ CommonJS `require()`** | `use` trees | `import` specs | `import` / `import static` |
| references | token intersection | token intersection | token intersection | token intersection | token intersection | token intersection | token intersection |
| calls | calls + attribute calls | calls + member calls | + JSX elements | same as TSX (JSX included) | + macro invocations | calls + selector calls | invocations + `new X(...)` |
| inheritance | base classes, incl. `abc.ABC` | `extends`/`implements`, incl. qualified + generic | same as TS | same as TS | `impl Trait for T`, supertrait bounds | struct/interface embedding | `extends`/`implements` on class, interface, enum, record |
| decorator/annotation-inclusive spans | yes | yes | yes | yes | n/a | n/a | yes (free — annotations sit inside the declaration node) |
| broken files | module fallback | module fallback | module fallback | module fallback | module fallback | module fallback | module fallback |
| determinism | pinned | pinned | pinned | pinned | pinned | pinned | pinned |
| incremental single-file edit | pinned | pinned | pinned | pinned | pinned | pinned | pinned |

### Coverage matrix, continued

Split into a second table purely so neither is unreadable; these languages
go through exactly the same path as the ones above.

| Dimension | Ruby | PHP |
|---|---|---|
| grammar | ruby | php (not `php_only`) |
| definitions + stable ids | yes | yes |
| kinds | class/module/method/function/constant | class/interface(+trait)/enum/module(namespace)/method/function/constant |
| qualified names | containment **+ singleton receiver prefix** (`Store.self.create`) | containment |
| parent/child containment | yes (`module` nests like a namespace) | yes; a braceless `namespace X;` spans its own statement only, so file-level declarations stay top-level (like a Java package) |
| imports | `require` / `require_relative` / `load`, literal string argument only | `use` statements (alias dropped) + `include`/`require`(`_once`) with a literal string |
| references | token intersection | token intersection |
| calls | calls and method calls; `Foo.new` attributes to `Foo` | free, `->`, `::`, and `new X()`; `new self/static/parent` excluded |
| inheritance | `<` **plus `include`/`extend`/`prepend` mixins**, direct children of the class or module body | `extends`/`implements` **plus trait `use`**, direct children of the declaration body |
| decorator/annotation-inclusive spans | n/a | yes, free — a PHP 8 `#[Attr]` sits inside the declaration node, as Java's annotations do |
| broken files | module fallback | module fallback |
| determinism | pinned | pinned |
| incremental single-file edit | pinned | pinned |

## What Python and TypeScript gained in this round

- **Decorated definitions span their decorators.** `@app.route`,
  `@pytest.fixture`, `@Injectable()` are inside the symbol's span, so they
  reach `content_hash`, `signature`, and the lexical index's body tokens. A
  decorator's own call now attributes to the decorated symbol instead of the
  file's module symbol.
- **Qualified and generic bases resolve.** `class Record(abc.ABC)`,
  `extends ns.Base`, `implements Iface<T>` — all previously produced no
  relation at all. `class Panel extends React.Component<Props>`, the most
  common class-heritage shape in a TSX codebase, produced nothing.
- **JSX element usage is a call.** `callers_of("Button")` returns the
  components that render `<Button />`. Intrinsics (`<div>`) are excluded by
  JSX's own lowercase rule.
- **`uses←` is narrowed to import-backed definitions.** Two files defining
  `Client`, one imported: the importer's `uses` edge points only at the one
  it imported.
- **The handwritten extractors are gone.** ~800 lines deleted; the tags path
  is the only extraction path.

Because all of that changes spans, hashes and relations for files whose
source never changed, `EXTRACTION_VERSION` is bumped to 2 and `update_base`
now forces a full reparse whenever the stored version is not this binary's.
An existing index repairs itself on the next plain `oxide index`; no `-a`
and no manual `.oxide` deletion.

## Performance

### Provenance

Everything in this section was measured on **2026-09-10** against the
`oxide` binary at commit **c0f5bcc** (`cargo build --release -j 2`), with
the offline hashed embedder — `OXIDE_EMBED_NATIVE=hashed`, with
`OXIDE_EMBED_URL` and `OXIDE_EMBED_MODEL` unset, which is what
`scripts/perf.sh` forces — so no model download or embedding server is
involved and the numbers are reproducible without one. These are indexing
and structural-retrieval measurements; they are **not** a retrieval-quality
claim and do not touch `docs/canonical-baseline.md`'s ruler. The
retrieval-quality gate for this whole round is
`./target/release/oxide eval --config fixtures/benchmark.json`, unchanged
throughout at vector-only recall@5 0.818 / hybrid 0.909.

Machine: Intel i7-13620H, a shared laptop, every run under `nice -n 10`.
**Each number is the median of 3 runs**, not a single run — single runs
taken a day apart on this machine differed by up to 1.7x in absolute terms
while their *ratios* held, so medians are the only comparable form. Do not
compare these absolutes against `docs/perf-baseline-v0.1.md`'s 2026-08-29
rows, which were single runs without `nice` on a differently loaded machine.

Repositories, at these exact revisions:

| repo | revision | how to get it |
|---|---|---|
| flask | `7ee9ceb71e868944a46e1ff00b506772a53a4f1d` | `~/.cache/oxide-contextbench/repos/flask` |
| darkreader | `a787eb511f45159c8869d30e5a6ba1f91cb67709` | `~/.cache/oxide-contextbench/repos/darkreader` |
| tokio | `1.52.1` | `~/.cargo/registry/src/*/tokio-1.52.1` |
| gin | `dcaa4296d111981ffb31ac3eba90bb63e1eb5ab9` | `git clone --depth 1 https://github.com/gin-gonic/gin` |

Commands, verbatim:

```bash
cargo build --release -j 2
nice -n 10 scripts/perf.sh <repo-path>     # real repos; copies the repo first
nice -n 10 scripts/perf.sh 200             # synthetic, all four languages
cargo build --release -j 2 --example structural_probe
nice -n 10 ./target/release/examples/structural_probe <repo-copy> <names...>
```

`scripts/perf.sh` copies the target repo to a temp dir before indexing and
editing it, so a real checkout is never mutated.

### Real repositories, one per language

| repo | language | files | symbols | cold index | no-change | 1-file edit | peak RSS | index size |
|---|---|--:|--:|--:|--:|--:|--:|--:|
| flask | Python | 80 | 1,755 | 865 ms | 31 ms | 180 ms | 28 MB | 6.9 MB |
| darkreader | TS + TSX | 197 | 1,356 | 1,021 ms | 29 ms | 190 ms | 28 MB | 5.4 MB |
| tokio | Rust | 547 | 7,155 | 5,666 ms | 131 ms | 315 ms | 51 MB | 33 MB |
| gin | Go | 99 | 2,076 | 1,338 ms | 38 ms | 520 ms | 34 MB | 8.6 MB |

Every single-file edit reported `+1 new, ~1 changed, 2 written, N reused` —
incremental re-embedding holds for all four. The edit appends a real
declaration rather than a comment: a trailing comment changes no symbol's
span, so nothing re-embeds and the measurement is vacuous.

gin's 520 ms edit is the outlier and is explained, not mysterious: the file
`perf.sh` picks is the largest in the repo, and gin's is `context.go` at
1,539 lines (with a 3,957-line `context_test.go` alongside), so the single
file being reparsed is unusually large relative to a 99-file repo.

### Synthetic scaling, and the cost of adding two languages

`scripts/gen_bench_repo.py` now emits Rust and Go modules alongside Python
and TypeScript. Both rows below were measured in the same session, medians
of 3:

| repo | files | symbols | cold index | ms/symbol | no-change | index size |
|---|--:|--:|--:|--:|--:|--:|
| N=200, Python + TS only | 804 | 3,412 | 1,072 ms | 0.314 | 45 ms | 9.4 MB |
| N=200, all four languages | 1,205 | 6,615 | 1,996 ms | 0.302 | 71 ms | 18 MB |

0.302 against 0.314 ms/symbol — adding Rust and Go costs the *same* per
symbol within the spread of the runs themselves, and certainly not more.
(An earlier measurement of the same pair at 8a4b02e read 0.333 vs 0.329,
the difference in the other direction and equally small; treat the claim as
"no per-symbol cost", not as a signed delta.) Files
grow more slowly than symbols because Rust and Go modules are denser. The
5x catastrophic-regression threshold in `docs/perf-baseline-v0.1.md` is
nowhere near.

(To reproduce the Python+TypeScript-only row: generate with
`python3 scripts/gen_bench_repo.py <dest> 200`, then delete `src/rs`,
`src/go` and `src/retry.rs` before pointing `perf.sh` at it.)

### Regression check against the pre-work binary

Both binaries, the same generated Python+TypeScript corpus, median of 3
cold indexes each, same session:

| binary | cold index | symbols | index size |
|---|--:|--:|--:|
| 43c9d1b (before this round) | 1,074 ms | 3,412 | 9.3 MB |
| c0f5bcc (after) | 1,072 ms | 3,412 | 9.4 MB |

Indistinguishable, with identical symbol counts. The individual runs for
43c9d1b were 1053/1074/1088 — the after figure sits inside that spread. The
decorator walk, the widened base-clause queries, the JSX patterns and the
attribution ladder cost nothing measurable. (The 43c9d1b row was measured
earlier the same day, same corpus, same methodology, from a worktree at
that commit.)

### Structural retrieval on real repositories

`cargo run --release --example structural_probe <repo> <names…>`, same
revisions as above:

- **gin (Go)** — `implementors_of("RouterGroup")` = `gin.go#Engine` (struct
  embedding); `implementors_of("IRoutes")` = `routergroup.go#IRouter`
  (interface embedding); `callers_of("Context")` returns real
  `Context.ClientIP`, `Context.Deadline`, … method calls.
- **tokio (Rust)** — `implementors_of("AsyncRead")` returns 43 implementors
  including `fs::File`, `BufReader`, both `ReadHalf`s and `Stdin`, plus the
  supertrait `AsyncBufRead`. `implementors_of("Future")` returns 64.
- **OXIDE's own `src/` (Rust)** — 25 files, 611 symbols, 678 ms cold;
  `implementors_of("IndexBackend")` = `storage.rs#SqliteStore`;
  `implementors_of("LanguageExtractor")` = `languages/tags.rs#TagsExtractor`.

Two honest artifacts in the tokio list: `async_read.rs#Box` and `#Pin` are
`impl AsyncRead for Box<T>` / `Pin<P>` blocks, which survive as symbols in
their own right because no same-named struct in that file dedups them away
— they are real code regions implementing the trait, not false positives.

## Remaining unsupported constructs

| Language | Not extracted | Why |
|---|---|---|
| Go | import *resolution* | A Go import names a package *directory* of many files, and is usually module-qualified (`github.com/…`) or stdlib. `resolve_module`'s contract is "exactly one unambiguous file", so Go imports are recorded on the symbol but never produce an `imported-definition` edge. Rust `use` paths do resolve (`crate::backend::Backend` → `src/backend.rs`). |
| all | import *bindings* (the names, not the module) | `SymbolKind::Import` is never produced. The `uses` precision this was wanted for came from file-level import resolution instead; per-name bindings would only help when two *imported* files define the same name. |
| all | `exported` for Python/Rust/Go | The flag is derived only from TypeScript's `export` wrapper; Python is unconditionally `true`, Rust `pub` and Go's leading-capital convention are not read. |
| Python | cross-file reference staleness within one run | Known, accepted, architectural — see AGENTS.md. |
| TypeScript | `.d.ts` files | Excluded by the scanner denylist. |
| TSX | JSX member usage as a *distinct* relation | `<ns.Button />` is recorded as a call of `Button`, on the same bare-name tier as everything else. |
| Rust | `macro_rules!` definitions | Dropped rather than mislabelled — OXIDE has no macro kind. |
| Rust | `union` | Lands on `Class`. |
| Go | interface *satisfaction* | Structural and semantic in Go; `bases` means embedding only, by design. |
| Go | named types vs aliases | `type Key string` lands on `Class`; only `type X = Y` is a true alias and it is not distinguished. |
| Go | package `var` | Lands on `Constant` — OXIDE has no variable kind. |
| Rust, Go | rarer grammar shapes, found empirically | The base/call queries cover the shapes that have actually been exercised — bare, qualified, generic, and their compositions — but the set is empirical, not exhaustive. Eight adversarial review rounds each turned up narrower ones (`impl external::Trait<T> for Local`, Go's `*pkg.Base` embedding) and the last rounds were finding compositions of shapes already covered separately. Expect more; each is a one-line `.scm` alternative plus a regression test. |
| TypeScript, TSX, JavaScript | accessor pairs and static/instance name pairs | `get x()` and `set x(v)` both qualify as `A.x`, and `static run()` alongside an instance `run()` both qualify as `A.run`; `parse_file_with`'s first-wins dedup keeps one of each. Pre-existing TypeScript behavior, verified byte-identical in JavaScript because the two share a grammar and a query — separating them means putting a discriminator in the qualified name (the route Java's overloads took), which would move every existing TypeScript symbol's id and re-embed every TypeScript index. Not worth that for a shape where both halves sit on adjacent lines. |
| JavaScript | a grammar of its own | `.js`/`.jsx`/`.mjs`/`.cjs` are parsed with the **TSX** grammar and the TypeScript tags/locals and TSX callers/implementors queries. TSX is a syntactic superset of JavaScript and resolves `<` the way a `.jsx` file does; 261/261 real `.js` files across tailwindcss and openlibrary parse with zero ERROR/MISSING nodes, as do the ambiguity traps (`const lt = 1 < 2 > 0`, `/a<b>c/g`), private fields, static blocks, generators and CJS. Forking the queries into `javascript_*.scm` copies would reintroduce exactly the drift `TSX_CALLERS_SRC`'s concatenation exists to prevent. |
| JavaScript | `module.exports` as an export | The `exported` flag comes from an ESM `export` wrapper only; CommonJS export assignment is not read. `require()` *is* read, as an import. |
| Java | fields | Instance and static fields produce no symbol. OXIDE has no variable kind, and Java fields are numerous enough that mapping them onto `Constant` (the way a Go package `var` is) would be mostly noise. Not asked for; easy to add as one `.scm` pattern if it turns out to matter. |
| Java | annotation-type elements | `String value();` inside an `@interface` is an `annotation_type_element_declaration`, not a `method_declaration`, so the `@interface` itself is a symbol (kind `interface`) but its elements are not. |
| Java | method references (`Foo::bar`) | The grammar gives two identifiers with no field distinguishing receiver from member, so the callee would be a guess. Deliberately absent from `java_callers.scm` under the same "conservative call relations" rule the rest of the call queries follow. |
| Ruby | `attr_accessor` / `attr_reader` / `attr_writer` | These synthesize methods at *runtime* from a method call; they are not declarations, so there is no span to hash, no signature to show, and N symbols would share one line. Recorded as an ordinary call on the enclosing class instead. Adding them means inventing spans, which is a different contract from every other symbol OXIDE stores. |
| Ruby | a `class Foo::Bar` name's namespace | The declared symbol is `Bar`, not `Foo.Bar` — `scope_resolution` in a class name reduces to its last segment, the same bare-name tier `calls`/`bases` live on. A `class Foo::Bar` and a `Bar` nested inside `module Foo` in the same file would collide. |
| Ruby | `define_method`, `method_missing`, `instance_eval` | Metaprogramming defines methods with no syntactic declaration at all. Out of scope by the same rule as `attr_accessor`, and unrecoverable without running the code. |
| Ruby | `exported` | Ruby's `private`/`public`/`protected` are method calls that switch a mode for everything after them, so the flag would need statement-order tracking inside a class body. Left `false`, like Rust and Go. |
| Ruby | a conditional or block-nested `include` | Only a direct child of the class/module body counts as a base. An `include` behind `if RUBY_VERSION > "3"` is conditional behavior, and walking deeper would also attribute a nested class's mixins to its enclosing one. |
| PHP | properties | `private string $name;` produces no symbol. Upstream tags it `@definition.field`, which OXIDE has no kind for — the same call Java's fields got, for the same reason. |
| PHP | `use` *resolution* | PSR-4 maps a namespace prefix onto a directory through `composer.json`, which OXIDE does not read, so a `use App\Contracts\Backend;` is recorded on the symbol but never produces an `imported-definition` edge. Same standing gap Go and Java imports have. |
| PHP | `require_once __DIR__ . '/x.php'` | Only a bare literal string counts as an import. The concatenation idiom's string is a *fragment* of a path rooted at the including file's own directory, which `resolve_module` has no notion of; recording half a path would resolve to nothing while looking handled. |
| PHP | `define()`, variable functions, variable classes | `define('FOO', 1)` is an ordinary call, and `$fn()` / `new $cls` name a runtime value. Skipped under the same rule as Ruby's bare identifiers and Java's method references. |
| PHP | trait conflict resolution (`insteadof`, `as`) | A `use A, B { A::run insteadof B; }` records both traits as bases and ignores the adaptation block. `bases` is a bare-name tier; which half of a conflict wins is semantics. |
| all | a method and a nested declaration packed onto **one source line** | `void top() { a(); } class Inner { void ping() { b(); } }` collapses both spans to zero lines, and `structural_relations::enclosing`'s longest-qualified-name tie-break then attributes `a()` to `Inner.ping` as well. Language-independent and pre-existing — the identical shape in TypeScript (`function outer() { a(); function inner() { b(); } }`) does the same thing, and it is the LANG-002 tie in `docs/review/structural-and-language.md`. Java annotations do **not** cause it (removing `@Deprecated` changes nothing); pinned both ways by `structural_relations.rs::java_annotations_do_not_cause_attribution_ties_but_one_line_packing_does`. Fixing it needs byte-range attribution instead of line numbers, which touches every language at once. |
| Java | package declaration / true type resolution | Qualified names stay file-scoped, exactly as Python modules are. Parameter types in a signature are normalized by erasure and last-segment name, not resolved — so `com.a.Key` and `com.b.Key` are one type as far as overload identity is concerned. Two overloads that differ *only* that way would collide; no real Java API does that. |

## Next language worth adding

Java shipped, and with it the answer to the overload question that was
blocking it — see `docs/java-feasibility/README.md`'s "Resolution". The fix
was to put a normalized parameter signature in Java's `qualified_name`, which
changes one *input* to the symbol-id formula for one language rather than the
formula itself, so no other language's ids moved and no existing index
re-embedded. JavaScript shipped alongside it for a different reason entirely:
it needed no new grammar, no new queries and no new identity rule, only a
`LanguageProfile` pointing at machinery that was already there.

**C#** is now the obvious next one: it has the same overload problem Java had,
and Java's answer transfers directly. **Ruby** and **PHP** still have upstream
tags and no identity conflict, making them the closest thing to mechanical
additions left. **C/C++** remains the expensive one — overloads plus a
preprocessor that makes byte-range containment unreliable — and the least
worth attempting under a no-semantic-resolution rule.

## What JavaScript and Java cost

Measured **2026-09-12** against this round's binary, same methodology as the
section above (release build, offline hashed embedder, `nice -n 10`, medians
of 3). These are a *separate session* from the four-repo table above: compare
them with each other, not in absolute terms against those rows.

| repo | revision | language | files | symbols | cold index | no-change | 1-file edit | peak RSS | index size |
|---|---|---|--:|--:|--:|--:|--:|--:|--:|
| tailwindcss | `~/.cache/oxide-contextbench/repos/tailwindcss` | JavaScript | 133 | 268 | 210 ms | 10 ms | 100 ms | 23 MB | 936 KB |
| gson | `8b4b55051489132190cb8d1c61eb9dc7f5381295` | Java | 264 | 4,388 | 2,010 ms | 80 ms | 270 ms | 40 MB | 26 MB |
| flask (control) | `7ee9ceb71e868944a46e1ff00b506772a53a4f1d` | Python | 80 | **1,755** | 540 ms | 30 ms | 130 ms | 28 MB | 7.0 MB |

The flask row is the regression control, and the number that matters in it is
**1,755 symbols — identical to the row measured before this round**. Adding
two languages changed no existing language's extraction at all; the five
committed conformance goldens say the same thing field-for-field.

Both new languages hold the incremental-re-embedding contract: tailwindcss's
one-file edit reported `1 new, 1 changed, 2 written, 266 reused`, gson's
`2 new, 1 changed, 3 written, 4,385 reused`.

`scripts/perf.sh` needed two fixes to produce this table, both worth knowing
about. It learned the new extensions for its single-file-edit probe, and it
stopped grepping OXIDE's human output for timings: the v0.1.1 terminal
styling pass renamed the `took Nms` line it had been parsing, so the harness
had been silently failing (`set -e` on an empty `grep`) since that commit. It
now reads `--json` for counts and `/usr/bin/time` for wall clock, neither of
which can drift with a rendering change.
