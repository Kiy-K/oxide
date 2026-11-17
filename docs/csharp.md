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
  and `params`. Overloads of equal arity are not told apart by argument type: the
  declaring type's own fitting methods win, several fitting ones stay as a set only
  if they resolve to one method.
- **Unresolved by design:** calls on `var`, expression results, tuples, extension
  methods, external types (BCL, NuGet), `dynamic`, and names an unknown base type
  could declare.

## Limits and evidence

No oracle against the Roslyn compiler was run: no .NET SDK was available in the
development environment, so resolution is validated by the fixture tests
(`tests/python/test_csharp.py`, `fixtures/csharp_repo`) rather than a compiler. Ranking,
routing and packing are untouched: the ContextBench py gate's `metrics.json` is
byte-identical to `baseline-v21` after the change (only the derivation id differs).
