# c-v1: C language slice evidence

The C slice (`oxide-c-v1`, [../../c.md](../../c.md)) is judged against GCC, which binds
every call the way the build does. `oracle/oracle.py` compiles each translation unit
with `gcc -O0 -fcallgraph-info` and reads every real call: its site, its callee, and
the file that defines the callee (a static's own node, else where `nm` reports an
external text definition). Each oxide `calls` edge of those units at such a site is
judged. A resolved edge agrees only when its target is a function definition of the
callee's name in the defining file. GCC (gcc 16.2.1) reports a macro's expansion at the
macro's use site, so edges to macros, which is where oxide stops, are counted and not
judged.

Corpora (sources outside the repository, `~/Projects/oxide-eval-data/c-v1`):
- Lua 5.4.7 (`lua-5.4.7.tar.gz`, sha256 `9fbf5e28…1e30`), `src/` with `-DLUA_USE_LINUX`;
- Redis 7.2.5 (`redis-7.2.5.tar.gz`, sha256 `5981179706…423d`): `deps/hiredis` units,
  resolved inside the whole Redis capture (692 C files), so unrelated same-named
  definitions elsewhere in Redis compete.

## Results (measured)

| Tally | Lua | hiredis in Redis |
| --- | ---: | ---: |
| gcc calls | 4,335 | 1,651 |
| resolved, agree | **3,036** | **975** |
| resolved, disagree | **0** | **0** |
| resolved to a macro, not judged | 3,334 | 533 |
| ambiguous to a macro, not judged | 334 | 0 |
| ambiguous, gcc binds a captured definition | 8 | 0 |
| unresolved, gcc binds a captured definition | 0 | 12 |
| unresolved, gcc external (libc) | 376 | 185 |
| resolved, no gcc call in this build | 145 | 241 |

Every disagreement found while building the slice traced to the oracle, not oxide: a
macro's expansion reported at its use site, a prototype redeclared in a `.c` file read
as a definition, and statics in headers named after their unit. Each was fixed in
`oracle.py` before these numbers.

The other categories, characterized:
- The 8 Lua ambiguous sites call functions defined once per `#if` branch (`rotl`,
  `nextrand`, `lsys_load`, …): conditional compilation is not evaluated (docs/c.md).
- The 12 hiredis unresolved sites invoke a macro defined inside a function body
  (`INTEGER_WIDTH_TEST` in `test.c`), which is opaque; GCC reports `hi_free` from its
  expansion.
- "No gcc call in this build" are calls in inactive `#if` blocks (`HI_SDS_TEST_MAIN`,
  `HIREDIS_TEST_ASYNC`, `_WIN32`), compiled-out `lua_assert`s, and calls nested in
  macro arguments that GCC reports at another site.
- No call that GCC binds to a captured definition is left unresolved by oxide except
  the 12 above.

Coverage and cost (measured, `derive` only, 200% CPU cap): Lua 61 C files, 9 complete
and 52 partial (API macros such as `LUA_API` are syntax errors to the grammar; their
declarations are recovered), 0.31 s. Redis 692 C files (10.0 MB), 389 complete and 303
partial, 3.2 s, 45,711 resolved calls.

Not measured: retrieval or context quality on C tasks. The pinned ContextBench corpora
hold no C, and their baselines move by identity only ([../baseline-v16](../baseline-v16/README.md),
[../typescript-v13](../typescript-v13/README.md)).

## Verification (Linux, measured)

Logs are in [verification/](verification/):
- `mise-verify.log`: ruff clean, mypy `--strict` clean (73 files), pytest 413 passed,
  7 skipped, `test_baseline.py` against baseline-v16 included.
- `contextbench-ts.log`: the TypeScript gate (`OXIDE_CONTEXTBENCH_TS=1`), 17 passed, against
  typescript-v13.
- `distribution-wheel.log`, `distribution-sdist.log`: `uv build` artifacts, each in its
  own fresh venv with the locked runtime requirements, `oxide prepare`, then
  `python -I scripts/smoke_dist.py fixtures`, which now covers `fixtures/c_repo`
  (`store_must_get` first, `store_open` context). Both: **smoke: OK**, 74 files,
  359 entities.

Windows and macOS runtime checks remain pending hosted CI.

## Reproduce

```
uv run python docs/phase-6/c-v1/oracle/oracle.py $LUA $LUA/src docs/phase-6/c-v1/oracle/lua-oracle.json -DLUA_USE_LINUX
uv run python docs/phase-6/c-v1/oracle/oracle.py $REDIS $REDIS/deps/hiredis docs/phase-6/c-v1/oracle/hiredis-oracle.json
```

`*-oracle.json` lists every disagreement, every miss (`missed`) and every resolved
edge without a gcc call (`unseen`); `*-oracle.log` is the printed tally.
