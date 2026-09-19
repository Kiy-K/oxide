# Literal search: control-arm baseline

Status: **Control arm shipped** (`oxide search --mode literal`, `src/literal.rs`).
**FTS5 trigram challenger measured and REJECTED** (`spike/`, a standalone
crate — not part of OXIDE's build, not reachable from any user-facing
surface) — see the verdict section below. **Profiled** the control against
`rg -F`, found the gap's real source (line-splitting + matching +
allocation combined, not walk/I/O), and **optimized the matcher**
(`memchr::memmem` — ~3.9x faster end-to-end on a clean real-repo
comparison). **Reviewed by both Greptile and Codex** (commits
`636b0ae..ec7bfd0`): Greptile found a real pre-existing bug in the
per-file match cap's `truncated` flag, and its proposed ordering fix was
itself caught by a Codex review as a coverage regression — the resolution
keeps per-file multi-file coverage, fixes the flag bug outright, and
documents the actual (not strict-global-prefix) contract explicitly; see
"Post-review correctness fix" below for the full back-and-forth. Remaining
performance gap (~2.6-3.2x vs. ripgrep, down from ~12-13x) is
line-splitting and per-hit allocation, not yet addressed. No regex support
either way.

## What was built

A native, deterministic byte-substring scan over every file OXIDE's ignore
policy would keep (`scanner::scan_repo_text` — the same denylist, `.gitignore`
handling, binary sniffing, and size cap as indexing's `scan_repo`, minus the
"has a recognized language" gate, so READMEs, configs, and other repository
text are searchable too). Exposed identically through:

- CLI: `oxide search QUERY --mode literal [--limit N] [--json]`
- `RepositoryService::search_literal(pattern, limit)`
- MCP: the existing `search` tool's optional `mode: "literal"` argument —
  not a separate tool. `search`'s other mode values (lexical/semantic/
  hybrid) stay unexposed over MCP as before (they only change internal
  ranking weights and share one output shape); `literal` earns a place in
  the same schema specifically because it changes *capability* (no index
  needed) and *output shape* (`{hits, truncated}` instead of ranked
  evidence) — see `SEARCH_MODE_DESCRIPTION` in `src/mcp.rs`. This mirrors
  the CLI's own `search --mode literal`/`search --json` asymmetry: same
  verb, output shape gated by `mode`, rather than inventing an MCP-only
  naming convention.

It needs no index and no embedder: `RepositoryService::search_literal` reads
files straight off disk, so it works identically before and after `oxide
index` has ever run — and never touches `.oxide/index.db` if one already
exists (`tests/literal_search.rs::literal_search_never_touches_an_existing
_index_db`).

## Correctness

- `src/literal.rs`'s own unit tests pin byte-exact matching (including on
  non-UTF-8 files — the pattern is matched as raw bytes; only the returned
  snippet is lossily decoded for display), deterministic truncation
  (accumulation happens in the same sorted `(file, line, column)` order the
  final result is in, so a `build_parallel()` walk's nondeterministic
  arrival order never leaks into which subset survives a limit), and that
  the ignore policy (denylist dirs, `.gitignore`, and — via the walker's own
  `hidden(true)` — dotfiles including `.env*`) is respected.
- `tests/literal_search.rs::matches_ripgrep_over_the_same_file_set` compares
  OXIDE's literal search against `rg -F` restricted to *exactly* the file
  set `scanner::scan_repo_text` returns (fetched directly via
  `oxide::scanner::scan_repo_text`, not re-derived heuristically). Comparing
  against a bare `rg -F` over the whole tree would only measure the two
  tools' differing default ignore policies, not the matcher itself — this
  is why the file set is pinned before comparing.
- `tests/mcp_e2e.rs::search_literal_mode_needs_no_index_and_matches_cli_output`
  asserts the CLI (`--json`) and MCP `search` tool (`mode: "literal"`)
  return byte-identical JSON for the same query on the same repository,
  with no index built — the parity criterion the issue's acceptance list
  asks for. `search_rejects_a_lexical_semantic_or_hybrid_mode_value` pins
  that a `mode` value from the CLI's larger lexical/semantic/hybrid/literal
  set, other than `"literal"`, is rejected rather than silently falling
  through to the default hybrid search.
