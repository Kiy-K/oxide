# Structural relations in the parse workers — evaluation (#29)

Concurrency experiment, benchmark-first: can `compute_file_relations`
(calls + bases) move from the serial store loop into the parallel parse
workers, reusing the tree the worker already builds for extraction, without
changing any output? Parents: #27, #9. Follows #24 (`docs/one-parse-eval/`).

**Disposition: ACCEPT (pending approval)** — see [§10](#10-disposition).

## 1. Baseline

| | |
|---|---|
| OXIDE `main` | `b2555151ef741457f866f550d4c65898691d41df` (#24 ACCEPT; #28 Go bugs untouched) |
| tree-sitter / tree-sitter-tags | 0.27.0 / 0.27.0 (unchanged) |
| machine | i7-13620H, Linux 7.2.5, rustc 1.98.0; benchmarks pinned to P-cores 0,2,4,6 |
| corpora | #24's eight pinned repos (flask, darkreader, axios, tokio, gin, gson, zstd, fmt) + `fixtures/conformance`, `fixtures/py_repo`, `fixtures/ts_repo` |

Method as in #24: a research probe (`harness/parse-probe`, derived from
#24's, standalone crate outside OXIDE's workspace and CI) links OXIDE
unmodified and wraps `ts_parser_parse_with_options` at link time to count
and time every Tree-sitter parse. New for #29: each parse is also tagged by
thread (`[worker]` = parse-pool thread, `[main]` = calling thread / store
loop), and every run records per-stage wall and process CPU, each parse
worker's file count, finish time and thread CPU, and the gap between the
parse and store stages (reference resolution, on the main thread). The same
probe source builds against both sides; only its `dump` mode is
feature-gated, because the worker seam's API is what the challenger changes.

## 2. Parse counts (Phase 1 / result)

Through the real pipeline, one fresh index per language built from the
1,822 tracked text files of the eight corpora + `fixtures/conformance`, of
which the scanner indexes **1,787** (JavaScript 184 → 175, Markdown
110 → 84; the ratios below are per *reparsed* file;
`results/langseam.*.json`), and per scenario on every repo
(`results/pipeline_attrib.*.jsonl`). The 1,822-file extraction dump (§5)
is separate evidence covering every file:

| | baseline | challenger |
|---|--:|--:|
| Python, TS, TSX, JS, Rust, Go, Java, Ruby, PHP — parses/file | **3.00** | **2.00** |
| C (175 files, incl. `.h`) / C++ (104) — parses/file | 3.75 / 3.77 | 2.75 / 2.77 |
| `.h` edit (zstd, fmt) | 5 | 4 |
| Markdown | 0 | 0 |
| parses on the main thread (store loop), any scenario | 1 per reparsed file | **0** |
| full index flask / tokio / zstd / fmt | 240 / 1,692 / 758 / 273 | 160 / 1,128 / 558 / 198 |
| no-op | 0 | 0 |

Baseline sites per grammar file: `tree-sitter-tags` (worker), the shared
OXIDE tree for imports + extraction metadata (worker, #24 C1), and the
separate calls + bases parse (**main**, store loop, #24 C2). The challenger
drops the third: the call and base queries run on the second. The
tags-internal parse stays (not shareable without a fork), so 2 is the floor.

## 3. Current flow (Phase 2)

`index/pipeline.rs::parse_and_persist_changed_files`, shared by
`update_base` and the watcher's `update_base_for_files`:

1. **Parse phase** — `std::thread::scope`, ≤4 workers, `to_parse` split
   into *static contiguous chunks*. Per file: `scanner::language_for_source`
   (2 parses for an ambiguous `.h`), `parser::parse_file` (tags parse +
   shared OXIDE tree) → `(ParsedFile, Language)`. All workers are joined
   and results concatenated in chunk order. **There is no channel and no
   producer/consumer overlap**: the store loop starts only after every
   worker has finished, so there is no backpressure to change.
2. **Resolve phase** (main thread) — project-wide `known_names`, then per
   symbol `references` and `content_hash = embedding_input_hash(...)`.
3. **Store loop** (main thread, serial) — per file: symbol delta counts,
   `compute_file_relations(&pf.symbols, &pf.src, lang)` (own parse +
   calls/bases queries + attribution), `compute_file_postings`, then one
   `replace_file` transaction (symbols, kept embeddings, relations,
   postings).

What `compute_file_relations` reads, verified in source rather than from
types: `Symbol::id()` (= FNV of `file` + `qualified_name`), `start_line`,
`end_line`, `kind`, `name`, and `qualified_name` (length for the nesting
tie-break, `:__module__` suffix for the file fallback), plus `src` and the
language. It never reads `references`, `content_hash`, `imports`,
`calls`/`bases` or `signature`. Between the worker and the store loop
only step 2 mutates symbols, and it writes only `references` and
`content_hash`; the symbol vector's order and length do not change
(`min_by_key`'s first-wins ties depend on that order). So every input is
final when `parse_file` returns in the worker.

The grammar is the same too: `extractor_for(X)` uses `X_PROFILE`, whose
`language` is X, and `tree_sitter_structural::ts_language(X)` returns
`X_PROFILE.ts_language()` (JavaScript → the TSX grammar on both sides);
both build the tree with `Parser::new()` + `set_language` + `parse(src,
None)`, no timeout or ranges. A failed parse yields empty metadata *and*
empty sites on one tree, exactly as two independent failures of a
deterministic parse would.

## 4. Challenger (Phase 3)

No new abstraction, thread, pool, channel, runtime, storage type or schema:

- `tree_sitter_structural::calls_and_bases_in_tree` — the two existing
  queries on a caller-supplied tree; `all_calls_and_bases_in_file` = parse
  + that (unchanged behavior). `StructuralSites` names the existing tuple.
- `LanguageExtractor::extract_with_structure` — default = the existing
  `extract_with_imports` + `all_calls_and_bases_in_file` (none for
  Markdown). `TagsExtractor` overrides it: one parse, `collect_meta` walk
  and both queries on that tree, tree dropped, then `extract_from_meta` as
  before. The tree's lifetime ends before the tags parse starts, as in #24.
- `parser::parse_file_with_structure` — `parse_file` plus the sites; the
  dedup + module-fallback body moved unchanged into a shared
  `finish_symbols`.
- `structural_relations::relations_from_sites` — the attribution half of
  `compute_file_relations`, byte-for-byte the old body;
  `compute_file_relations` = Markdown guard + parse + that (the backfill
  path in `base.rs` still uses it, unchanged).
- `index/pipeline.rs` — the worker calls `parse_file_with_structure` +
  `relations_from_sites`; the parallel `Vec<Language>` #24 added becomes a
  parallel `Vec<FileRelations>`; the store loop passes the precomputed
  rows to the same `replace_file` call. Worker count, chunking, join
  order, store order and transaction boundaries are unchanged.

5 files, +185 / −43, of which 36 are two new contract tests:
`extract_with_structure_matches_the_separate_calls` and
`worker_relations_match_the_separate_parse` (every `fixtures/conformance`
file: identical imports, serialized symbols, sites and relation rows).

## 5. Equivalence

| check | result |
|---|---|
| extraction dump through each side's own worker seam — every serialized `Symbol` field (qualified name, kind, span, parent, exported, imports, signature, parser `content_hash`), id, and the relation row, in output order — 1,822 files | byte-identical, `f9736065…` both (`results/extraction_dump.sha256`) |
| `oxide eval --config fixtures/benchmark.json` | byte-identical (`results/eval.*.txt`) |
| `index.db` full dump + `index --json`, 10 corpora × {full, no-op, edit, watcher, delete, add, no-op} (#24's `equiv.py`, unchanged) | 70/70 steps identical (`results/equivalence.txt`) |
| repeated-run stability (challenger vs challenger, same suite) | 70/70 identical, same canonical hashes as above (`results/repeat_stability.challenger.txt`) |
| full test suite default + `--no-default-features`, `mise run verify` | §11 |

The DB comparison covers every table incl. `symbols.content_hash` (the
embedding-input hash), persisted ids, `embeddings`, `symbol_relations`,
`lexical_postings`/`lexical_docs`, `files` and `meta.index_generation`
(counts write transactions); only `meta.index_id` and `meta.root` are
masked. As in #24, rows are compared as sorted multisets: physical row
order already differs between two runs of the *same* binary (hash-map
iteration of the changed-file set; #24's `order_control.txt`), and is
reported per step as `db_order=differs` on both sides of both comparisons.

## 6. Performance (interleaved A/B)

7 reps × 8 repos × 5 scenarios, each step a fresh process on the pinned
cores, hashed embedder; within each rep the two binaries alternate which
runs first (#24 always ran the baseline first; the order effect measured
here is −22.9% vs −24.5%, i.e. negligible). `results/ab_index.jsonl`,
`results/ab_summary.md`. Median paired change:

| scenario | wall | CPU | per-repo median wall | paired reps faster |
|---|--:|--:|---|--:|
| **full index** | **−23.4%** | **−10.3%** | −13.9% (fmt) … −32.4% (zstd) | **56/56** |
| largest-file edit¹ | −4.3% | −4.1% | −7.8% … +0.3% | 41/49 |
| median-file edit | −0.4% | +0.5% | −1.2% … +7.8% | 28/56 |
| watcher edit | +1.2% | +1.2% | −2.2% … +9.5% | 28/56 |
| no-op | +0.8% | +0.4% | −1.6% … +7.7% | 28/56 |

¹ Seven active repos. axios's largest tracked `.js` (`dist/axios.js`) is
not indexed, so its 7 "largest-file edit" pairs reparse 0 files (0 parses
on both sides) and are excluded; #24's selection rule is kept unchanged
for comparability. Including them gives −4.2%, 45/56.

No-op runs no changed code at all, so its ±8% per-repo spread is the noise
floor for these 7–130 ms single-file scenarios. The two worst edit medians
(flask edit +7.8%, watcher +9.5%, i.e. +2–4 ms on ~25 ms) have paired
ranges spanning −20% … +54% and did not reproduce in the allocator runs
(§8: −2.8% … +3.5% default malloc). Every full-index rep of every repo
was faster.

## 7. Scheduling: removed vs shifted work

Full index, medians of paired deltas (`results/shift_decomposition.md`,
stage figures in `results/ab_summary.md`):

| repo | Δ wall ms | Δ store-loop wall | Δ parse-stage wall | Δ store CPU | Δ worker CPU | net Δ CPU | share of removed store CPU that reappears in workers |
|---|--:|--:|--:|--:|--:|--:|--:|
| flask | −101 | −108 | +5 | −108 | +57 | −54 | 53% |
| darkreader | −164 | −220 | +56 | −220 | +125 | −96 | 57% |
| axios | −83 | −134 | +50 | −134 | +83 | −50 | 62% |
| tokio | −535 | −671 | +124 | −669 | +360 | −309 | 54% |
| gin | −163 | −223 | +66 | −223 | +136 | −86 | 61% |
| gson | −327 | −406 | +79 | −405 | +233 | −161 | 57% |
| zstd | −1,033 | −1,136 | +139 | −1,134 | +476 | −657 | 42% |
| fmt | −798 | −1,308 | +502 | −1,306 | +766 | −533 | 59% |

- **Store loop:** 30–59% less wall per repo (e.g. zstd 2,125 → 989 ms,
  tokio 1,786 → 1,119 ms). What remains there is postings + SQLite.
- **Workers are heavier:** +22–52% parse-stage CPU (the relation queries and
  attribution now run there). The removed parse is the other 38–58% of
  the old store-loop relations cost, and it is gone outright: total CPU
  −10%.
- **Wall = store saving − parse-stage growth** on every repo; the moved
  work runs ~4-wide, so it costs 5–502 ms of parse-stage wall against
  108–1,308 ms removed from the serial loop. The gain is part removed
  work (the parse) and part parallelization (queries + attribution), not
  a relabeling: the serial critical path shrinks.
- **Worker balance:** pool utilization is unchanged (e.g. zstd 75% → 75%,
  fmt 51% → 50%) because chunks are static and contiguous; the
  first-to-last worker finish spread grows with the extra per-file work
  (fmt 1,557 → 1,875 ms, one huge file dominates a chunk). fmt's
  parse stage therefore absorbs the most (+502 ms) and gains least
  (−13.9%). Dynamic work distribution could recover some of that, but it
  is a separate concurrency change and not attempted here.
- **Channel/backpressure:** none exists (parse and store are sequential
  phases), so nothing changed there. The resolve gap between them is
  unchanged (±2 ms).

## 8. Memory

Full index peak RSS: median +1.1% over repos (−8.5% … +9.6% per repo,
within ±3 MiB). Single-file edits and the watcher under default glibc
malloc: +0.3 … +3.9 MiB (median +5–6.5%). The largest single file (fmt
`gmock-gtest-all.cc`, 2 s edit): **−5.8 MiB** (−10%).

Isolation (`results/alloc_check.jsonl`, `results/alloc_check_summary.md`;
flask, darkreader, fmt × all scenarios × 5 interleaved reps × 3 malloc
settings):

| scenario | default | `MALLOC_ARENA_MAX=1` | pinned mmap/trim thresholds (#24) |
|---|--:|--:|--:|
| edit_median Δ RSS (flask / darkreader / fmt) | +1.5 / +1.6 / +3.8 MiB | +0.2 / −0.1 / −0.1 | −0.1 / −0.4 / −0.0 |
| watch_median Δ RSS | +1.5 / +2.4 / +3.9 | +0.2 / +0.0 / −0.4 | +0.1 / −0.5 / −0.0 |
| edit_largest Δ RSS | +1.3 / +0.3 / −5.8 | −0.2 / −0.4 / −9.1 | −0.3 / −1.4 / −5.0 |
| full Δ RSS | −0.4 / +2.6 / +2.9 | +0.1 / +1.0 / −1.1 | −0.7 / +0.0 / −2.3 |

Reading: the small-edit increase largely vanishes with a single malloc
arena, and with #24's pinned thresholds. That is consistent with glibc
per-thread-arena high-water behavior — the relation queries' allocations
moved from the main thread's arena to a worker thread's, whose freed
pages the later SQLite work on the main thread does not reuse — and
points away from retained data as the main cause; it does not prove that
*all* of the added RSS is allocator placement (the relation rows below
are genuinely retained longer). By
construction the relations tree is dropped inside `meta_and_sites`
before the tags parse, so a worker holds one OXIDE tree at a time as
before, and the store loop no longer builds a tree at all (hence the
largest-file drop). What *is* retained longer: each reparsed file's
relation rows (`(id, calls, bases)`) now live from the parse phase until
that file's `replace_file`, alongside `ParsedFile`'s symbols and source
that were already retained for the whole batch; the full-index RSS
figures above bound that cost.

## 9. Complexity

Five files, one default trait method + one override, one entry point, one
function split in two, one parallel `Vec` retyped. No new threads, locks,
atomics, channels or shared state: the worker still owns its chunk and
returns owned values through the existing join. The only new invariant —
the tree handed to `calls_and_bases_in_tree` must come from `lang`'s
grammar and `src` — is documented at the function and covered by the two
contract tests (the profile table is the single source of both grammars).

One behavioral edge: attribution now runs on a worker thread, so a panic
in it (none is reachable today — `.expect`s are on static query captures)
would surface as the pipeline's existing `"parse worker panicked"` error
instead of unwinding the main thread. The unchanged-file relations
backfill (`base.rs`, `Stage::Relations`) keeps its own parse; it only runs
once for pre-feature indexes and never fired in these scenarios.

## 10. Disposition

**ACCEPT** (pending approval), against the issue's criteria:

1. equivalence proven — extraction dump, `oxide eval`, 70/70 DB + JSON
   steps, repeated-run stability;
2. 3 → 2 parses per reparsed file achieved (`.h` 5 → 4), 0 on the store loop;
3. full index −23.4% median wall beyond #24, all 56 paired reps faster,
   −13.9% … −32.4% per repo;
4. incremental/watcher not regressed: largest-file edit −4.3% (41/49), median and
   watcher edits within the no-op noise floor;
5. memory acceptable: full index within ±3 MiB; small edits +≤3.9 MiB,
   largely removed by a single malloc arena (consistent with arena
   placement, not proven to be all of it); largest file −5.8 MiB;
6. complexity small and local, no new concurrency primitives;
7. serialization reduced, not shifted: store loop −30 … −59% wall, parse
   stage grows by 5–38% of what the store loop lost.

## 11. Verification log

`mise run verify` on the challenger (`results/verify.log`) **exited 1**, at
its last step only (below). Run with
`CARGO_TARGET_DIR` on disk (the checkout lives on a small tmpfs):
`cargo fmt --check`, clippy `-D warnings` default and
`--no-default-features`, full test suite in both feature sets (**1,035
passed, 0 failed, 7 ignored** — #24's 1,031 + the two new contract tests
in each set; includes the language-conformance goldens, `incremental`,
`full_incremental_parity`, `update_base_for_files`, `watcher_e2e`,
`lexical_persistence`, `index_generation`, `interrupted_index_recovery`
and `tests/benchmark_gate.rs`), release build + canonical fixture
benchmark (hybrid recall@5 0.909 ≥ vector-only 0.818, identical to the
baseline's `oxide eval`). The final `installer-check` step failed only
because `tests/install_sh_test.sh` looks for `target/release/oxide`
literally rather than honoring `CARGO_TARGET_DIR`; rerun with `target`
symlinked to the relocated directory it passes 64/64
(`results/installer_check.log`). No goldens were regenerated.

## 12. Independent review (Codex)

Read-only review (fresh Codex session) of the diff, harness, raw results
and this report (`results/codex_review.txt`). Verdict: **ACCEPT justified**
— no BLOCKER or MAJOR finding. It confirmed from source that attribution
reads only id/name/kind/span/qualified-name and that the resolve pass
changes only `references`/`content_hash` without reordering; that
`update_base`, `update_base_for_files` and the watcher share the pipeline
while the backfill keeps `compute_file_relations`; identical grammar and
`parse(src, None)` on both sides (JavaScript → TSX, `.h` resolution before
extraction, Markdown empty, failed parse → empty on both); unchanged
chunking, join and concatenation order, no channel; the tree dropped
before tag extraction; one `replace_file` transaction incl. the generation
bump; and it recomputed all 280 A/B pairs (56/56 full-index faster,
−23.4% / −10.3% CPU), the shift decomposition and all 225 allocator pairs.

| # | severity | finding | resolution |
|---|---|---|---|
| 1 | MINOR (confirmed) | 7 axios "largest-file edit" pairs are no-ops (`dist/axios.js` not indexed) but were counted in −4.2%, 45/56 | §6 now reports the 7 active repos: −4.3%, 41/49, with the inclusive figure in a footnote; criterion 4 updated |
| 2 | MINOR (confirmed) | §2 said the language census covered all 1,822 files; the pipeline reparsed 1,787 | §2 states both denominators; ratios were already per reparsed file |
| 3 | NOTE | the allocator runs support, but do not prove, that *all* small-edit RSS is arena placement; relation rows are genuinely retained longer | §8 and criterion 5 reworded |
| — | note | `mise run verify` itself exited 1 at `installer-check` | §11 now says so up front; the step passes 64/64 with the binary where it expects |
| — | note | the panic-surface difference (worker join) is real | already disclosed in §9 |
| — | note | equivalence is strong sampled evidence, not a proof for every possible file | agreed; §5 lists exactly what was compared |
