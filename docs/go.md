# Go source support

`oxide-go-v1` adds Go as a language slice of the existing engine: `core/go.py`
extracts syntax facts with `tree-sitter-go==0.25.0`, `core/go_resolve.py` resolves
them over the whole capture. No Go toolchain, `go list`, `gopls`, module download
or repository code runs during indexing or querying. The ordinary Python API, CLI,
retrieval, graph, router and context packer serve these files unchanged, and MCP
remains exactly one tool, `oxide_search`, with `search` and `context` modes.

## Extraction and identity

`.go` files report `FileInfo.language == "go"`. Package-level declarations become
symbols:

| Go declaration | Kind | Key segment |
| --- | --- | --- |
| `func F` (generic or not, with or without a body) | `function` | `F` |
| `func (r *T) M` / `func (T[P]) M` | `method` | `T.M` |
| `type S struct` | `struct` | `S` |
| `type I interface` | `interface` | `I` |
| method element `M()` of an interface `I` | `method`, nested in `I` | `I.M` |
| any other `type T U` or alias `type T = U` | `type` | `T` |
| each name of a package-level `const` / `var` | `constant` / `variable` | the name |

Keys are `symbol:<path>#<segment>`. Methods keep the file as their physical parent,
so their segment names the receiver base type (pointer, type arguments and
parentheses stripped). Equal segments in one file (several `init` functions,
declarations that differ only by build tags, broken code) take `~n` in source order.
Blank (`_`) declarations and declarations inside function bodies are not symbols;
their code stays in the enclosing symbol. Entity names are the plain identifiers
(`Get`, not `T.Get`).

A grouped declaration's entries (`const ( … )`, `var ( … )`, `type ( … )`) get their
own spec's byte range; a single declaration spans the whole declaration, keyword
included. Several names of one spec (`var a, b = f(), g()`) share its range.
Signatures are the declaration header up to the body (`func (s *Store) Get(key Key)
(string, bool)`, `type Store struct`, `const ModeRead = 1`). Doc comments are not
part of a symbol's range.

A package is the `.go` files of one directory that share a package clause:
`module:go:<dir>/<package>`, or `module:go:<package>` for the repository root.
Files of an external test package (`package foo_test`) and of a second clause in the
same directory are separate modules. A module's name is its import path when a
captured `go.mod` owns the directory (`example.com/shop/store`, `…/store_test`),
otherwise the directory. Modules `contain_logical` their files and `define` their
package-level symbols. Interface method elements are contained by their interface.

`go test` discovery marks `TestXxx`, `BenchmarkXxx`, `FuzzXxx` and `ExampleXxx`
package-level functions of `_test.go` files as tests (the suffix must not start with
a lower-case letter, so `Testable` is not a test). A resolved call made inside a test,
including inside its closures such as `t.Run` bodies, adds `tested_by`, the same
heuristic as the Python and TypeScript slices.

## Resolution

Everything below is established syntactically and by the Go specification's
package and file scoping. Nothing needs a type checker, and nothing that would is
inferred.

- **Imports.** The `module` line of every captured `go.mod` maps an import path to a
  directory by the longest matching module path. The directory's nearest `go.mod`
  must be that module (a nested module owns its subtree). The importable packages
  are that directory's package clauses other than `main` and `_test` packages, with
  at least one non-test file; one is resolved, several ambiguous. Paths through
  `testdata`, `vendor`, or `.`/`_`-prefixed directories, the standard library and
  other modules are unresolved and keep the path as their name.
- **Package scope.** A bare name resolves to package-level declarations of the
  same package in any of its files. Declarations in `_test.go` files are visible
  only from test files of the same package. An exported name not declared in the
  package can come from a dot-imported local package.
- **Qualified names.** `pkg.Name` resolves through the file's import binding: an
  alias, or the target's package clause (not the last path element). Only exported
  names of non-test files are visible.
- **Receivers.** Inside a method, `r.M()` on the receiver `r` resolves to the
  method `M` declared with the same receiver base type in the same package.
- **Conservative locals.** A name bound anywhere in a declaration (parameters,
  results, type parameters, `:=`, local `var`/`const`/`type`, `range`, type switch
  and `select` bindings, function literal parameters) is opaque throughout that
  declaration, so shadowing never produces a wrong edge. A receiver bound again is
  an ordinary local.
- **Calls** cover calls through an identifier or `x.F`, explicit instantiations
  (`F[T](x)`, `pkg.F[T](x)`), and conversions `T(x)` (resolved to the type).
- **References** cover a method's receiver type, embedded struct fields and
  interface elements, composite literal types (`T{}`, `&pkg.T{}`), and type
  names in those supported type positions. Generic instantiation arguments are not
  emitted as separate references; Go's grammar does not distinguish all of them from
  indexed expressions without type information.

Same-named declarations in several files (typically `_linux.go` / `_windows.go` or
`//go:build` variants) make a reference ambiguous, listing every candidate:
build constraints and `GOOS`/`GOARCH` file suffixes are not evaluated.

## Not modeled

These need type information, a build configuration or the module graph, and stay
unresolved (with their name) or absent rather than guessed:

- method calls on anything but the receiver (`v.M()`, `pkg.Var.M()`, chained
  calls), promoted methods and fields through embedding, interface dispatch and
  implementations (there is no `implements` relation);
- method values and expressions (`T.M`, `(*T).M`), and calls of function values
  or struct fields;
- uses that are not calls or the type references above: values of constants,
  variables and functions (`return ErrClosed`, `f := New`), and types in
  signatures, fields, and composite element types (`[]T{}`, `map[K]V{}`);
- the standard library, third-party modules, `replace` and `go.work` directives,
  vendored packages, cgo (`import "C"`) and generated code that is not captured;
- build constraints, file name `GOOS`/`GOARCH` suffixes and `ignore` builds;
- block scoping (a shadowed name is opaque in the whole declaration), labels,
  `goto`, and local types and functions as symbols;
- struct fields as symbols, and the type-checked validity of the program.

Structural search (`Repository.structural_search`, ast-grep) remains Python and
TypeScript only.

## Reliability

The shared bounded native query, downward-only node reads, depth limit (65,535),
UTF-8 check and bounded syntax diagnostics apply unchanged. Invalid UTF-8 or
excessive depth is unsupported file evidence; grammar recovery is partial and keeps
recovered declarations. `go.mod` files stay unsupported file evidence and are read
only for their `module` line during resolution, which always reruns over the full
capture, so a `go.mod`-only change re-resolves correctly with all facts reused.

Facts are cached by `(go, source digest)` under `oxide-go-facts-v1`; the cache decoder
validates types, ranges, identifier names, the package clause and the scope tree,
and anything else is parsed again. Derivation components add `go` (`oxide-go-v1`)
and `go-parser`; older indices are re-indexed. Evidence and reproduction:
[phase-6/go-v1](phase-6/go-v1/README.md).