- UX matrix (`tests/literal_search.rs`): empty pattern is an
  `invalid_configuration` error (not a match-everything scan), no matches is
  an empty bounded result rather than an error, an empty/unindexed
  repository is not an error, quoted regex metacharacters (`.`, `*`, `(`)
  match literally rather than as a pattern, and `--mode bogus` is rejected
  with `literal` listed among the valid choices.

## Zero index/DB/update cost

The control arm's whole value proposition is that it costs *nothing* at
rest and *nothing* to keep current — not just that it can run without an
index, but that it never creates or modifies one:

- `tests/literal_search.rs::literal_search_never_creates_an_oxide_directory`:
  a literal search on a repository with no `.oxide` leaves it that way.
- `tests/literal_search.rs::literal_search_never_touches_an_existing_index_db`:
  on a repository that already has an index, `index.db`'s bytes and the
  full set of files under `.oxide/` (no incidental `-wal`/`-shm`) are
  identical before and after a literal search — the same idiom
  `tests/cli_e2e.rs::read_only_commands_never_modify_index_db_content`
  already pins for `status`/`search`/`context`. Unlike those, which read
  the database through `SqliteStore::open_read_only` and can therefore
  legitimately touch WAL state as any reader does,
  `RepositoryService::search_literal` never opens the database at all, so
  its bar is the stricter "not even that."
- There is consequently no "cold-index cost" or "database growth" line for
  literal search itself in the latency table below — those columns are
  N/A by construction, not measured-and-small. The one index-related
  number in this doc (`oxide index`'s ~5.5s / 70MB on the 800-module repo)
  is reported only as a contrast, not as a cost literal search incurs.

## Latency vs. `rg -F`

Measured with `scripts/literal_search_bench.sh` on `scripts/gen_bench_repo.py`
synthetic repos (this machine, 20 reps, best-effort — not a controlled
benchmark environment; treat as order-of-magnitude, not a precise number).
Pattern: `RetryPolicy`, present across the generated corpus.

| Repo size            | oxide literal p50 / p95 | `rg -F` p50 / p95 (whole tree) |
|-----------------------|------------------------|----------------------------------|
| 200 modules/lang (1,205 files) | 28ms / 37ms | 23ms / 32ms |
| 800 modules/lang (4,805 files) | 46ms / 57ms | 29ms / 36ms |

`rg -F` stays roughly 1.3-1.6x faster here. At the time this table was
first measured we guessed the gap was "dominated by process startup and
file I/O, not the matcher" — **that guess was wrong, and the profiling
section below corrects it with a phase-by-phase measurement**: at real-repo
scale, the byte-scan matcher itself is the dominant cost by roughly an
order of magnitude, not walk or I/O. `rg`'s hit count is uncapped and
includes files OXIDE's denylist excludes (e.g. nothing under `.git`);
OXIDE's is capped at `--limit` (200 here), which is why the two hit counts
in the script's raw output aren't directly comparable — the parity test
above, not this script, is what establishes they agree on file/line/column
identity.

## Profiling: where the gap to `rg -F` actually comes from

Measured with `examples/literal_scan_profile.rs` (new; diagnostic only, no
`src/literal.rs` changes — this does not optimize anything) on the OXIDE
repository itself (1,396 kept files, 31.5 MB), 15 reps, decomposing
`literal::search`'s own work into the same phases it performs internally
but timed separately, plus ripgrep's real matching engine run in-process
(via the `grep-searcher`/`grep-regex` dev-dependencies — not a
reimplementation, not a subprocess) over the *identical* file list, so the
comparison isolates matcher/algorithm differences from ignore-policy
differences:

| Phase | pattern with 200 hits (`"fn "`) | pattern with 2 hits (absent-ish) |
|---|---:|---:|
| A: walk (`scan_repo_text`) | 7.6ms | 8.9ms |
| B: read all kept files | 8.9ms | 8.8ms |
| **C: control end-to-end** | **169.9ms** | **150.1ms** |
| D: rg engine, in-process, same files | 12.5ms | 11.7ms |
| E: `rg -F` subprocess, same files | 13.0ms | 13.1ms |

**The byte-scan matcher, not the walk or the I/O, is the dominant cost —
by roughly an order of magnitude.** `A + B` (walk + read) is ~16-18ms in
both rows; `C` (the whole control call) is 150-170ms regardless of hit
count. The difference, `C - (A + B)` ≈ 132-153ms, is what the byte-scan
loop itself costs once file discovery and I/O are subtracted out — and
that number barely moves between a pattern with 200 hits and a
near-absent one, which rules out "many matches cost more to record" as the
explanation: **the cost is proportional to bytes scanned, not matches
found**, exactly what you'd expect from `src/literal.rs::find`'s current
algorithm (`haystack.windows(needle.len()).position(|w| w == needle)` — a
byte-by-byte scalar comparison, re-windowed from scratch on every call).

Ripgrep's own engine (D), run in-process over the exact same 1,396 files,
does the whole scan in ~12ms — about **12-13x faster than the control's
byte-scan phase alone**, and that gap is essentially the entire story:
`D` and `E` (real `rg -F` subprocess) agree closely (12.5ms vs 13.0ms),
meaning process-spawn overhead is *not* a meaningful factor at this corpus
size either — the earlier subprocess-vs-subprocess CLI benchmark's 1.3-1.6x
number was already comparing two fairly-matched process-startup costs, and
what's left over (the walk+read+match difference) is now explained: almost
entirely the matcher.

This matters for the smaller fixture repos too, in the opposite direction:
on `fixtures/py_repo` (8KB, 10 files), walk alone (2.7ms) exceeds the
control's whole end-to-end time (2.0ms, well within measurement noise at
this scale) — confirming the walk-dominates-at-small-scale intuition was
directionally right for tiny corpora, just not for anything resembling a
real repository, which is the case that actually matters for this
decision.

