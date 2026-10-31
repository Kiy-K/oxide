# C source support

`oxide-c-v1` adds C as a language slice of the existing engine: `core/languages/c.py`
extracts syntax facts with `tree-sitter-c==0.24.2`, and `core/languages/c_resolve.py`
resolves them over the whole capture. No compiler, preprocessor, build system,
`compile_commands.json` or repository code runs during indexing or querying. The ordinary
Python API, CLI, retrieval, graph, router and context packer serve these files unchanged,
and MCP remains exactly one tool, `oxide_search`, with `search` and `context` modes.

## Extraction and identity

`.c` and `.h` files report `FileInfo.language == "c"`, except a `.h` file with C++ syntax,
which is C++ ([cpp.md](cpp.md)). File-level declarations become symbols,
including those in every branch of an `#if`/`#ifdef`/`#else`:

| C declaration | Kind | Key segment |
| --- | --- | --- |
| a function definition, or a prototype `T f(...);` | `function` | `f` |
| `struct S { … }`, `union U { … }`, `enum E { … }` with a body | `struct` / `union` / `enum` | the tag |
| `typedef … T;` | `type` | `T` |
| each declarator of another declaration (`int a, *b;`, `extern int c;`, `int (*fp)(int);`) | `variable` | the name |
| `#define N …`, `#define F(x) …` | `macro` | the name |

Keys are `symbol:<path>#<segment>`; every symbol's physical parent is its file. A
prototype and its definition are separate symbols (ADR-0010), and so are a tag and a
same-named typedef: equal segments in one file (a prototype before its definition,
`#ifdef` branches, `typedef struct S {…} S;`) take `~n` in scope order, a tag before
the declaration that holds it. Declarations inside function bodies, struct members,
enumerators, anonymous tags and forward declarations (`struct S;`) are not symbols.

A function spans its definition or prototype, a tag its specifier, and a typedef,
variable or prototype its whole declaration (several declarators share it). A macro
spans its `#define` line(s). Signatures are whitespace-collapsed headers with bodies
cut out (`Store *store_open(enum Mode mode)`, `typedef struct Store Store;`,
`struct Store`); a variable's is its first line, a macro's its first line without the
continuation backslash. Doc comments are not part of a symbol's range.

C has no modules: there are no `module:` entities or `defines` edges, and `#include`
edges name files. There is no test convention either, so no symbol is a test and
there are no `tested_by` edges.

## Resolution

Everything below follows C's translation-unit, linkage and name-space rules without a
preprocessor or a type checker; nothing that needs one is inferred.

- **Includes** (`imports`, file to file). `#include "p"` resolves beside the including
  file, else, standing in for an unknown `-I` path, to the one captured file whose path
  ends with `p`; several are ambiguous. `#include <p>` resolves only by that suffix and
  only when `p` has a directory part, so `<stdio.h>` never binds a project file.
  System headers and computed includes (`#include NAME`) stay unresolved with their
  path as name.
- **Visibility.** A file sees its own declarations and those of every captured file
  its resolved includes reach, transitively.
- **Macros first.** A call or type name that a visible macro defines resolves to the
  macro, as the preprocessor would expand it.
- **Calls** through an identifier (`f(x)`) resolve by linkage: a visible definition
  (the file's own or a header's, `static` or not), else every external definition in
  the capture, else a visible prototype. Another file's `static` function is never a
  target. Calls through function pointers, struct fields and expressions are
  unresolved.
- **References** cover typedef names in any type position (declarations, parameters,
  casts, `sizeof`) and `struct`/`union`/`enum` tags without a body, each resolved in its
  own name space among visible declarations. A prototype references its definitions
  (the visible ones for a `static` prototype, the external ones otherwise).
- **Conservative locals.** A name bound anywhere in a declaration (parameters, local
  variables, local typedefs, locally defined tags) is opaque throughout it, so
  shadowing never produces a wrong edge.

Same-named declarations in several branches or files (`#ifdef _WIN32`, `posix.c` /
`windows.c`, several programs each defining a helper) make a use ambiguous, listing
every candidate: conditional compilation and the build's link units are not
evaluated.

## Unexpanded macros

The grammar parses source as written. Common macro idioms are handled:

- `API int f(void)`: an unknown leading macro is grammar recovery, and the name is the
  identifier before the parameters, whichever way recovery places it. A declarator
  that would name a keyword (`API int (f)(void);`) is dropped rather than misnamed.
- A block comment inside a multi-line macro body ends the body in the grammar, so the
  parser sees every block comment as spaces (byte offsets and line breaks kept); all
  texts and spans still come from the original bytes.
- Declarations recovered under a top-level parse error are kept, as in the other
  slices.

Other macro-heavy code parses partially: files with syntax errors report `partial`
coverage with bounded diagnostics, and keep every declaration recovery preserves.

## Not modeled

These need the preprocessor, a type checker or a build configuration, and stay
unresolved (with their name) or absent rather than guessed:

- macro expansion: what a macro's body calls, conditional compilation, `#undef`,
  include guards and macro-generated declarations;
- the build's include paths and link units (beyond the suffix rule above),
  `compile_commands.json`, and system or third-party headers that are not captured;
- uses of variables, enumerators and functions as values (`return ERR_X`,
  `cb = handler`), member access, and calls through pointers;
- struct members, enumerators and block-scope declarations as symbols, and block
  scoping (a shadowed name is opaque in the whole declaration);
- K&R parameter declarations, and non-UTF-8 sources (unsupported file evidence).

Structural search (`Repository.structural_search`, ast-grep) remains Python and
TypeScript only.

## Reliability

The shared bounded native query, downward-only node reads, depth limit (65,535),
UTF-8 check and bounded syntax diagnostics apply unchanged. Resolution always reruns
over the full capture, so adding, removing or moving a header re-resolves includes with
all facts reused.

Facts are cached by `(c, source digest)` under `oxide-c-facts-v1`; the cache decoder
validates types, ranges, identifier names and the scope list, and anything else is
parsed again. Derivation components add `c` (`oxide-c-v1`) and `c-parser`; older
indices are re-indexed. Evidence and reproduction: [phase-6/c-v1](phase-6/c-v1/README.md).
