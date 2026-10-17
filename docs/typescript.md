# TypeScript language slice (`oxide-typescript-v1`)

The second language slice (BOOTSTRAP § Remaining, item 3), in `src/oxide/core/typescript.py`.
It has the same shape as the Python slice: one compiled tree-sitter query per dialect
collects every fact in C; Python only assigns captured nodes to their innermost declaration
scope (a bisect over body ranges) and resolves names conservatively. It produces the same
entity and relation model, so retrieval, TreeRouter, packing, the CLI and MCP serve
TypeScript repositories unchanged. There is no TypeScript runtime, compiler or type checker.

Parser: `tree-sitter-typescript==0.23.2` (its `typescript` and `tsx` grammars), pinned
beside `tree-sitter-python`, and named in the derivation components (`typescript`,
`typescript-parser`), so any change is a new generation.

## Files

| Path | Dialect | Notes |
| --- | --- | --- |
| `*.ts`, `*.mts`, `*.cts` | `typescript` | Declaration files (`*.d.ts`, `*.d.mts`, `*.d.cts`) are TypeScript sources too |
| `*.tsx` | `tsx` | JSX elements are parsed; the dialect is chosen by extension only |
| `*.js`, `*.jsx`, `*.mjs`, `*.cjs`, JSON, … | — | Not parsed: file evidence with `unsupported` coverage, as before |

Every TypeScript file is `FileInfo.language == "typescript"`. A file that is not UTF-8 is
`unsupported` with a diagnostic. A file with syntax errors is `partial`, with bounded
diagnostics as for Python, and keeps everything the grammar recovered.

## Identity and kinds

Keys follow ADR-0010 exactly as for Python: `symbol:<path>#Outer.inner~1`. The ordinal
separates same-named siblings in source order: overload signatures and their
implementation, getter/setter pairs, interface/class merging.

| Syntax | Kind | Name |
| --- | --- | --- |
| `function`, `function*`, overload/`declare function` signatures | `function` | declared name |
| `const f = () => …` / `function` / `function*` expressions (any declarator) | `function` | declarator name |
| `export const/let/var x = …` that is not a function (module or namespace level) | `variable` | declarator name |
| `class`, `abstract class` | `class` | declared name |
| methods, method/abstract signatures, constructors, accessors, arrow-function fields | `method` | member name |
| `interface` | `interface` | declared name |
| `type X = …` | `type` | declared name |
| `enum` | `enum` | declared name |
| `namespace A` / `module A`; `namespace A.B` | `namespace` | `A` (the binding it declares) |
| `declare module "pkg" { … }` | `namespace` | synthetic `<module>` |
| `export default function () {}`, `export default class {}`, `export default () => …` | `function`/`class` | synthetic `<default>` |