This profiling pass didn't optimize anything itself, but identified exactly
the shape a fix should take (SIMD/vectorized substring search instead of a
scalar windowed-equality loop) — see the next section for the follow-up
that acted on it.

For reference, `oxide index` on the 800-module repo (26,415 symbols) took
~5.5s and produced a 70MB `.oxide/index.db` — literal search needs none of
that, which is the whole point of a path that works before a repository has
ever been indexed.

## Optimization: `memchr::memmem` replaces the scalar matcher

**Kept — measured improvement is substantial and unambiguous, no
regressions found.** `src/literal.rs::search` now builds one
`memchr::memmem::Finder` per call (reused across every file and line,
rather than the old per-call `windows().position()` scan reconstructed
from scratch on each match attempt) and calls `finder.find(...)` where the
old code called the hand-rolled `find`. `memchr` was already present
transitively (pinned at 2.8.3 via `ignore`/`regex`/`bstr`); this adds a
direct dependency, no new version, no new compile cost. Nothing about the
function's signature, the walk, the file set, the caps, or the CLI/MCP
surface changed.

### What's preserved (verified, not assumed)

- **Byte-exact matching, including non-UTF-8 files**: `src/literal.rs`'s
  own `matches_non_utf8_files_byte_exactly` test passes unchanged — the
  matcher still runs on raw bytes, never decoded text.
- **Exact `(file, line, column)` ordering**: `finds_matches_with_line_and_column`
  (asserts specific column numbers) and `truncates_deterministically_when
  _over_limit` (asserts a specific deterministic subset survives
  truncation) both pass unchanged. `memchr::memmem::Finder::find` finds
  the same leftmost match in a given slice `windows().position()` did, so
  the cursor-advance loop's positional semantics are identical. **This
  specific claim was incomplete when first written** — see "Post-review
  correctness fix" below for a real sorted-prefix violation a Greptile
  review found in the surrounding per-file-cap logic, which neither this
  memchr swap nor the existing tests at the time exercised.
- **All caps** (`MAX_MATCHES_PER_FILE`, `SCAN_SAFETY_CAP`, `MAX_RESULTS`):
  untouched — the loop structure around the matcher call didn't change,
  only what's inside the innermost `while let Some(pos) = ...`.
- **CLI/MCP parity**: `tests/mcp_e2e.rs::search_literal_mode_needs_no_index
  _and_matches_cli_output` (asserts the CLI and MCP tool return
  byte-identical JSON) and `tests/literal_search.rs::matches_ripgrep_over
  _the_same_file_set` (real `rg -F` parity) both pass unchanged.
- **Zero database/index access**: no change to what `search` touches on
  disk — still `scanner::scan_repo_text` + `std::fs::read`, nothing else.
- **Retrieval untouched**: `src/retrieval.rs` has zero diff; `oxide eval
  --config fixtures/benchmark.json` is byte-identical before and after.

### Measured: end-to-end and per-stage, same corpora as the profiling pass

