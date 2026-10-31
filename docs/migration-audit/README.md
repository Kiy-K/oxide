# Python migration audit: fixes and evidence (2026-10-15)

This pass follows up the audit of the Python engine (ADR-0011) on `rewrite/v2`.
The `M`/`L` IDs are that audit's labels. Every claim below is marked
**measured** (a test or probe ran) or **inferred**.

## Fixed and regression-tested

| ID | Defect | Fix | Regression |
| --- | --- | --- | --- |
| M1 | A damaged `graph.lbug` (random bytes, truncated) raised raw `RuntimeError` from every call and was never rebuilt. A malformed `MANIFEST.json` raised `KeyError`, and a corrupt `repo.json` raised `JSONDecodeError` | `View` validates the manifest's shape before any native call. It maps native open and read failures (`RuntimeError`, measured as the only class raised) to `StoreCorrupt` and closes what it opened. It also compares the stored entity count with the manifest, because an empty file opens as an empty database (measured). `index()` rebuilds; `repo.json` damage fails closed at open and is never rewritten | `test_recovery.py` (damage runs in a subprocess), `test_cli.py::test_a_damaged_store_is_a_stable_error_then_rebuilt` |
| M2 | A `Repository` dropped without `close()` held its lock until the process exited | `weakref.finalize(store, os.close, fd)` holds only the descriptor, never the store; `close()` runs it once | `test_lifecycle.py` (GC releases the lock to another process, no descriptor leak over 30 cycles, exception paths, idempotent close) |
| L1 | A crash between the two renames of a same-name rebuild left `CURRENT` dangling and needed manual deletion | `_recover` moves `<g>.replaced` back when `<g>` is absent, and `index()` then rebuilds. A failure in that window (not a crash) is undone in place. Any other dangling `CURRENT` still fails closed, and nothing is deleted | `test_a_crash_between_the_rebuild_renames_is_undone_on_open` (child killed with `os._exit` at the second rename) |
| L2 | A deleted retained source file was reported `unchanged` forever | `is_current` verifies every retained source file's existence and digest | `test_missing_retained_source_is_never_reported_unchanged` |
| L3 | `--limit -5` and `--budget -1` crashed with tracebacks and exit 1 | `InvalidArgument` (`invalid_argument`, also a `ValueError`) validates limit, budget, query, hints and selection before any query is built, with bounds `MAX_LIMIT = 1000` and `MAX_BUDGET = 2**20` | `test_retrieval.py::test_malformed_arguments_*`, `test_cli.py::test_expected_errors_never_print_a_traceback` |
| L4 | A data directory inside the repository indexed itself and never reached `unchanged` | Capture excludes the data directory and its `repos/` by `(st_dev, st_ino)`, so nested paths and symlink aliases are excluded too, with no string-prefix matching | `test_capture.py::test_a_data_location_inside_the_repository_is_never_captured` (`state`, `deep/nested/state`, a symlink alias) |
| L5 | Store and extension paths containing `'` or `\` were refused, undocumented | Escaped instead (`\` → `\\`, `'` → `\'`). The probe below shows byte-exact round trips. Only paths that are not UTF-8 are refused (`invalid_path`) | `test_native.py::test_paths_with_quotes_and_backslashes_work_end_to_end` (the FTS extension itself loaded from a quoted path) |
| Unverified concern | The derivation version check ran after the native database was opened | The component comparison moved into `View`, before the open | `test_derivation_compatibility_is_checked_before_the_database_opens` (a garbage `graph.lbug` under a foreign `ladybug` component gives `incompatible_index`, not `store_corrupt`) |

Run against the pre-fix source, 17 of the 23 `test_recovery.py` cases fail and
all 23 pass after the fix (measured). The 6 that pass on both lock in behavior
that was already correct: a non-JSON manifest, a foreign `root`, garbage
`CURRENT` text, and crashes before and after `CURRENT` moves.

## Investigated and disproven

