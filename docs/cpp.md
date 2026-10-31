# C++ source support

`oxide-cpp-v1` adds C++ as a language slice of the existing engine, built on the C slice
([c.md](c.md)): `core/languages/cpp.py` extracts syntax facts with
`tree-sitter-cpp==0.23.4`, `cpp_macros.py` handles unexpanded-macro idioms,
`cpp_facts.py` holds the facts' cache codec, and `cpp_resolve.py` resolves them over the
whole capture. No compiler, preprocessor, build system, `compile_commands.json` or
repository code runs during indexing or querying. The ordinary Python API, CLI,
retrieval, graph, router and context packer serve these files unchanged, and MCP remains
exactly one tool, `oxide_search`, with `search` and `context` modes.

## Files

`.cc`, `.cpp`, `.cxx`, `.c++`, `.hh`, `.hpp`, `.hxx`, `.h++`, `.ipp`, `.tpp` and `.inl`
files report `FileInfo.language == "cpp"`. A `.h` file is C++ when, outside comments and
string or character literals, it has C++-only syntax: `namespace x {`, `template <`,
`class X {` or `class X :`, an access label (`public:`), or `a::b`. Every other `.h`
stays C, so `#ifdef __cplusplus` / `extern "C"` guards in a C header keep it C. The test
reads only the file's bytes, so a file's dialect never depends on another file.

## Extraction and identity

Declarations become symbols at namespace scope and inside classes, nested as written,
including those in every branch of an `#if`/`#ifdef`/`#else`:

| C++ declaration | Kind | Key segment |
| --- | --- | --- |
| `namespace n { … }`, `namespace a::b { … }` (each block) | `namespace` | `n`, `a.b` |
| `class C`, `struct S`, `union U`, `enum E` / `enum class E`, with a body | `class` / `struct` / `union` / `enum` | the name |
| a member function (constructors, destructors, operators included) | `method`, nested in its class | `C.m` |
| an out-of-line definition `int C::m() { … }` | `method` | `C.m`, under the block it is written in |
| any other function, definition or prototype | `function` | `f` |
| `typedef … T;`, `using T = …;` | `type` | `T` |
| each declarator of a namespace-scope variable declaration | `variable` | the name |
| `#define N …`, `#define F(x) …` | `macro` | the name |

Keys are `symbol:<path>#<segment>`, the segment being the dotted path of the enclosing
symbols, so `shop.Store.get` is method `get` of class `Store` in `namespace shop`. An
out-of-line definition's segment adds its written qualifier to the block that holds it
(`int Store::get()` inside `namespace shop` is `shop.Store.get`); its physical parent is
that block or the file, as with Go methods. A function's kind is `method` when its
qualified name's scope is a captured class. In segments only, `.` and `~` become `_`
(`~Store` is `Store._Store`); `Entity.name` keeps the name (`~Store`, `operator==`,
`operator bool`). Equal segments in one file take `~n` in source order and children nest
under the ordinal'd segment: overloads (`Store.get`, `Store.get~1`), a prototype before
its definition, several blocks of one namespace (`shop`, `shop~1`), a tag and its
typedef. A declaration and its definition elsewhere are separate symbols (ADR-0010).

Not symbols: anonymous namespaces (their members are symbols of the enclosing scope, with
internal linkage), anonymous classes, data members, enumerators, friend declarations,
`using` declarations and directives, local declarations, lambdas and forward
declarations (`class C;`).

A symbol's range is its declaration (a template's from `template`), doc comments
excluded. Signatures are whitespace-collapsed headers with bodies cut out
(`class Store : public Backend`, `Store::Store(Mode mode)` without its member
initializers, `template <typename T> struct Cart`, `namespace shop`); a variable's is its
first line, a macro's its first line without the continuation backslash.

C++ has no modules here: there are no `module:` entities or `defines` edges, and
`#include` edges name files. There is no test convention, so no symbol is a test and
there are no `tested_by` edges.

## Resolution

Name lookup follows C++'s rules without a preprocessor, a type checker or overload
resolution; nothing that needs one is inferred.

- **Includes and visibility** are C's: `imports` edges file to file (beside, else a
  unique path suffix; `<x>` without a directory never binds), and a file sees its own
  declarations and those of every captured file its includes reach, transitively. C
  headers included by C++ files are visible; their declarations are global names.
- **Macros first.** A visible macro of a call's or type's name wins, as its expansion
  would.
- **Unqualified names** are looked up in the scope of the symbol using them, then
  outwards: for a member (in-class or out-of-line) its class, the class's captured
  bases, then the enclosing namespaces, the global namespace and the namespaces named by
  `using namespace` in scope. The innermost scope that declares the name decides: a type,
  variable or namespace there hides any function further out. Inside a class its own
  name is the class.