`examples/literal_scan_profile.rs`, same machine, same methodology
(best-effort, not a controlled benchmark environment — treat as
order-of-magnitude). OXIDE repo: 1,396 files, ~31.5MB, 15 reps.

| Stage | before (windows) | after (memmem) | speedup |
|---|---:|---:|---:|
| A: walk | ~8ms | ~10ms | ~1x (unchanged; noise) |
| B: read | ~9ms | ~10-16ms | ~1x (unchanged; noise) |
| **C: control end-to-end, near-absent pattern (2 hits)** | **150.1ms** | **~38-39ms** | **~3.9x** |
| D: rg engine, in-process (unchanged, reference) | ~12ms | ~12-15ms | n/a |

The near-absent-pattern row is the clean, isolated matcher comparison:
that query never triggers `MAX_MATCHES_PER_FILE` (only 2 hits total), so
its workload — and therefore this comparison — is unaffected by the
correctness fix below. It is not inflated by walk or I/O changing either
(A and B are within measurement noise of the pre-optimization numbers, as
expected — nothing about them changed).

**The `"fn "` (200-hit) row from the original measurement is retracted, not
just updated** — the correctness fix below makes it measure a different
*workload*, not just a faster implementation of the same one (see the next
section for why), so a before/after speedup number for it would compare
two different queries and is not reported.

On the small fixture repos (`fixtures/py_repo`, `fixtures/ts_repo`, ~5-8KB)
control end-to-end stayed ~3-4ms, unchanged within noise — walk dominates
at that scale regardless of matcher, exactly as the profiling section
found.

RSS: a control-only session (`docs/literal-search-eval/spike/bench.sh
SKIP_TRIGRAM=1`, the same tool and methodology that produced the
previously-recorded 56MB baseline) now peaks at **57.3MB** — no measurable
regression; a `Finder` built once per call is not a meaningful allocation
next to reading 31.5MB of file content.

### Remaining gap (not closed, not in scope here)

The control is now **~2.6-3.2x slower than ripgrep's engine** on the
near-absent-pattern comparison (was ~12-13x before the memchr swap) — real
progress, gap not closed. The profiling pass's **Phase B2** (read +
line-split, no matching: `bytes.split(|&b| b == b'\n')`) measured ~25-31ms
on this corpus — a plain scalar per-byte closure call with no SIMD fast
path in `std::slice::split` — which is now comparable to or larger than
the matcher's own remaining cost. Closing the rest of this gap would mean
also replacing the newline-finding with a `memchr`-based line iterator (or
restructuring around `grep-searcher`'s buffered line-oriented model
directly), plus reducing per-hit allocation (the `display.clone()`/
snippet-truncation cost on every `LiteralHit`). **Neither is done here** —
out of scope for "replace the matcher," and each would need its own
before/after measurement the way this one got.

### Post-review correctness fix: two reviews, two opposite proposed fixes, and the resolution

A Greptile review of this optimization (`greptile review --branch <pre-#6
commit>`, commits `636b0ae..ec7bfd0`) found a **P1** bug in the per-file
cap logic (`MAX_MATCHES_PER_FILE = 50`) — pre-existing since the control's
first commit, not introduced by the memchr swap, but only caught once a
reviewer checked the interaction directly. Fixing it produced a second,
conflicting review finding, and the sequence of both is worth recording in
full because the final answer is a documented tradeoff, not a clean bug
fix.

**The bug (confirmed real, not theoretical):** hitting a file's 50-match
cap moved on to the *next* file instead of stopping. Files are visited in
sorted order, so a file with more than 50 matches had its matches 51+
silently dropped while *later-sorting* files' matches were still admitted
— an "ordered but non-prefix" subset, per Greptile: *"Matches 51 onward
therefore disappear even though they sort before every match from those
later files, so a request for up to 200 results can return an ordered but
non-prefix subset."* `truncated` was also set unconditionally at the
50th match, even when a file had *exactly* 50 and nothing more — a
separate, unambiguous bug regardless of which design won below.
`src/retrieval.rs` in this very repo has 80+ occurrences of `"fn "`, the
exact pattern this doc's earlier benchmark used, so **the original `"fn "`
(200-hit) measurements above were already affected by this bug** — that
row is retracted rather than corrected, since any fix changes what that
specific query actually scans.