- **The Python/Rust asyncio entity and relation differences are not derivation
  differences (measured).** `parity/` holds a dump of the `f79808a` kernel
  derivation (`oxide_kernel/crates/kernel` plus the runtime's tree-sitter
  extraction, compiled without LadybugDB) and the same dump from this engine.
  The Rust dump reads exactly the file list the Python capture produced. Their
  outputs are byte-identical, line order included, with every entity's key,
  kind, name, signature, span and test flag, and every relation's kind,
  endpoints, resolution, basis, name and evidence span:

  | Corpus (copy of Fedora `python3-libs-3.14.8-1.fc44`) | Files | Entities / relations (both engines) |
  | --- | --- | --- |
  | `fixtures/py_repo` | 11 | 65 / 194 |
  | `asyncio/` without `__pycache__` | 35 | 1,179 / 5,139 |
  | `asyncio/` with `__pycache__` | 140 | 1,284 / 5,244 |
  | `email/` without `__pycache__` | 30 | 727 / 3,615 |

  So the ADR-0011 rows compared different snapshots. Email's Rust record (814 /
  3,702) is exactly the 30 sources plus 87 `.pyc` files (measured: 87 `.pyc`
  files, and each file adds one entity and one `contains` relation). The asyncio
  Rust record (1,283 / 5,224, 140 files) differs from today's 140-file tree by
  -1 entity and -20 relations. That fits a change in the `.py` sources:
  `python3-libs` was updated to 3.14.8 on 2026-10-14, after the Rust records
  (inferred; the old package isn't available to re-run). The tree-sitter
  runtime differs (Rust 0.27.0, Python 0.26.0, same grammar 0.25.0) with no
  observed effect on these corpora.
- **FTS query injection (measured).** Search text reaches `QUERY_FTS_INDEX` only
  as OXIDE terms, checked with `[a-z0-9]+( [a-z0-9]+)*` before the literal is
  built. A subprocess ran 325 hostile and repeated queries (quotes,
  backslashes, Cypher fragments, parameters, NUL, non-ASCII, 10,000-character
  inputs) with no crash and identical results per query
  (`test_native.py`). The parameterized-statement segfault workaround is
  unchanged.

## Remaining limitations

- **Capture and derivation memory (measured, then extrapolated).** Capture
  holds every captured file in memory until `index()` returns, and the bound is
  1 GiB in scope at 1 MiB per file. With `probes/capture_memory.py` (non-Python
  synthetic files under `MemoryMax=3G`), peak RSS was 91 MiB for 64 MiB of
  input and 282 MiB for 256 MiB (about 1.0× plus 30 MiB), and a full `index()`
  of 256 MiB peaked at 384 MiB. That is linear and bounded, with no unbounded
  growth found. Derivation adds per-entity memory: the 14 MiB stdlib (16,512
  entities) peaked at 239 MiB. A 1 GiB Python tree would therefore need several
  GiB (inferred). Streaming capture or derivation would be an architecture
  change, so it is not made here.
- **Store damage with no crash record still needs the user.** A malformed
  `repo.json`, a lost `repo.json`, an unreadable `CURRENT`, or a `CURRENT` naming
  an absent generation fails closed with the store directory in the message.
  OXIDE can't prove which data is recoverable, so it deletes nothing.
- **Platform.** The FTS extension is pinned for Linux x86_64. Atomic publication,
  locking, crash injection and descriptor counting (`/proc/self/fd`) are
  verified on Linux only. macOS and Windows were not run.
- **Power-loss durability.** Publication fsyncs files and directories, but only
  process crashes were injected, not power loss (unverified).
- **Thread safety.** A `Repository` and the native binding are single-threaded.
  Callers serialize, and nothing new was added.

## Request-scoped open cost (MCP, 2026-10-16)

`oxide mcp` opens the repository on every tool call instead of holding it, so the
CLI can index between calls. Measured cost (one process, 20 calls after one
warm-up, median/max ms, `CPUQuota=200%`, `MemoryMax=3G`, private data, Linux
x86_64; fixture `fixtures/py_repo`, and the `.py` files of the CPython 3.14
stdlib: 1,422 files, 36,376 entities):

| Operation | fixture | stdlib |
| --- | --- | --- |
| open + view + close | 9.2 / 10.3 | 13.2 / 17.0 |
| search, handle held | 1.2 / 1.9 | 15.8 / 24.5 |
| search, open per call | 12.3 / 13.4 | 37.9 / 48.9 |
| context (2048 units), handle held | 5.8 / 6.4 | 133.6 / 143.9 |
| context, open per call | 19.3 / 21.0 | 158.9 / 174.6 |

The open costs about 10 ms. On the stdlib a per-call search also loses about
10 ms more to a cold buffer pool (inferred from the gap). That is small next to
an agent's tool-call round trip, so no long-lived handle or lock was introduced.

## Method

- **Parity.** Extract `f79808a:oxide_kernel` to a scratch directory, place
  `parity/Cargo.toml` and `parity/main.rs` beside it as a crate whose paths point
  at `../rust/oxide_kernel/...`, and build it with `cargo build --release
  --offline -j 2` under `CPUQuota=200%`/`MemoryMax=3G` (18 s). Then run
  `uv run python parity/pydump.py CORPUS files.list > py.tsv` and
  `paritydump CORPUS < files.list > rs.tsv`, and compare them.
- **Benchmark.** `scripts/bench.py` (definitions in its docstring) ran under
  `systemd-run --user --scope -p CPUQuota=200% -p MemoryMax=3G`, with 5
  iterations for the fixture, asyncio and email and 1 for the stdlib. The JSON
  reports in `bench/` record corpus snapshot IDs, the environment and
  min/median/max. The corpora under `/tmp` were copies of the Fedora stdlib
  without `__pycache__`.
- **Literal paths.** `probes/literal_paths.py` creates 12 directories whose
  names contain `'`, `\`, `\u`, a raw newline, a tab, `"`, `;)`, `$` and
  non-ASCII characters. It then checks `COPY` row counts, `RETURN` literal
  round trips and `LOAD EXTENSION`; all 12 pass.