Synthetic names cannot be identifiers. Unexported variables are not symbols, like
Python module constants: they are module-level source, which ContextPacker packs as
module-level evidence. Not modeled as symbols: class fields that are not functions,
enum members, interface properties, `#private` members and computed or string-named
members (their names cannot form a key). Correction (measured, 2026-10-17): methods
of object literals and method signatures of type literals *are* symbols (kind
`method`; parent the innermost enclosing function, class, interface, enum or namespace,
else the file, since a type alias is not a scope). This section first said they were
not; the TypeScript evaluation's scorer validation found the difference
([phase-6/typescript-v1](phase-6/typescript-v1/README.md#scorer-validation)).

A declaration's range starts at its `export`/`declare` keyword or its decorators. Its
signature is the header from the first keyword to the body (`export class Derived extends
ns.Base implements Shape`, `export type Handler`, `export const defaultRetryPolicy:
RetryPolicy`). An exported variable's initializer is its body, so calls in it belong to
it.

Modules: `module:typescript:<path without extension>`, where a trailing `/index`
collapses to its directory (`src/ui/index.tsx` is `module:typescript:src/ui`); the
root `index.ts` has no module. `a.ts`, `a.d.ts`, `a.tsx` and `a/index.ts` are one module
of several files (`contains_logical`), as Python allows. A module `defines` its top-level
declarations.

## Relations and resolution

Resolution is by ES-module semantics over the files of the capture and lexical scoping.
There is no type inference. A name resolves to every declaration it can denote: one is
`resolved`, several are `ambiguous` (for example a value and a type merged under one
name), none or unknowable is `unresolved` with the name it used.

- **Imports** (`imports`, from the file): named, default (`import x`, the export
  `default`), namespace (`import * as ns`), combined (`import x, * as ns`),
  side-effect, `import x = require("…")`, and module-level re-exports
  (`export { a as b } from`, `export * from`, `export * as ns from`). A named import
  targets what the export resolves to; the others target the module. Unresolved names
  are shown as `<specifier>:<name>` or `<specifier>`.
- **Specifiers**: only relative ones (`./`, `../`, `.`, `..`), against the importing
  file's directory, with an optional `.js`/`.jsx`/`.mjs`/`.cjs`/`.ts`/`.tsx`/`.mts`/`.cts`
  extension (ESM `./a.js` names `a.ts`), then `<dir>/index`. Bare specifiers (packages,
  workspace packages, `tsconfig` `paths`/`baseUrl` aliases) are unresolved.
- **Exports**: a module exports its `export`ed top-level declarations under their name
  (or `default`), `export { local as name }`, `export default <identifier>`, and its
  re-exports. Explicit exports win over `export *`, which never re-exports `default`.
  A name that two star sources export is ambiguous. A star from an unresolvable
  module, any cycle, or an exported name that is rebound in its module makes the name
  unknowable (unresolved), never guessed.
- **Calls** (`calls`): `f()`, `new C()`, `ns.f()`, and in TSX capitalized or dotted JSX
  elements (`<Button />`, `<UI.Card>`; intrinsic `<div>` is not a call). A bare name
  resolves through the enclosing function and namespace scopes, then module-level
  declarations and imports. Class, interface and enum bodies are not scopes: a member
  is never a bare name. `ns.member` resolves only when `ns` is a namespace import of a
  known module. Other dotted calls (`this.m()`, `obj.m()`, `a.b.c()`) stay unresolved
  with their text, as `self.m()` does for Python.
- **Shadowing and rebinding**: parameters (with destructuring, defaults and rest), variable
  declarators, `catch` and `for … of/in` bindings, and `import x = …` aliases make a name
  opaque in the enclosing declaration's scope. Block scoping is approximated by the
  function, which can only turn a resolution into `unresolved`. An identifier assigned
  anywhere in the file (`=`, `+=`, `++`) is unresolved everywhere in that file.
- **Heritage** (`references`): `extends` and `implements` of classes, `extends` of
  interfaces (identifiers, `ns.Base`, `A.B`, generic arguments stripped), resolved
  from the enclosing scope. `extends mixin(Base)` is not recorded.
- **Tests** (`tested_by`): Jest/Vitest discovery by path: `*.test.*`/`*.spec.*` files and
  files under `__tests__/`. Their symbols have `test` set. A resolved call from a test
  file to a symbol outside test files adds `tested_by` from the callee to the calling
  scope: the test file itself for calls in `describe`/`it` callbacks.

Not modeled: type annotations and other type references (only heritage is), `this`/instance
member calls, decorators as relations, dynamic `import()`, CommonJS `require` calls,
`export =`, `declare global` augmentation (its declarations belong to the file), and
cross-language relations.

## Incremental indexing

The facts of each parsed file are cached in `facts.json` like Python's
(`oxide-typescript-facts-v1`), in one section per dialect (`python`, `typescript`,
`tsx`; container format `oxide-facts-v2`), because the same bytes parse differently
as `.py`, `.ts` and `.tsx`. `decode` checks cached facts as strictly as the Python
slice. Anything `parse` could not have produced is parsed again, never trusted. Resolution
and publication cover the whole snapshot, so an incremental index equals a full
rebuild (`tests/python/test_incremental.py`: barrel, re-export, namespace-import,
rebinding, deletion, move, dialect-change and damaged-facts steps, compared against
fresh full rebuilds of every logical fact).

## Evidence (measured, 2026-10-17)

- Conformance: `tests/python/test_typescript.py` covers every declaration kind, identity,
  signatures, the resolution rules above, JSX, test discovery and coverage. On
  `fixtures/ts_repo` (8 files, 53 entities, 134 relations), its three implementors of
  `RetryPolicy`, both cross-file callers of `shouldRetry`, the barrel's re-exports
  and the TSX component test resolve exactly. All 8 legacy TypeScript fixture tasks
  (`fixtures/benchmark.json`, `structural_benchmark.json`) pack every gold entity at
  1,024 units (entry rank 1–3 for 7 tasks, 13 for the callers task, reached by routing).
- Real repositories (shallow clones; one run on this laptop, capped at 2 CPUs):

  | Repository | TS files | Partial | Derive (cold) | Derive (all facts reused) |
  | --- | --- | --- | --- | --- |
  | `colinhacks/zod@680d19f7` | 515 | 4 | 1.11 s | 0.30 s |
  | `excalidraw/excalidraw@4c37956b` | 684 | 5 | 1.53 s | 0.40 s |

  End to end on excalidraw (`index()` with publication and FTS, fresh process each,
  median of 3): cold build 4.20 s (262 MB peak RSS; 7,305 entities, 77,199 relations),
  unchanged 0.17 s, one edited file 2.83 s (683 of 684 facts reused), 10% of files
  edited 3.17 s. Compiling the two queries costs ~32 ms per process at import. Python
  indexing is unchanged within noise (pytest corpus, 214 files, `incremental-indexing/
  bench.py` before/after, median of 3: cold 1.42 → 1.54 s, edit 1.10 → 1.17 s, parse and
  resolve times equal).

  Reused-facts derivations equal cold ones exactly on both. On a random sample of 14
  resolved calls in excalidraw, every target was correct by reading the source, including
  JSX components and an aliased re-export (`export { renderApp as render }`). Resolved
  shares: imports 41%/44% (zod/excalidraw), calls 13%/22%, mostly limited by member calls,
  which need types. The unresolved relative imports sampled (27 in zod, 603 in excalidraw)
  are re-exports of workspace packages or package stars and unexported variables,
  unresolved by the rules above.
- All 9 partial files are grammar gaps in `tree-sitter-typescript` 0.23.2, the latest on
  PyPI: variance annotations (`interface X<out T>`, TS 4.7) and generic calls inside
  `typeof import(…)` type queries. Declarations outside the damaged region still parse.

## Limitations and next steps

- No `tsconfig.json` (`paths`, `baseUrl`, project references) or `package.json`
  (`exports`, workspaces) resolution. Workspace packages are the largest unresolved
  class in monorepos. Supporting them means reading those files as declared
  configuration, a separate design.
- Type references beyond heritage, and member calls through typed receivers, need
  either type information or a declared heuristic, and are out of this slice.
- JavaScript (`.js`/`.jsx`) files are not parsed. The `tsx` grammar would parse most of
  them, but they would need their own coverage and resolution conformance.
- The quality of TypeScript context is gated only by the fixture tasks. A
  ContextBench-style TypeScript baseline (pinned repositories, human gold) is the
  next evaluation milestone before any TypeScript ranking change.
