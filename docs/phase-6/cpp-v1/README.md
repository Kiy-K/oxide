# cpp-v1: C++ language slice evidence

The C++ slice (`oxide-cpp-v1`, [../../cpp.md](../../cpp.md)) is judged against G++, which
binds every direct call the way the build does. `oracle/oracle.py` compiles each
translation unit with `g++ -O0 -fcallgraph-info` and reads every direct call: its file
and line, the callee's mangled name and the definition GCC kept (a callee's definition is
where a unit that defines it, per `nm`, places it). G++'s call column is not the call's
own (calls in another call's arguments carry that call's `(`, a temporary its `)`), so
each oxide `calls` edge is matched to the GCC callees of the same unqualified name
(`c++filt -p`) on the line of its `(`. A resolved edge agrees when its target is a
function or method whose source holds such a callee's definition line, for every such
callee a compiled unit defines (a template's call site binds once per instantiation;
"agree for some" is reported apart). A resolved prototype whose callee no compiled unit
defines there is checked against the declaration GCC kept. Edges to macros, which GCC
sees expanded, are counted and not judged. Measured with g++ 16.2.1.

Corpora (sources outside the repository, `~/Projects/oxide-eval-data/cpp-v1`), each
captured whole (tests, bundled gtest/gmock and docs included):
- yaml-cpp 0.8.0 (`yaml-cpp.tgz`, sha256 `fbe74bbd…3a16`): the 31 units of `src/` and
  `src/contrib/`, `-std=c++17 -include cstdint -Iinclude -Isrc`;
- fmt 11.1.4 (`fmt.tgz`, sha256 `ac366b7b…1e1e`): `src/format.cc`, `src/os.cc`, every
  `test/*-test.cc`, the test utilities and the bundled `gmock-gtest-all.cc`,
  `-std=c++17 -Iinclude -Itest -Itest/gtest -DGTEST_HAS_STD_WSTRING=1 -include cstdint`
  (28 units; `format-impl-test.cc`, `header-only-test.cc` and `module-test.cc` do not
  compile with these flags and are skipped).

## Results (measured)

| Tally | yaml-cpp | fmt |
| --- | ---: | ---: |
| G++ calls to a captured definition (in units / in headers) | 1,491 / 726 | 36,748 / 45,708 |
| resolved, agree | **640** | **2,244** |
| resolved, disagree | **0** | **7** |
| resolved, agree for some instantiations or overloads on the line | 8 | 1 |
| resolved, callee defined in no compiled unit | 0 | 15 |
| resolved to a macro / ambiguous to a macro, not judged | 7,950 / 81 | 2,883 / 420 |
| ambiguous, GCC binds a captured definition | 66 | 611 |
| unresolved, GCC binds a captured definition | 0 | 2,083 |
| unresolved, GCC binds an external one / one no unit compiles | 15 / 1 | 281 / 346 |
| no GCC call of that name on the line (resolved, ambiguous, unresolved) | 7,941 / 1,229 / 17,650 | 1,168 / 338 / 3,551 |

Precision on judged resolved edges is 1.0 on yaml-cpp and 0.997 on fmt. The 7 fmt
disagreements, characterized:
- 3 are `FMT_POSIX_CALL(dup(fd))`-style calls inside a class with a `dup` member: the
  macro makes them `::dup` (or the test build's `test::dup`); macro expansion is not
  modeled;
- 1 is argument-dependent lookup (`format_as(value)` binds a function in the argument
  type's namespace), not modeled;
- 1 is in the gtest amalgamation, where parse errors lost a namespace's nesting, so the
  call bound the header's prototype of the same function under another qualified name;
- 2 are templates: `formatter<string_view>::format(...)` drops its template argument, so
  it binds the enclosing `formatter<throws_on_move>` specialization's own `format`; and a
  `scan_data<…>()` constructor bound to another constructor of the same arity.

The "agree for some" cases are lines that call two overloads of one name (`RegEx(' ') |
RegEx("\r\n")`, gtest's `Message` copy and default constructors) or a template
instantiated so that its call binds differently; the oracle cannot separate them by line.
Recall is low by design: most G++ calls to captured definitions go through objects
(`x.f()`, `p->f()`) and need types, which the slice does not record (docs/cpp.md); "no
GCC call" edges sit in code no compiled unit instantiates or includes (templates, test
helpers, bundled gtest), or in macro arguments G++ reports elsewhere.

Every disagreement found while building the slice was traced and fixed or explained
before these numbers. Fixed in the slice: C's "every external definition" linkage rule
bound fmt's own `fprintf` template to libc calls in units that never declare it (C++
calls now need a visible declaration); default arguments on a prototype made its
out-of-line definition look too strict (arity is shared by declarations of the same
parameter count); and the unexpanded-macro idioms in docs/cpp.md, which had folded most
of `fmt/format.h` into one parse error. Fixed in the oracle: G++'s columns, unit-prefixed
internal symbols, and callees defined in units first left uncompiled.

The C slice's GCC oracle is unchanged by this slice: Lua 3,036 agree / 0 disagree,
hiredis in Redis 975 / 0 ([../c-v1](../c-v1/README.md)). Of Redis's 274 `.h` files, the two
Qt adapter headers (`deps/hiredis/adapters/qt.h`, `examples/example-qt.h`) are now C++;
Lua's 27 stay C.

Coverage and cost (measured, `derive` only, 200% CPU cap): fmt 69 C++ files, 44
complete and 25 partial, 1.3 s; yaml-cpp 246 C++ files (bundled gtest included), 183
complete and 63 partial, 2.1 s.

Not measured: retrieval or context quality on C++ tasks. ContextBench holds 60 C++
rows (fmt, nlohmann/json, simdjson, Catch2, ponyc, …), but no diagnostic was run; the
pinned baseline corpora hold no C++, and their baselines move by identity only
([../baseline-v17](../baseline-v17/README.md), [../typescript-v14](../typescript-v14/README.md)).

## Verification (Linux, measured)

Logs are in [verification/](verification/):
- `mise-verify.log`: ruff clean, mypy `--strict` clean (78 files), pytest 425 passed,
  7 skipped, `test_baseline.py` against baseline-v17 included.
- `distribution-wheel.log`, `distribution-sdist.log`: **FAILED**, open. Both artifacts
  install and index the fixtures (82 files, 403 entities), then `scripts/smoke_dist.py`
  stops at `installed Rust extraction unavailable`; not yet investigated. The TypeScript
  gate was not rerun for this slice.

## Reproduce

```
uv run python docs/phase-6/cpp-v1/oracle/oracle.py $YAML docs/phase-6/cpp-v1/oracle/yaml-cpp-oracle.json 'src/*.cpp' 'src/contrib/*.cpp' -- -std=c++17 -include cstdint -Iinclude -Isrc
uv run python docs/phase-6/cpp-v1/oracle/oracle.py $FMT docs/phase-6/cpp-v1/oracle/fmt-oracle.json 'src/format.cc' 'src/os.cc' 'test/*-test.cc' 'test/gtest-extra.cc' 'test/util.cc' 'test/test-main.cc' 'test/gtest/gmock-gtest-all.cc' -- -std=c++17 -Iinclude -Itest -Itest/gtest -DGTEST_HAS_STD_WSTRING=1 -include cstdint
```

`*-oracle.json` lists every disagreement (with "agree for some"), every miss (`missed`)
and every resolved edge in a unit without a GCC call of its name (`unseen`);
`*-oracle.log` is the printed tally.
