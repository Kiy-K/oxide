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

| Dimension | Python | TypeScript | TSX | Rust | Go |
|---|---|---|---|---|---|
| definitions + stable ids | yes | yes | yes | yes | yes |
| kinds | class/function/method/constant | + interface/type_alias/enum | same as TS | class/enum/type_alias/interface(trait)/module(mod)/method | class/interface/method/function/constant |
| qualified names | containment | containment | containment | containment (incl. `impl` blocks) | receiver (`Store.Get`) |
| parent/child containment | yes | yes | yes | yes | interface members only |
| imports | `import` / `from … import` | `import` / `export … from` | same as TS | `use` trees | `import` specs |
| references | token intersection | token intersection | token intersection | token intersection | token intersection |
| calls | calls + attribute calls | calls + member calls | + JSX elements | + macro invocations | calls + selector calls |
| inheritance | base classes, incl. `abc.ABC` | `extends`/`implements`, incl. qualified + generic | same as TS | `impl Trait for T`, supertrait bounds | struct/interface embedding |
| decorator-inclusive spans | yes | yes | yes | n/a | n/a |
| broken files | module fallback | module fallback | module fallback | module fallback | module fallback |
| determinism | pinned | pinned | pinned | pinned | pinned |
| incremental single-file edit | pinned | pinned | pinned | pinned | pinned |

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

## Performance

Measured with `scripts/perf.sh`, offline hashed embedder, under `nice -n 10`
on a shared laptop (Intel i7-13620H). Single runs, not medians — see
`docs/perf-baseline-v0.1.md` on noise before calling any delta a regression.

### Real repositories, one per language

| repo | language | files | symbols | cold index | no-change | 1-file edit | peak RSS | index size |
|---|---|--:|--:|--:|--:|--:|--:|--:|
| flask | Python | 80 | 1,755 | 501 ms | 30 ms | 104 ms | 29 MB | 7.0 MB |
| darkreader | TS + TSX | 197 | 1,356 | 614 ms | 31 ms | 126 ms | 33 MB | 5.4 MB |
| tokio 1.52.1 | Rust | 547 | 7,136 | 3,390 ms | 72 ms | 165 ms | 53 MB | 33 MB |
| gin | Go | 99 | 2,076 | 778 ms | 32 ms | 298 ms | 35 MB | 8.6 MB |

Every single-file edit reported `+1 new, ~1 changed, 2 written, N reused` —
incremental re-embedding holds for all four. The edit appends a real
declaration rather than a comment: a trailing comment changes no symbol's
span, so nothing re-embeds and the measurement is vacuous.

gin's 298 ms edit is the outlier and is explained, not mysterious: its
largest source file is `context.go` at 1,539 lines (with a 3,957-line
`context_test.go` alongside), so the one file being reparsed is unusually
large relative to a 99-file repo.

### Synthetic scaling, and the cost of adding two languages

`scripts/gen_bench_repo.py` now emits Rust and Go modules alongside Python
and TypeScript. Both rows below were measured in the same session, minutes
apart, on the same machine:

| repo | files | symbols | cold index | ms/symbol | index size |
|---|--:|--:|--:|--:|--:|
| N=200, Python + TS only | 804 | 3,412 | 664 ms | 0.195 | 9.4 MB |
| N=200, all four languages | 1,205 | 6,615 | 1,208 ms | 0.183 | 18 MB |

Adding Rust and Go is *slightly cheaper per symbol*, not more expensive —
their modules are denser, so files grow more slowly than symbols. Well
inside the 5x catastrophic-regression threshold.

A Phase-1 regression check on the same corpus: the pre-work binary
(43c9d1b) indexes the Python+TypeScript repo in 659 ms producing 3,412
symbols and a 9.4 MB index; the current binary gives 664 ms, 3,412 symbols,
9.4 MB. Identical within noise, despite the decorator walk, the widened
base-clause queries, and the TSX JSX patterns.

### Structural retrieval on real repositories

`cargo run --release --example structural_probe <repo> <names…>`:

- **gin (Go)** — `implementors_of("RouterGroup")` = `gin.go#Engine` (struct
  embedding); `implementors_of("IRoutes")` = `routergroup.go#IRouter`
  (interface embedding); `callers_of("Context")` returns real
  `Context.ClientIP`, `Context.Deadline`, … method calls.
- **tokio (Rust)** — `implementors_of("AsyncRead")` returns 42 implementors
  including `fs::File`, `BufReader`, both `ReadHalf`s and `Stdin`, plus the
  supertrait `AsyncBufRead`. `implementors_of("Future")` returns 60+.
- **OXIDE itself (Rust)** — `implementors_of("IndexBackend")` =
  `SqliteStore`; `implementors_of("LanguageExtractor")` = `TagsExtractor`.

Two honest artifacts in the tokio list: `async_read.rs#Box` and `#Pin` are
`impl AsyncRead for Box<T>` / `Pin<P>` blocks, which survive as symbols in
their own right because no same-named struct in that file dedups them away
— they are real code regions implementing the trait, not false positives.

## Remaining unsupported constructs

| Language | Not extracted | Why |
|---|---|---|
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
| Java | everything | Not implemented. See `docs/java-feasibility/README.md`. |

## Next language worth adding

Not Java. The spike (`docs/java-feasibility/README.md`) found the grammar
builds cleanly against the pinned tree-sitter 0.27 and every `tags.scm` gap
is ordinary work — but Java overloading collides on OXIDE's
`(file, qualified_name)` symbol identity, and that identity is a pinned,
cross-language invariant whose change re-embeds every index in every
language. Settle the overload question on its own terms first; adding Java
before then ships a language whose most common construct is silently
truncated.

The cheaper next steps, in order: **C#** and **Java** share the overload
problem, so both wait on the same decision. **Ruby** and **PHP** have
upstream tags and no identity conflict, making them the closest thing to
mechanical additions left. **C/C++** has both the overload problem and a
preprocessor that makes byte-range containment unreliable — the expensive
one, and the least worth attempting under a no-semantic-resolution rule.