**First fix attempt (rejected by a second review):** make the cap abort
the *entire* scan (`break 'files'`) instead of moving to the next file,
so the result is always a strict global sorted prefix. A Codex review of
that attempt (`codex review --uncommitted`) caught a real regression it
introduced: *"the previous per-file cap explicitly prevented one noisy
file from crowding out all others... [this fix] causes a common
match-heavy file to terminate repository-wide search, suppressing valid
results from all later files."* Both reviews are correct about the
property each is defending — they simply value different things — but
strict global ordering and per-file crowding protection cannot both hold
when one file legitimately has more matches than the cap: something has
to give.

**Final resolution:** keep the original per-file-skip-to-next-file
behavior (multi-file coverage wins — literal search's job is finding a
string across a repo, and ripgrep's own `-m`/`--max-count` is per-file for
the same reason, with no global-prefix guarantee either), fix *only* the
unambiguous `truncated`-flag bug (check-before-record, so it's set only
when a match genuinely exists beyond the cap), and make the actual
contract explicit in `MAX_MATCHES_PER_FILE`'s and `search`'s own doc
comments rather than pretending the earlier wording's implied strict
top-K was ever fully accurate. This resolves Greptile's underlying
concern (a caller should not be misled about the guarantee) without
Codex's flagged regression (other files still get a chance).

**Regression tests** (`src/literal.rs`): `per_file_cap_skips_to_the_next
_file_and_flags_truncated` (an over-cap file's later-sorting sibling MUST
still appear — pins the final, deliberate design) and `exactly_at_the_per
_file_cap_does_not_falsely_report_truncated` (a file with exactly the cap
count and nothing more must not set `truncated` — the one part of the bug
fixed outright).

A second, lower-severity (**P2**) finding from the same review — that
`examples/literal_scan_profile.rs`'s original wording attributed the whole
`C - (A + B)` remainder to "the matcher," when Phase B2 itself shows
line-splitting is a substantial, separately-measured part of it — is
addressed by this document's wording above and by the profiler's own
output strings, both corrected to attribute the remainder to
"line-splitting + matching + per-hit allocation" rather than "the
matcher" alone.

## FTS5 trigram challenger: measured and rejected

### Where this lives

