# One-parse extraction for reparsed files — evaluation (#24)

Benchmark-first experiment: does OXIDE parse each reparsed file more often
than it needs to, does that cost anything material, and can the redundant
parses be removed while changing no output at all? Parents: #27, #9.

**Disposition: ACCEPT (pending approval)** — see [§10](#10-disposition).

## 1. Baseline

| | |
|---|---|
| OXIDE `main` | `e407935c620f0a2100e3428b7770f888098a38e8` (after #22 split, #23 REJECT; #28 Go bugs tracked separately and untouched) |
| tree-sitter / tree-sitter-tags | 0.27.0 / 0.27.0 (unchanged) |
| machine | i7-13620H, Linux 7.2.5, rustc 1.98.0 |

## 2. How parses were counted (Phase 1)

Every Rust parse method in tree-sitter 0.27 (`Parser::parse`,
`parse_with_options`, the UTF-16 variants) and `tree-sitter-tags`'
internal parse funnel into one C function, `ts_parser_parse_with_options`.
The research probe (`harness/parse-probe`, a standalone crate outside
OXIDE's workspace and CI) links OXIDE unmodified and wraps that symbol at
link time (`-Wl,--wrap`). Every parse made through tree-sitter's Rust API
— which is how OXIDE and `tree-sitter-tags` parse — is counted and timed;
the C library's other entry points (`ts_parser_parse`,
`ts_parser_parse_string*`) are not wrapped, and nothing in this indexing
flow calls them. With `PROBE_ATTRIB=1` a debug build captures a backtrace
per parse and classifies it by call site (thread identity separates the
parse worker from the store loop). No instrumentation lives in OXIDE's
sources.

## 3. Measured parse counts (baseline)

Per-file seam, all 1,822 tracked text files of the eight corpora below plus
`fixtures/conformance` (`results/seam_attrib.baseline.json`):

| responsibility | parses per file | where |
|---|--:|---|
| imports (`collect_imports` → `collect_meta` walk) | 1 | `languages/tags.rs::parse` |
| extract metadata (`extract` → the same `collect_meta` walk again) | 1 | `languages/tags.rs::parse` |
| tag extraction (`TagsContext::generate_tags`, internal) | 1 | `tree-sitter-tags` |
| structural calls (`all_calls_in_file`) | 1 | `tree_sitter_structural.rs::parse` |
| base/inheritance (`all_bases_in_file`) | 1 | `tree_sitter_structural.rs::parse` |
| `.h` C/C++ disambiguation (`scanner::error_nodes`, C *and* C++ grammar) | 2 per resolution × 2 resolutions | `scanner.rs` — in the parse worker **and again in the store loop** |

| language | files | parses/file |
|---|--:|--:|
| Python, TS, TSX, JS, Rust, Go, Java, Ruby, PHP | 1,433 | **5.00** (each site exactly 1) |
| C | 175 | 6.51 (5 + 4 per `.h`) |
| C++ | 104 | 6.54 |
| Markdown | 110 | 0 |

Through the real pipeline (`update_base`/`update_index_scoped`, and
`update_base_for_files` for the watcher) the same counts hold exactly:
flask full index 400 parses for 80 grammar files, zstd 1,316 for 200
(316 `.h` disambiguation parses, split 2 + 2 between parse worker and store
loop), one-file edit/watch 5 (9 for a `.h`), no-op 0. The unchanged-file
relations refresh (`base.rs`) only parses during the one-time relations
backfill and never fired in these scenarios. There is no other parse path.
Per-site pipeline attribution for both binaries: `results/pipeline_attrib.json`.

`tree-sitter-tags` parses internally and never exposes its tree, so without
forking it the floor is **two** parses per file, not one.

## 4. Baseline cost (Phase 2)

Eight pinned repos, full index → no-op → median-file edit → largest-file
edit → watcher (`update_base_for_files`) edit, each step a fresh process
pinned to four distinct P-cores, hashed (offline) embedder, 5 reps
(`results/baseline_index.jsonl`):

| repo (language) | full-index wall | CPU | parse CPU / total CPU |
|---|--:|--:|--:|
| flask @7ee9ceb71e86 (Python) | 498 ms | 639 ms | 35% |
| darkreader @a787eb511f45 (TS/TSX) | 719 ms | 994 ms | 37% |
| axios @0abc70564746 (JS) | 460 ms | 589 ms | 35% |
| tokio @43c224ff47e4 (Rust) | 2,709 ms | 3,611 ms | 36% |
| gin @dcaa4296d111 (Go) | 812 ms | 1,028 ms | 37% |
| gson @8b4b55051489 (Java) | 1,917 ms | 2,392 ms | 28% |
| zstd @823a28a1f4cb (C) | 4,257 ms | 6,045 ms | 57% |
| fmt 11.1.4 (C++) | 7,831 ms | 10,062 ms | 46% |

Parsing is 28–57% of full-index CPU. Two of the five per-file parses (plus
the second `.h` resolution) are removable without touching dependencies —
a third, the tags parse, only with a `tree-sitter-tags` fork — and the
calls/bases parses run in the serial store loop, directly on the
wall-clock path. For
single-file edits parsing is 1–15% of wall on a median file and 11–33% on
the largest file (the rest is process start, query compilation, SQLite).
Baseline judged **material** for full indexing and large-file edits.

## 5. Challenger (Phase 3)

Smallest change that reuses work inside the existing path — no new
abstraction, no concurrency change, no tree kept across stages:

- **C1** `LanguageExtractor::extract_with_imports` (default = the two
  existing calls, so `MarkdownExtractor` is untouched); `TagsExtractor`
  overrides it with one parse + one `collect_meta` walk feeding both
  halves. `parse_file_with` calls it.
- **C2** `tree_sitter_structural::all_calls_and_bases_in_file` runs both
  queries on one tree; `compute_file_relations` uses it. The standalone
  `all_calls_in_file`/`all_bases_in_file` stay as thin wrappers.
- **C4** the parse worker already resolves each file's language; the store
  loop now reuses it instead of calling `language_for_source` again (a pure
  function of path + source, and every parsed file had resolved `Some`).

Result: **5 → 3 parses per file; 9 → 5 per `.h`** in the pipeline
(`results/pipeline_attrib.json`: zstd `.h` edit 9 = 5 + 2 worker + 2 store
loop → 5 = 3 + 2 worker; flask edit 5 → 3; full index zstd 1,316 → 758,
flask 400 → 240). The seam harness totals (8,984 → 5,560;
`results/seam_attrib.*.json`) measure C1 + C2 only: the seam mirrors the
old double language resolution in both binaries, so C4 does not show there
and a `.h` counts 7 in its challenger run. Lifetimes: C1 and C2 only
borrow a `Tree` within one function; the tags parse is untouched.

Two contract tests pin the shared paths against the separate ones on every
`fixtures/conformance` file (`extract_with_imports_matches_the_two_separate_calls`,
`shared_parse_matches_separate_calls_and_bases`).

Not done, and why: sharing the tags parse needs a fork of
`tree-sitter-tags`; moving `compute_file_relations` into the parallel
parse worker (reaching 2 parses and taking relations off the serial path)
is a concurrency change the issue gates separately.

## 6. Equivalence (baseline vs challenger binaries)

| check | result |
|---|---|
| extraction dump (symbols, qualified names, kinds, spans, parents, exported, imports, calls, bases) — 1,822 files | byte-identical (`results/extraction_dump.sha256`) |
| `oxide eval --config fixtures/benchmark.json` | byte-identical (`results/eval.*.txt`) |
| `index.db` full dump + `index --json`, 10 corpora × {full, no-op, edit, watcher, delete, add, no-op} | all 70 steps identical (`results/equivalence.txt`) |
| full test suite, default and `--no-default-features`; `mise run verify` | §11 |

The DB comparison covers every table (`symbols` incl. `content_hash` —
the embedding-input hash — and persisted ids, `embeddings` blobs,
`symbol_relations`, `lexical_postings`/`lexical_docs`, `files`, `meta`
incl. `index_generation`, which counts write transactions); only
`meta.index_id` (random) and `meta.root` (copy path) are masked. Rows are
compared as sorted multisets because **physical insertion order already
differs between two baseline runs** of the same binary on the same input
(checked on `py_repo`: 7/7 steps differ in order, 7/7 identical as
multisets; `results/order_control.txt`, 14/14 steps on py_repo and flask)
— a pre-existing property of the changed-file set's hash-map iteration
order, not introduced here and out of scope. Limit: a sorted-row
comparison checks row values (explicit rowids such as `symbols.id`
included), not implicit rowids or the physical order a row-order-sensitive
consumer might observe — which already varies run to run in the baseline.
Per-step hashes for both sides are in `results/equivalence.txt`; the full
dumps are regenerable with `harness/equiv.py`.

## 7. Performance delta

Baseline and challenger alternate inside every rep (7 reps × 8 repos × 5
scenarios, same pinned cores; `results/ab_index.jsonl`, `results/ab_summary.md`).
Median paired change:

| scenario | wall | CPU | range of per-repo wall medians |
|---|--:|--:|---|
| **full index** | **−14.8%** | **−17.1%** | −10.1% (gson) … −25.6% (zstd) |
| largest-file edit | −7.3% | −7.5% | −14.0% … −2.9% (axios void: its largest `.js` is not indexed) |
| median-file edit | −2.3% | −2.9% | −9.3% … +1.6% (mostly within noise) |
| watcher edit | −1.7% | −1.6% | −8.5% … +2.2% (within noise except C/C++) |
| no-op | +0.4% | +0.5% | noise (no parses either way) |

Every full-index rep of every repo was faster (worst paired rep −7.1%).
Parse time actually removed (timed inside the parse call) accounts for
14.3 of the 17.1 CPU points, so the gain is work removed, not moved; the
remaining ~3 points are consistent with the eliminated second
`collect_meta` walk, but that walk was not timed separately.

**Peak RSS:** full index within noise. Single large-file edits show a
consistent +0.9 to +4.0 MiB (fmt +7.4%). Isolation (`results/rss_allocator_check.txt`):
removing *either* redundant parse alone gives the same +4 MiB, and with
glibc's dynamic mmap/trim threshold pinned the peaks are identical (54.9
vs 55.0 MiB) while the speedup remains. That strongly indicates allocator
high-water behavior; peak RSS alone cannot prove nothing is retained, but
by construction no tree or `FileMeta` outlives the function that creates
it in either change.

## 8. Complexity / maintenance

6 files, +170/−39 lines, ~60 of them tests and the shared test helper. One
default trait method, one combined query function, one parallel
`Vec<Language>` in the pipeline. No new dependency, no schema, ranking,
embedding, scanner-policy, transaction or concurrency change; #28's Go
behavior is untouched (extraction output byte-identical).

## 9. Independent review (Codex)

Read-only review (fresh Codex thread) of the diff, harness and results.
Verdict: **ACCEPT justified** for the source challenger and the repeatable
full-index gain. It confirmed parse coverage for the Rust paths (incl.
`tree-sitter-tags`), identical behavior in C1 (sorting, dedup, parse
failure, Markdown default), C2 (sequential cursors on one borrowed tree,
per-query order kept, empty-on-failure) and C4 (`language_for_source`
depends only on path + source), unchanged worker count, join order, store
order and `replace_file` transactions, all 280 A/B pairs present with every
full-index pair faster, and the README's numbers against the raw files.
Findings, all confirmed and resolved:

| # | severity | finding | resolution |
|---|---|---|---|
| 1 | MINOR | seam totals don't measure C4 (seam `.h` = 7), yet were paired with "9 → 5" | pipeline attribution for both binaries added (`results/pipeline_attrib.json`); §5 now separates seam (C1 + C2) from pipeline (C1 + C2 + C4) counts |
| 2 | MINOR | challenger seam file labelled the new structural site `other`; seam scanner parses are `[other]` | classifier fixed and seam file regenerated; worker-vs-store split now comes from the pipeline attribution |
| 3 | MINOR | CPU residue attributed to the `collect_meta` walk without isolating it; RSS evidence can't prove no retention | wording narrowed in §7 |
| — | note | "every real parse" should be scoped; "three of five redundant" imprecise; sorted-row equivalence limits; dumps not preserved | §2, §4 and §6 corrected; per-step hashes and baseline order control added to `results/` |

Codex noted query compilation now follows parsing, which only changes the
order of a static-query panic that cannot happen (queries are compiled and
tested at build/test time); no reachable output difference.

## 10. Disposition

**ACCEPT** (pending approval): equivalence proven on extraction, eval,
database and `index --json`; full-index wall −10% to −26% on every repo,
repeatable; large-file edits −7%; memory flat except an allocator-policy
effect on single large-file edits; small, local diff.

The 4.49× CodeGraph figure was not a target and is not comparable.

## 11. Verification log

`mise run verify` on the challenger (`results/verify.log`): exit 0 in
3m29s — `cargo fmt --check`, clippy `-D warnings` default and
`--no-default-features`, full test suite in both feature sets (**1,031
passed, 0 failed, 7 ignored**, including both new contract tests, the
language-conformance goldens and `tests/benchmark_gate.rs`), release build
+ canonical fixture benchmark, installer checks.

## 12. Smallest justified next action

Land C1 + C2 + C4 as one behavior-preserving commit. A later, separately
gated experiment could move `compute_file_relations` into the parse worker
(2 parses, relations off the serial path); not started.
