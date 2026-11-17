# C# source support

`oxide-csharp-v1` adds C# as a language slice of the existing engine:
`core/languages/csharp.py` extracts syntax facts with `tree-sitter-c-sharp==0.23.1`
(helpers in `csharp_syntax.py`, the facts' cache codec in `csharp_facts.py`), and
`core/languages/csharp_resolve.py` resolves them over the whole capture. No .NET SDK,
MSBuild, NuGet restore or repository code runs during indexing or querying. API, CLI,
retrieval, router and packer are unchanged; MCP stays the one `oxide_search` tool.

## Extraction and identity

`.cs` files report `FileInfo.language == "csharp"`. Types and their members become
symbols, nested as written:

| C# declaration | Kind |
| --- | --- |
| `class`, `struct`, `interface`, `enum`, `record` (also `record struct`), `delegate` | the same word |
| method, constructor, destructor, operator and conversion operator | `method`, `constructor`, `destructor`, `operator` |
| property, indexer (named `this`), event, field, enum member | `property`, `indexer`, `event`, `field`, `enum_member` |

Keys are `symbol:<path>#Outer.Inner.member`; equal segments (overloads, operators) take
`~n` in source order. Operators are named as the runtime does (`op_Addition`,
`op_Implicit`). A multi-declarator field is one symbol per variable. Local functions,
lambdas and anonymous types are not symbols; their code stays in the enclosing symbol.
A declaration's range is its tree-sitter node (attributes included, comments excluded);
its signature is the header without attributes or body, whitespace collapsed.

Namespaces are not symbols. A namespace is every type declared in it, wherever the
file: `module:csharp:<namespace>`, which `contain_logical`s those files and `define`s
their top-level types. Block and file-scoped (`namespace A;`) forms, nesting and
`global::` are understood; the global namespace has no module. Partial types across
files merge into one hierarchy for resolution.

Test methods carry an xUnit, NUnit or MSTest attribute (`Fact`, `Theory`, `Test`,
`TestCase`, `TestMethod`, `DataTestMethod`, …) by simple name, with or without the
`Attribute` suffix; a resolved call inside one adds `tested_by`.

## Resolution

Following the C# name-lookup order over captured source only; anything that would need
a type checker or reaches outside the capture is left unresolved, not guessed.

- **Types** resolve through type parameters (opaque), nested and inherited member types,
  then each enclosing namespace outward: its own types, `using A = X;` aliases, and
  `using N;` namespaces (`global using` applies to every file), then the global
  namespace. An ambiguous name stays unresolved.
- **`using static T;`** brings static members of `T` into scope for unqualified calls.
- **Attributes** resolve as `N` or `NAttribute`.
- **Members** are looked up on the nearest declaring type of the captured hierarchy
  (base class, interfaces, partial parts); `this.m`, `base.m`, `new T(...)` (resolves
  to `T`), qualified `A.B.m`, and locals/fields/properties with a declared type and
  `var`-free typed locals. Calls are filtered by arity, honouring optional parameters
  and `params`. Overloads are not told apart by argument type: when
  the declaring type and its captured bases both have fitting methods, all of them are
  candidates and the call is ambiguous rather than resolved.
- **Unresolved by design:** calls on `var`, expression results, tuples, extension
  methods, external types (BCL, NuGet), `dynamic`, and names an unknown base type
  could declare.

## Limits and evidence

Roslyn oracle (`scripts/csharp_oracle`, Microsoft.CodeAnalysis.CSharp 5.12 on the .NET 8
SDK; `scripts/csharp_oracle_compare.py` scores a resolved `calls`/`references` edge at
its source site against the oracle's binding, with the project's `DefineConstants`).
Measured 2026-11-17, edges OXIDE resolved vs. oracle bindings inside the tree:

| Repo (commit HEAD at clone) | calls agree / disagree | refs agree / disagree |
| --- | --- | --- |
| `fixtures/csharp_repo` | 11 / 0 | 7 / 0 |
| jbogard/MediatR `src` | 112 / 0 | 143 / 0 |
| JamesNK/Newtonsoft.Json `Src/Newtonsoft.Json` | 3,739 / 4 | 3,130 / 0 |

The 4 disagreements are overloads picked by argument type (e.g. `DeserializeObject` with
`params`, a call inside an overload reaching a sibling overload): OXIDE's arity filter
keeps a superset, the oracle picks one. Recall (oracle edges OXIDE also resolved) is
about 55% of calls and 44% of references on Newtonsoft.Json, deliberately: extension
methods, `var`, expression results and external types stay unresolved. Without the
project's `DefineConstants` the oracle compiles a different tree (`#if` excluded code):
OXIDE indexes every file's tokens regardless, so compare with the defines set.

Ranking,
routing and packing are untouched: the ContextBench py gate's `metrics.json` is
byte-identical to `baseline-v21` after the change (only the derivation id differs).