`spike/` (this directory) is a **standalone Rust crate** — its own
`Cargo.toml`, its own empty `[workspace]`, never a member of any ancestor
workspace, and OXIDE's root `Cargo.toml` has no `[workspace]` section
either, so `cargo build`/`cargo test` at the repo root never sees it. It
takes a path dependency on the real `oxide` crate so its tests can compare
byte-for-byte against the actual shipped control (`oxide::literal::search`)
rather than a second reimplementation of it — see `spike/Cargo.toml`'s top
comment for why that's a deliberate departure from `docs/
storage-backend-eval/spike`'s fully-standalone design. **There is no
FTS5/trigram code anywhere in `src/`** — `src/literal.rs`'s own module doc
says so and points back here.

- `spike/src/lib.rs` — the challenger implementation and its unit/parity
  tests (`cargo test --manifest-path spike/Cargo.toml`).
- `spike/src/bin/bench.rs` — the benchmark harness this report's numbers
  came from (`cargo run --release --manifest-path spike/Cargo.toml --bin
  bench -- REPO...`, or `spike/bench.sh` for the full battery used below).

### Design

The challenger implements the exact same contract the control established:
same `LiteralHit`/`LiteralSearchResult` types (imported from `oxide::
literal`, not re-declared), same bounds (`MAX_MATCHES_PER_FILE`/
`SCAN_SAFETY_CAP`/`MAX_RESULTS` — duplicated as private constants inside
`spike/src/lib.rs`, since they're private to `oxide::literal` and this
experimental crate has no business asking the shipped crate to widen its
API for a rejected experiment; the parity tests are what catch drift), same
file set (`oxide::scanner::scan_repo_text`), same `(file, line, column)`
ordering. **It is not reachable from any CLI flag, MCP argument, or
service method** — no `--mode trigram`, nothing in `RepositoryService`, and
now not even in the same crate.

Schema: one FTS5 virtual table, one row per line —
`CREATE VIRTUAL TABLE lines USING fts5(file UNINDEXED, line_no UNINDEXED,
content, tokenize = 'trigram case_sensitive 1')`. `case_sensitive 1` is
deliberate: the tokenizer defaults to case-*insensitive*, which would make
the challenger answer a different question than the control (and than
`rg -F`'s own case-sensitive default) — caught by
`case_sensitive_by_construction_unlike_the_tokenizer_default` in the
module's tests before it reached the benchmark. A MATCH query only narrows
candidate lines; each candidate's stored content is re-scanned with the
control's own byte-exact `find` to compute the exact column and to not
trust FTS5's candidate set blindly, which is standard practice for
trigram-accelerated substring search.

### Correctness

Exact, byte-identical results to the control on every pattern tried, **with
two structural exceptions found and reproduced, not just theorized:**

1. **Patterns shorter than 3 bytes cannot be trigram-queried at all**
   (`MIN_PATTERN_LEN`). FTS5's trigram tokenizer has no complete trigram to
   query on below that length; `spike`'s `search` returns an explicit error
   rather than silently returning nothing or falling back.
   The control has no such floor — a 1-2 character literal search (a
   single-letter flag, an operator) is exactly the kind of "partial
   identifier" query issue #6's own scope calls out, and the challenger
   cannot serve it.
2. **Non-UTF-8 files are invisible to the trigram index.** FTS5 columns are
   text; `build`/`update_file` skip a file outright rather than index a
   lossily-decoded (and therefore byte-inexact) stand-in for it. The
   control matches raw bytes and has no such gap — this is one of the
   issue's own explicit UX-matrix requirements ("non-UTF-8 files"), and the
   challenger fails it structurally, not by omission. Closing this would
   need a custom FTS5 tokenizer over raw bytes, out of scope for an
   experimental challenger.

Both are pinned by tests (`rejects_patterns_shorter_than_the_trigram_floor`,
`skips_non_utf8_files_and_reports_the_gap`) and reproduced live on every
corpus below (`ab`, 2 bytes, always shows as `TRIGRAM_ERROR` against the
control's real hits).

### Latency, DB growth, and update cost

Measured with `spike/src/bin/bench.rs` (`REPS=15`, via `spike/bench.sh`) on:
the two committed fixtures, the OXIDE repository itself, and two synthetic
corpora
(`scripts/gen_bench_repo.py`) for a controlled growth curve. No network
access was available in this environment to clone external real-world
repos (Django/pylint/matplotlib-scale, as `examples/
lexical_parity_real_repos.rs` uses when `~/.cache/oxide-contextbench/repos`
is populated); the OXIDE repository itself — a genuine, non-trivial,
prose-and-code real codebase — stands in as this evaluation's
"representative real repo," and turned out to be the single most
informative data point (see the DB-growth finding below). This machine,
best-effort, not a controlled benchmark environment — treat as
order-of-magnitude.

| Corpus | files | lines | control p50 | trigram p50 | trigram DB size | bytes/line | update (1 file) |
|---|---:|---:|---:|---:|---:|---:|---:|
| fixtures/py_repo | 10 | 272 | 2.3-3.6ms | 0.35ms | 94 KB | 345 | 0.4-0.8ms |
| fixtures/ts_repo | 10 | 202 | 2.4ms | 0.26ms | 74 KB | 372 | 0.5ms |
| synthetic, 200 modules/lang | 1,205 | 29,238 | 16.9ms | 2.2ms | 4.7 MB | 169 | 7.9ms |
| synthetic, 800 modules/lang | 4,805 | 116,838 | 48.4ms | 6.5ms | 18.9 MB | 168 | 23.3ms |
| **OXIDE repo itself (real)** | **1,393** | **119,753** | **107-187ms** | **27-45ms** | **140 MB** | **1,170** | **26-43ms** |

Control's own DB size and update cost are 0 in every row — it has no index
to grow or keep current.

**The DB-growth number is the load-bearing finding here, and it is much
worse than the ~34MB the prior experiment (`docs/storage-backend-eval/
enhanced-sqlite.md`) measured.** That number was for a trigram index over
*symbol bodies only* — a small, code-only subset of a repository. This
challenger indexes everything `scan_repo_text` keeps (READMEs, configs,
committed benchmark data, all prose), which is what literal search's own
value proposition ("search text beyond supported source-language files")
requires. The two synthetic corpora look cheap (**~170 bytes/line**) because
generator-templated code is extremely repetitive and trigram-compresses
well; **the real OXIDE repository costs ~1,170 bytes/line — roughly 7x
worse** — because prose (docs, comments, raw eval data) has far higher
trigram cardinality than repetitive generated code. A synthetic-only
benchmark would have badly underestimated this cost. On this repo, the
trigram database (140 MB) would be **more than 3x the size of the entire
`.oxide/index.db`** the symbol index itself produces (per the control-arm
report above, ~70 MB for a much larger 800-module synthetic corpus) — a
disproportionate storage cost for one feature.

Latency: trigram is consistently 4-7x faster than the control on
real/synthetic corpora at this scale (the OXIDE repo: ~107-187ms vs
~27-45ms). The mechanism is exactly what a trigram index is for: the
control re-walks, re-reads, and re-scans the whole kept file set on every
call (it has no persistent state to avoid any of that), while the trigram
query narrows straight to matching lines. The "Profiling" section below
(added in a later pass) breaks the control's own cost down further and
finds the re-*scan* — not the walk or the read — is what actually
dominates; trigram sidesteps all three, but it's the scan it mainly saves.
Absolute control latency stays well under 200ms even on this repo, though —
nowhere near a threshold where an agent or a human would perceive it as
slow.

RSS: a combined session (build + correctness + both latencies + update) on
the OXIDE repo peaked at **78.5 MB** (`VmHWM`); a control-only session
(repeated scans, no trigram build at all) peaked at **56 MB**. The
difference (~22 MB) is roughly the trigram build's own working set, well
below the on-disk DB size since SQLite doesn't hold the whole index
resident.

### Verdict: REJECT (keep experimental)

Per the issue's own Pareto gate — "accept only if measured discovery
utility or workflow cost improves enough to justify the added index size
and maintenance path" — **reject promoting the trigram challenger to a
shipped mode.** Reasoning:

- The control's absolute latency is already fast enough for interactive
  and agent use (well under 200ms on a genuine, non-trivial real repo) —
  the 4-7x speedup is real but does not cross a threshold where the
  control becomes a problem.
- The storage cost is large and *scales unfavorably with realism*: 140 MB
  on this one real repo, 7x worse per-line than synthetic corpora
  suggested, and more than 3x the size of OXIDE's own primary symbol
  index. A feature whose accelerator costs more than the thing it
  accelerates is a hard sell.
- Two real correctness gaps (sub-3-byte patterns, non-UTF-8 files) mean the
  challenger cannot be a drop-in replacement for the control even ignoring
  cost — it would need to coexist with a fallback path, adding complexity
  the control alone doesn't have.
- The control has zero maintenance path (nothing to keep fresh, ever); the
  trigram index needs its own live-update wiring into `oxide index`/
  `oxide watch` to not go stale, which is undesigned and unbuilt work this
  evaluation deliberately did not do (out of scope: "do not expand scope").

**Keep the native scan as the sole shipped literal-search implementation.**
Keep `spike/` in the repo, standalone and out of OXIDE's build entirely, as
the reproducible evidence and the pickup point the day this calculus
changes — most plausibly if a target corpus's control latency crosses into
the seconds range (large monorepos well beyond what was tested here) or
literal search becomes a sufficiently hot path that even tens of
milliseconds compound across a session. Re-run `spike/bench.sh` against
that corpus before reopening this decision; don't re-decide from these
numbers alone.

## What this does and doesn't settle

This closes all three halves of issue #6's "earn the surface through
measured workflow value" framing: a deterministic, tested, documented
literal-search control ships (needs no index, ~0 cost); the FTS5 trigram
challenger it was supposed to be measured against has been built,
benchmarked on real and synthetic corpora, and rejected on cost and
correctness grounds — not left unevaluated, and relocated out of `src/`
entirely once rejected so production contains no dormant FTS5 code; and
the control's own gap to `rg -F` has been profiled, correctly attributed
(line-splitting + matching + allocation combined — correcting an earlier
wrong guess that named the matcher alone), and closed substantially
(`memchr::memmem`, ~3.9x faster on a clean comparison, no regression) —
with the remainder (line-splitting, per-hit allocation) identified and
left for a future pass rather than pursued here. Greptile and Codex
reviews of the whole change additionally caught a real, pre-existing
`truncated`-flag bug and a real regression in the first attempt to fix it,
resolving to a documented (not silently assumed) per-file fairness
contract before any of this was considered settled — see "Post-review
correctness fix." Nothing about regex or a production trigram index
changes; both remain explicitly out of scope.