- **Qualified names** (`a::b::f`, `Store::open`) resolve relative to the same scopes;
  `::f` from the global namespace. `this->m()` looks only in the class and its bases.
- **Calls** need a visible declaration, as C++ does: a visible definition, or the
  definitions anywhere in the capture of a visible prototype with the same parameter
  count and linkage, else the visible prototype. Arity narrows overloads: a function
  takes from its fewest (any same-sized declaration's default arguments count) to its
  most arguments, or any number with `...` or a parameter pack. A call through a class
  name (`T(args)`) resolves to `T`'s constructors of that arity. Several candidates are
  ambiguous and listed; calls through objects (`x.f()`, `p->f()`), function pointers and
  expressions are not recorded.
- **References** cover type names in any type position, qualified or not (template
  arguments, bases, casts, `new T`), resolved among visible types (a typedef over a
  same-named tag). A prototype references its definitions (same name, parameter count
  and linkage; a `static` or anonymous-namespace one only where visible).
- **Conservative locals.** A name bound anywhere in a symbol (parameters, locals,
  template parameters, `using n::x`) is opaque in all of it and its members, and so is
  a data member in its class's methods. A class with a base that is not one captured
  class (a template parameter, `std::vector<int>`) may declare any name, so an
  unqualified name not found in the class itself stays unresolved there.

## Unexpanded macros

The grammar parses source as written. `cpp_macros.masked` rewrites common idioms in the
parser's copy only, in place, so every offset is kept and all texts and spans come from
the original bytes (an all-caps word means `[A-Z][A-Z0-9]*_[A-Z0-9_]*`):

- block comments as spaces, as in C;
- an export macro between `class`/`struct`/`union` and the name (`class FMT_API Error :`,
  `class LIB_VISIBILITY("default") Error {`);
- a line holding only an all-caps word or macro call (`FMT_BEGIN_NAMESPACE`, `Q_OBJECT`,
  `FMT_PRAGMA_GCC(push_options)`): the namespace such a macro opens is not seen, so its
  declarations are in the enclosing namespace;
- an all-caps specifier before a keyword, a constructor, a destructor or a type and a
  name (`FMT_CONSTEXPR20 inline auto`, `FMT_API format_error(`,
  `FMT_NO_UNIQUE_ADDRESS Alloc alloc_;`);
- an all-caps macro or macro call after a declarator (`~E() YAML_CPP_NOEXCEPT = default;`,
  `f() const GTEST_LOCK_EXCLUDED_(mu) {`);
- an all-caps macro call as a template parameter (`FMT_ENABLE_IF(...)`), seen as an
  unnamed `class` parameter;
- an angle path in an `#if` condition (`__has_include(<version>)`), seen as an
  identifier.

The C slice's `API T name(` recovery applies, keyword names are dropped, and
declarations recovered under a parse error are kept. Other macro-heavy code parses
partially: files with syntax errors report `partial` coverage with bounded diagnostics,
and the nesting of what follows an error may be lost.

## Not modeled

These need the preprocessor, a type checker or a build configuration, and stay
unresolved (with their name) or absent rather than guessed:

- macro expansion: what a macro's body calls or declares, conditional compilation
  (a definition in an inactive `#if` branch is still a candidate), `#undef`, macros that
  open namespaces, and macros that rewrite the calls in their arguments
  (`FMT_POSIX_CALL(dup(fd))` is `::dup(fd)` to the compiler);
- types: calls on objects and pointers, overload resolution by argument types (only
  arity), template argument deduction, SFINAE, specialization selection, dependent
  names, virtual dispatch, implicit conversions, operators and implicit constructor or
  destructor calls;
- argument-dependent lookup, `using` declarations as aliases, namespace aliases,
  friend injection, and inline namespaces;
- data members, enumerators and block-scope declarations as symbols, and block scoping;
- the build's include paths and link units (beyond the suffix rule), modules
  (`import`), and system or third-party headers that are not captured.

Structural search (`Repository.structural_search`, ast-grep) remains Python and
TypeScript only.

## Reliability

The shared bounded native query, downward-only node reads, depth limit (65,535),
UTF-8 check and bounded syntax diagnostics apply unchanged; declaration containers are
walked with an explicit stack. Resolution always reruns over the full capture, so adding,
removing or moving a header, or a `.h` changing between C and C++, re-resolves with all
other facts reused.

Facts are cached by `(cpp, source digest)` under `oxide-cpp-facts-v1`; the cache decoder
validates types, ranges, names, qualifiers and the scope tree (parents first, and only
namespaces and classes as parents), and anything else is parsed again. Derivation
components add `cpp` (`oxide-cpp-v1`) and `cpp-parser`; older indices are re-indexed.
Evidence and reproduction: [phase-6/cpp-v1](phase-6/cpp-v1/README.md).
