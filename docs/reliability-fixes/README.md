# Reliability fixes: index stack overflow, nondeterministic `oxide eval` order

Two OXIDE bugs found during the SCIP and scanner experiments, fixed before
the feature-scoped refactor. Both are behavior-preserving: index content,
retrieval output and evaluation scores are byte-identical before and after
(`raw/before_after.json`).

## A. `oxide index` stack overflow

**Reproduced on `main` (`d1333a9`)** before any change: `oxide index` on
prettier @ `6905b39e` (5,579 files) aborts — `thread '<unknown>' has
overflowed its stack`, exit 134 — with either embedder.

**Root cause** (`raw/gdb_backtrace_prettier.txt`): `languages::tags::
collect_meta`, the per-file metadata walk behind `collect_imports`,
recursed once per AST level — 3,436 frames of it on a parse worker thread
spawned by `index::parse_and_persist_changed_files` (default 2 MiB stack).
The trigger is `tests/format/flow-repo/union/yuge.js`, a single Flow union
type whose AST is 5,003 levels deep. `scanner::error_nodes`, which counts
error nodes for every `.h` file to choose C vs C++, had the same recursive
shape on the same path, and so did the C/C++ declarator-chain helpers
`function_declarator_of` and `cpp_declarator_suffix`: a chain is as long as
the source makes it (`int ****…p;` nests one declarator per `*`), and a
20,000-`*` declaration overflowed a 256 KiB stack (found by review).

**Fix:** a shared iterative pre-order walker,
`languages::walk_preorder(root, visit)`, built on a `TreeCursor`. It visits
exactly the nodes, in exactly the order, of the old "visit, then each child
in order" recursion, and `visit` returning `false` skips a node's children
as the old early `return`s did. `collect_meta`'s body became
`visit_meta(..) -> bool` (each of its 11 `return;` became `return false;`,
the trailing child loop became `true`); `error_nodes` counts through the
same walker; `function_declarator_of` is a loop and `cpp_declarator_suffix`
collects the chain, then builds the suffix from the innermost level
outward (pointer/reference levels prepend `*`/`&`, array levels append
their dimensions — the recursive definition's result). Indexed with the
parent commit's binary and the fixed one, a C++ file of pointer, reference,
array, multi-dimensional, pointer-to-array, function-pointer and `*&`
parameters yields identical qualified names, kinds and symbol ids. Raising
the thread stack size was not used: depth is unbounded by input, so any
fixed stack only moves the threshold.

**Tests** (`tests/deep_nesting.rs`): each case runs on a 256 KiB thread
with a generated source ~20,000 levels deep — a TypeScript union type (the
prettier shape), a JavaScript `require` placed *after* a deep expression
(proves the walk still reaches later nodes and records the import), a deep
C header through `language_for_source`, and 20,000-`*` C and C++
declarators. Each aborts with a stack overflow without the fix (the first
three checked on `d1333a9`, the declarator cases on the tree before the
declarator change) and passes with it. A sixth test pins the C++ overload
renderings above. The committed per-language
conformance goldens (`tests/language_conformance.rs`: symbol ids, content
hashes, imports) and `full_incremental_parity` pass unchanged.

**Before/after** (`scripts/before_after.py`, final binary, hashed embedder,
alternating cold runs per binary — 3, or 10 for the three re-timed repos;
the baseline is `d1333a9` built in a clean worktree):

| checkout | baseline | fixed | index content + `context`/`search` output |
| --- | --- | --- | --- |
| prettier (5,579 files) | **abort ×3** | ok ×3 — 10.8 s, 64 MB | — (baseline has none) |
| clap, flipt, requests, dayjs, zstd, darkreader, pylint | ok | ok | **identical** (symbols, relations, `context`, hybrid `search` digests) |

Minimum cold-index wall, fixed vs baseline: clap +0.8%, darkreader +3.4%,
dayjs −2.4%, flipt −4.0%, pylint +0.7%, requests +0.9%, zstd −3.3%;
minimum peak RSS equal to ±1 MB on every repository. Minimums, not
medians: the host was intermittently loaded during the re-timing runs and
both arms' medians swung by 2–3× (e.g. flipt baseline 4.4–12.3 s), which
`raw/before_after_retime.json` records in full. An earlier run of the same
harness, before the declarator change, is kept in `raw/discarded/`.

## B. Nondeterministic `oxide eval` order

**Confirmed:** `eval::BenchConfig.repos` was a `std::collections::HashMap`,
and `run_benchmark` iterated its keys (`src/eval.rs:14,113` at `d1333a9`),
so the per-repo row order — and the f32 summation order behind the
aggregate means — followed each process's random hash seed. Two runs of the
same binary printed the `py` and `ts` blocks in different orders.

**Fix:** `repos` is a `BTreeMap`: repos in name order, queries in config
order within each. No score or metric logic changed.

**Tests** (`tests/eval_determinism.rs`): `run_benchmark` four times in one
process → byte-identical serialized reports, rows grouped in repo-name
order; `oxide eval` in six separate processes → byte-identical stdout, rows
in repo-name order. On `d1333a9` both fail — the in-process one on its run,
the CLI one 3 of 3 runs. Both are probabilistic against the bug (each map
draws its own hash keys): a buggy build passes the in-process test by chance
with probability about 2⁻⁴ and the CLI test about 2⁻⁶. **Scores unchanged:** the baseline and fixed `oxide eval` outputs
contain the same rows as a sorted set (`raw/before_after.json` → `eval`);
`tests/benchmark_gate.rs` passes.

## Reproducing

```bash
# baseline: git worktree add <dir> d1333a9 && (cd <dir> && cargo build --release -j 2)
python3 docs/reliability-fixes/scripts/before_after.py <baseline oxide> target/release/oxide \
    <work dir> docs/reliability-fixes/raw/before_after.json 3 <prettier checkout> <other checkouts...>
cargo test -j 2 --test deep_nesting --test eval_determinism
```

Each measured run's full stderr (`measure.py` writes one file per run) is
kept in `raw/stderr.tar.gz`; the `*.measure.jsonl` records carry its tail.

Discarded: `raw/discarded/1-relative-binary-path.measure.jsonl` — the fixed
binary was passed as a relative path, `measure.py` runs inside each copy, so
it never started and the harness re-read the previous (baseline) record;
the script now resolves binaries and checks each record's label.
