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

The pinned baseline corpora hold no C++, and their baselines move by identity only
([../baseline-v17](../baseline-v17/README.md), [../typescript-v14](../typescript-v14/README.md)).

## ContextBench diagnostic (measured)

`scripts/eval_cpp.py` selected, before any scoring, the first three C++ rows per
repository (by instance id) whose gold names a C/C++ source: 12 rows over Catch2, fmt,
nlohmann/json and simdjson (`cpp-rows.json`, SHA-256
`1627c229fc27e7315927a5716550c84982362a86e6e19918d7b17a368f6f4e9b`). Corpora are a
`git archive` projection of C/C++ sources and build files at each base commit (65–376
files). Both arms used the same rows, corpora, gold audit and scorer, with module lines
projected through tree-sitter-cpp independently of OXIDE. Before ran the previous engine
(18ffaa5, C slice only, `.h` parsed as C) with this checkout's scorer and environment;
after ran this checkout. Gold audit: 16 exact, 59 path-only, 4 unresolved (excluded);
Catch2-1422 has no verified span.

| Verified macro metric (11 tasks, 75 spans, 6,021 lines) | Before | After |
| --- | ---: | ---: |
| Entry recall@20 | 0.267796 | 0.241872 |
| Routed coverage | 0.552564 | 0.484739 |
| Complete span recall@1,024 | 0.006993 | 0 |
| Complete span recall@4,096 | 0.006993 | 0.039960 |
| Line recall@4,096 | 0.034005 | 0.040730 |
| File recall@4,096 | 0.337413 | 0.406177 |

The C++ slice indexes 2–10× more entities (Catch2 587 → 6,560, nlohmann/json 678 →
6,511). Entry recall@20 rises on all three fmt tasks (0.43/0.09/0.12 → 0.57/0.45/0.50)
and Catch2-1448 (0.50 → 0.75), and falls on nlohmann/json-1221, json-1301 and
simdjson-1695.

json-1221 traced (measured, `repo.search` on the task query, both engines): its one
gold span is the whole `include/nlohmann/detail/exceptions.hpp`. The query is mostly
the pull-request template ("pull request checklist", "test suite compiles", "code
coverage"), with one specific term, "error 305". Before, the C slice gave `.hpp` files
only a file entity, so the file entity ranked 15th, inside the scorer's 20 entries
(`retrieval.LEXICAL_LIMIT`). Now the file holds 21 symbols plus its file entity,
which ranks below the first 1,000 entries; its best symbol, `other_error.create`, ranks
44th. The top 20 went from 5 to 17 vendored third-party entities (`test/thirdparty/catch`,
`Fuzzer`, `benchmarks/thirdparty`), which the C++ slice now indexes as symbols that
match the template words. The loss is boilerplate-query displacement by newly indexed
vendored code, not a C++ parsing fault; the gold file is parsed correctly. Nothing was
changed. Neither direction is meaningful at n=11; the results are retained as
observed and nothing was tuned. Median index time was 1.23 s before and 3.88 s after;
peak RSS 228 and 280 MiB (200% CPU / 3 GB cap). This is a small diagnostic, not a gate.
Raw scores, audits, corpus identities and timings are in `context-before/` and
`context-after/`.

## Verification (Linux, measured)

Logs are in [verification/](verification/):
- `mise-verify.log`: ruff clean, mypy `--strict` clean (78 files), pytest 425 passed,
  7 skipped, `test_baseline.py` against baseline-v17 included.
- `distribution-wheel.log`, `distribution-sdist.log`: both artifacts install, index the
  fixtures (82 files, 403 entities) and pass `scripts/smoke_dist.py` (`exit=0`). The first
  run failed: the copied C++ fixture's two `Store::must_get` entities ranked above Rust's
  `<impl Store>.must_get`, which the Rust check required at rank 1; it now requires the
  Rust entity among the results (extraction unchanged).
- `contextbench-ts.log`: `mise run contextbench-ts` against typescript-v14, 17 passed
  (14 min, 200% CPU / 3 GB cap).

## Reproduce

```
uv run python docs/phase-6/cpp-v1/oracle/oracle.py $YAML docs/phase-6/cpp-v1/oracle/yaml-cpp-oracle.json 'src/*.cpp' 'src/contrib/*.cpp' -- -std=c++17 -include cstdint -Iinclude -Isrc
uv run python docs/phase-6/cpp-v1/oracle/oracle.py $FMT docs/phase-6/cpp-v1/oracle/fmt-oracle.json 'src/format.cc' 'src/os.cc' 'test/*-test.cc' 'test/gtest-extra.cc' 'test/util.cc' 'test/test-main.cc' 'test/gtest/gmock-gtest-all.cc' -- -std=c++17 -Iinclude -Itest -Itest/gtest -DGTEST_HAS_STD_WSTRING=1 -include cstdint
```

ContextBench diagnostic (`$E` outside `/tmp`; the before arm puts a worktree of 18ffaa5
first on `PYTHONPATH`):

```
uv run --locked --with pyarrow python scripts/eval_cpp.py rows $PARQUET docs/phase-6/cpp-v1/cpp-rows.json
TMPDIR=$E/tmp uv run --locked python scripts/eval_cpp.py fetch docs/phase-6/cpp-v1/cpp-rows.json $E/corpora
uv run --locked python scripts/eval_cpp.py context docs/phase-6/cpp-v1/cpp-rows.json $E/corpora docs/phase-6/cpp-v1/context-after
PYTHONPATH=$BEFORE/src .venv/bin/python scripts/eval_cpp.py context docs/phase-6/cpp-v1/cpp-rows.json $E/corpora docs/phase-6/cpp-v1/context-before
```

`*-oracle.json` lists every disagreement (with "agree for some"), every miss (`missed`)
and every resolved edge in a unit without a GCC call of its name (`unseen`);
`*-oracle.log` is the printed tally.
