# Retrieval-layer profile: evidence stages, allocation, cold/warm, and the Pareto pass

Follow-on to [`../sqlite-request-path/README.md`](../sqlite-request-path/README.md),
which made retrieval candidate-first. This round profiled every evidence
stage of a request — literal, lexical, semantic, structural, git, blast
radius, fusion, hydration — **with allocation counts**, separated
embedding inference from retrieval proper, compared cold and warm
process state, and ran a Pareto gate over each candidate change. SQLite
stays authoritative, the schema gains one index, ranking is byte-identical,
and every CLI/MCP contract is unchanged.

Scope stop: no ANN/sqlite-vec/Zvec (the exact scan is still under 15 ms at
15k symbols and is not the bottleneck anywhere below), no new subsystem,
no reranker.

## 0. Method

- **Machine**: 16-core laptop, not idle (interleaved A/B runs; medians of
  7 unless stated). Offline hashed embedder (256-dim) unless stated, so
  numbers are retrieval, not inference. Numbers within one table are
  comparable; across tables the machine's load differed.
- **Workloads**: shallow clones indexed fresh — `requests` (933 symbols,
  4.7 MB), `flask` (1,800), `zod` (4,780, TypeScript), `pytest` (7,804,
  38 MB), `pylint` (15,057, 51 MB); plus `fixtures/py_repo` and, for the
  native embedder, a second `requests` index.
- **Tools** (none needed a new dependency): `examples/retrieval_profile.rs`
  (new — per-stage wall clock **and** allocation count/bytes from a
  counting `#[global_allocator]`; `PROFILE_REPEAT=2` runs a cold and a
  warm round in one process), a `getrusage`-based wrapper for one-shot CLI
  latency + peak RSS (`/usr/bin/time` is not installed here, which also
  means `scripts/perf.sh` cannot run on this machine), and
  `scripts/mcp_bench.py` for the warm MCP path.
- **Parity gate**: 5 repos × 8 queries × 11 surfaces (`search` lexical /
  semantic / hybrid / `--no-expand` / `--profile quality` / `--blast-radius`,
  `context` fast / balanced / quality / `--git` / `--blast-radius`), all
  `--json` = **440 outputs**, plus 55 literal-search outputs and
  `oxide eval --config fixtures/benchmark.json`. Two runs of the baseline
  binary are byte-identical to each other (the baseline is deterministic),
  and the final binary is byte-identical to the baseline on all 440 + 55
  outputs and on the fixture eval.

## 1. Where a request spends its time (baseline, `pytest`, warm process)

`examples/retrieval_profile.rs`, second round in the same process:

| stage | ms | allocs | bytes | notes |
| --- | ---: | ---: | ---: | --- |
| `stats` (3× `COUNT(*)`) | 3.9–7.6 | 2 | 0 | every one-shot CLI request; `embeddings` walks the blob table |
| lexical prepare + score | 1.6 | 72 | 158 KB | 5 terms, 1,411 postings |
| `embed_query` (hashed) | 0.02 | 11 | 1 KB | inference is separate: see §3 |
| embedding scan (rows only) | 6.0–6.4 | 4 | 0 | 7,804 × 256-dim, streamed |
| `symbols_by_ids` ×400 | 2.2–2.8 | 11,497 | 817 KB | hydration; 29 allocs/row (JSON `references`) |
| **`search hybrid --no-expand`** | **15.5–18.1** | **57,475** | **3.9 MB** | hydration is 11.5k of those allocs — the rest was hit assembly |
| `all_symbols` | 48.8–73.1 | 350,292 | 24.3 MB | 45 allocs/row: 117,849 `references` + 120,444 `imports` strings |
| `all_symbol_relations` | 9.3–9.9 | 50,534 | 2.2 MB | 21,274 rows; a `String` per `kind` column |
| **`SymbolSnapshot::load`** | **60–82** | **400,824** | **26.7 MB** | the corpus load `context`/expansion pay |
| `RelationGraph::build` | 4.7–5.9 | 20,520 | 2.1 MB | two `to_lowercase` per symbol + 4 SipHash maps |
| `search hybrid` (expand, cold engine) | 71–87 | 432,573 | 30.7 MB | = no-expand + corpus load + graph |
| `context balanced` (warm snapshot) | 23–28 | 86,043 | 6.8 MB | the MCP-cached case |
| `context balanced` (cold engine) | 88–106 | 485,800 | 34.3 MB | the one-shot CLI case |
| `gitctx::build_git_context` | 40–44 | 754 | 64 KB | see §2.7: a racy-index artifact of a fresh clone; ~10 ms on a refreshed index |
| `literal::search` ("parse") | 27 | 23,401 | 10.3 MB | walk 9–10, read 3–4, **per-line split+match 10–12**, whole-buffer match 0.7 |
| `blast_radius::compute` | 1.2 | 53 | 4 KB | |

`pylint` (15k) scales the same way: `all_symbols` 68–71 ms, `SymbolSnapshot::load`
83–100 ms, scan 14 ms, `stats` 9 ms.

### One-shot CLI, end to end (median of 5, hashed embedder, baseline)

| repo | `search --no-expand` | `search` | `search --blast-radius` | `context` | `context --git` | RSS (search / context) |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| requests | 16 ms | 27 ms | 34 ms | 28 ms | 60 ms | 20 / 19 MB |
| pytest | 39 ms | 123 ms | 196 ms | 128 ms | 173 ms | 21 / 39 MB |
| pylint | 42 ms | 139 ms | 265 ms | 164 ms | 273 ms | 20 / 40 MB |

Anything that needs the corpus (`context`, `search` with expansion,
`--blast-radius`, `-m lexical` with expansion) costs ~3× a `--no-expand`
search, and that gap is `SymbolSnapshot::load` + `RelationGraph::build`.

### Prioritized bottlenecks (measured share of the request)

1. **Corpus load** (`all_symbols` + `all_symbol_relations`): 55–65% of a
   one-shot `context`, and its 400k allocations are 85% of the request's.
   Decomposed in §2.1: it is row/overflow reads + JSON + string
   allocation, near its floor for this schema and `Symbol` shape.
2. **`COUNT(*) FROM embeddings`** on every one-shot request: 4–9 ms fixed,
   14–20% of a `--no-expand` search. Fixed (§2.2).
3. **Search hit assembly**: 45k of a search's 57k allocations were
   `Symbol` clones and `SearchHit`s built for all ~400 fused candidates,
   twice, to return 16. Fixed (§2.3).
4. **Literal search per-line split**: 10–12 ms of a 27 ms scan, paid in
   full even when the pattern appears nowhere. Fixed (§2.4).
5. **`RelationGraph::build`**: 5–8 ms per request, including every warm
   MCP request (20–25% of a warm `context`); allocation-heavy. Allocations
   −74%, time −22% (§2.5); the rest is a design note (§5).
6. **Native model load**: 130 ms per one-shot process (§3). Not
   retrieval; reported, not changed.
7. `review`: an O(N) `find` per related item and — a correctness gap —
   no tie-break on tied structural scores (§2.6).

## 2. Candidates, measurements, verdicts

Every accepted change is byte-identical on the 440-output parity matrix,
the 55 literal outputs and the fixture eval; `cargo test` (all binaries),
`clippy --all-targets -D warnings` and `fmt` are clean.

### 2.1 Corpus load (`all_symbols`, `all_symbol_relations`) — partly accepted

| variant | pytest `all_symbols` | pylint `all_symbols` | verdict |
| --- | ---: | ---: | --- |
| baseline | 49.2–49.9 ms, 350k allocs | 70.6–71.0 ms, 351k allocs | |
| borrow `kind`/`language`/JSON columns off the row (`get_ref`) + parse `imports_json` once per run of identical text (file-level, rows adjacent) + borrow `kind` in `all_symbol_relations` | 44.5–45.4 ms, 305k allocs (−9%, −13%) | 67.8–68.6 ms, 288k (−4%, −18%) | **accept** |
| drop SQL `ORDER BY file, start_line`, sort in Rust by `(file, start_line, id)` | 41–45 ms (no gain) | 52–56 ms (−7%, within noise of the above) | **reject** — see below |
| SQLite `page_size` 8/16/32 KB (VACUUM'd copy) | 38–41 ms at every size | — | **reject**: no effect on the load; the *ordered* step got slower (17 → 29 ms at 32 KB) |

Why the `ORDER BY` result is a negative result worth keeping: a step-only
probe shows the ordered query at 17 ms vs 4 ms for a rowid scan, and
Python's `sqlite3` showed 36 vs 22 ms for the full query — both misleading.
SQLite's sorter materializes the full row, so the ordered step *includes*
reading each ~1 KB row's overflow pages, which a plain scan merely defers
to column access; end to end, in Rust against the bundled SQLite, the two
are equal and the Rust sort adds 2–7 ms. The SQL `ORDER BY` stays, and its
tie order — `(file, rowid)`, verified equal to a `(file, start_line, id)`
sort on all five corpora including 580 tied pairs on `pylint` — is now
documented on `IndexBackend::all_symbols`.

What remains in the load (pytest, warm): ~13 ms row/overflow reads, ~7 ms
string allocation for 8 columns, ~10 ms `serde_json` for 240k
`references`/`imports` items (≈40 ns per item, allocation-bound), and the
relations side table. Reducing it further means changing what is stored
(e.g. a compact `references` encoding, or a side table so the row fits
its page) or what `Symbol` owns — schema/type changes, out of scope here
and listed in §5.

### 2.2 `COUNT(*) FROM embeddings` — accepted

`validate_index` compares `embeddings` and `symbols` row counts on every
request (a real completeness check; the count must stay exact). `symbols`
and `files` already count through a covering index; `embeddings` had none,
so SQLite walked the table b-tree whose ~1 KB `vec` blobs spread 15k rows
over every page. One additive DDL line: `CREATE INDEX IF NOT EXISTS
idx_embeddings_symbol ON embeddings(symbol_id)`.

| | without index | with index |
| --- | ---: | ---: |
| `COUNT(*) FROM embeddings`, pylint, warm connection | 7.51 ms | 0.01 ms |
| plan | `SCAN embeddings` | `SCAN embeddings USING COVERING INDEX idx_embeddings_symbol` |
| pytest `search --no-expand` (same binary, db without vs with) | 37.1–37.6 ms | 31.4–32.8 ms (−14%) |
| pytest `search -m semantic` | 28.8–28.9 ms | 24.2–24.5 ms (−16%) |
| pytest `search -m lexical --no-expand` | 21.3–21.6 ms | 16.6–18.6 ms (−18%) |
| pytest `context` | 113.6–118.0 ms | 115.2–115.5 ms (unchanged; ~5 of 115 ms) |
| cold index, pytest | 6.36 s | 6.41 s (+0.8%, noise) |
| no-change reindex | 192 ms | 177 ms |
| `index.db` | 39,424,000 B | 39,448,576 B (+24 KB, +0.06%) |

The exhaustive vector scan (`SELECT symbol_id, dim, vec FROM embeddings`)
still plans as `SCAN embeddings`; `tests/query_plans.rs` pins both.
Existing indexes gain the index on their next writer open (any `oxide
index` run, including a no-change one) and serve correctly, just without
the speedup, until then.

### 2.3 Search hit assembly — accepted

`RetrievalEngine::search` built a `SearchHit` (owning a `Symbol` clone) for
every fused candidate, sorted, then rebuilt the whole list a second time
after expansion, and cloned `rrf` for base scores. It now ranks `(id,
score)` pairs, borrows expansion candidates from the snapshot, and
assembles hits only for the `limit` entries it returns. The tie-break
(`cmp_score_id`) is the same order `cmp_hit` produced: the key id equals
`Symbol::id()` for every hydrated candidate (rows are stored under that
FNV1a id and `hydrate` keys by it).

| pytest (warm) | before | after |
| --- | ---: | ---: |
| `search hybrid --no-expand` | 15.5 ms, 57,475 allocs, 3.9 MB | 12.6 ms, 20,090 allocs, 1.8 MB (−19%, −65%) |
| `search lexical --no-expand` | 5.5 ms, 36,765 | 3.6 ms, 12,874 (−35%) |
| `search semantic` | 11.1 ms, 24,943 | 9.9 ms, 8,696 |
| `context balanced` (warm snapshot) | 23.3 ms, 85,977 | 20.7 ms, 47,418 (−11%, −45%) |
| pylint `search hybrid --no-expand` | 22.4 ms, 38,652 | 19.8 ms, 13,375 (−11%, −65%) |

### 2.4 Literal search — accepted

`literal::search` split every file into lines and ran the `memmem` finder
per line, so the scalar split was paid for every file, match or no match:

| pytest, in-process | walk | read | per-line split + match | whole-buffer `find_iter` |
| --- | ---: | ---: | ---: | ---: |
| "self" (9,510 matches) | 9.4 ms | 3.2 ms | **9.6 ms** | **0.8 ms** |
| absent pattern | 10.6 ms | 4.1 ms | **11.8 ms** | **0.6 ms** |

It now scans each whole buffer once and derives line/column only at match
sites (newlines counted between consecutive matches with `memchr`).
Semantics are unchanged — non-overlapping left-to-right matches, per-file
and global caps, `column` as byte offset within the line, and a pattern
containing `\n` matches nothing, exactly as it never could line by line —
verified by the existing `rg` parity test and 55/55 byte-identical outputs
over 11 patterns × 5 repos (including `\r`, multi-match lines, and
truncated results).

| one-shot CLI, median of 7 | baseline | after |
| --- | ---: | ---: |
| pytest "self" | 29.0–30.1 ms | 22.9–24.4 ms (−20%) |
| pytest absent | 31.3–31.8 ms | 19.6–19.9 ms (−37%) |
| pylint "self" | 44.6–46.3 ms | 40.1–41.4 ms (−10%) |
| pylint absent | 50.9–59.3 ms | 46.5–48.7 ms (−14%) |

`pylint` (4,365 files) is now dominated by the parallel walk (16–18 ms,
including a per-file open + 1 KB binary sniff) and the sequential
`fs::read` of every file (15 ms): syscalls, not matching. Parallel reads
would help there; not done (see §5).

### 2.5 `RelationGraph::build` — partly accepted, one negative result

| variant (pylint, warm) | build | allocs | bytes | verdict |
| --- | ---: | ---: | ---: | --- |
| baseline | 7.9–8.4 ms | 40,783 | 3.5 MB | |
| `is_test_symbol` lowercasing into a reused buffer, **per-char `char::to_lowercase`** | 9.8–10.6 ms (+25%) | 10,679 | 2.6 MB | **reject**: `str::to_lowercase` has a bulk ASCII fast path; per-char table lookups cost more than the two allocations saved |
| same, but `make_ascii_lowercase` into the buffer for ASCII input, `to_lowercase` fallback otherwise (identical mapping) | 7.9–8.1 ms (equal) | 10,705 | 2.6 MB | accept as churn reduction (latency-neutral) |
| + `rustc_hash::FxHashMap` for the graph's probe-only maps and `SymbolSnapshot::by_id` (already in the dependency graph; nothing iterates these maps) | 3.4–3.7 ms vs 4.6–5.9 on the same quieter runs (−22–25%) | 10,705 | 2.6 MB | **accept** |
| warm `context balanced` | 18.0–18.9 ms, 60,481 allocs → 17.3–18.2 ms, 30,403 allocs (−4%, −50%) | | | |

### 2.6 `review` — accepted (correctness)

`build_review_context` sorted `related` by score only. Structural scores
are exact small integers (1.0 per relation), so ties are the norm, and the
candidate map is a `HashMap` — the same diff produced **4 different
`related` orders in 8 runs** of `oxide review --json` on `fixtures/py_repo`,
and at the `truncate(15)` boundary a different *set*. It now uses the
`(score desc, id asc)` tie-break every other ranked surface has, pinned by
`tests/review_e2e.rs::review_related_order_is_deterministic_under_tied_scores`
(fails on the old code in 2 of 3 runs, as a random-order bug should). The
per-item `symbols.iter().find(|s| s.id() == id)` — an FNV hash of every
symbol per related item — became the snapshot's by-id lookup.

### 2.7 Git evidence — no change (and one diagnostic)

`context --git` measured 40–44 ms over `context` on a fresh clone, all of
it `git diff HEAD` (39 ms). That was the clone's **racy index**: entries
whose mtime equals the index's are re-hashed on every `git diff` until
something refreshes the index (`git status`, a commit…), after which the
same command takes 4 ms. On a refreshed index `--git` costs 9–12 ms
(3–4 `git` spawns at 3–5 ms each) — too little to justify running them
concurrently. A user who sees a slow first `--git` after cloning is
seeing git, not OXIDE.

### 2.8 Not attempted, by measurement

- **Exact vector scan**: 6 ms at 7.8k, 14 ms at 15k symbols (256-dim) —
  never the largest stage on any surface here. The §6 verdict of the
  previous round stands.
- **Lexical**: prepare + score is 1.6–2.4 ms on the persisted postings.
- **Blast radius**: `compute` itself is ~1 ms; `--blast-radius`'s
  70–125 ms on `search` is the corpus load it forces (`snapshot_with_relations`),
  i.e. bottleneck #1, not the traversal.

## 3. Embedding inference vs retrieval; cold vs warm

Native default (`arctic-embed-xs-q`, 384-dim), `requests` indexed natively:

| | cold (first request in process) | warm (same process) |
| --- | ---: | ---: |
| `open_embedder` (ONNX session) | **130 ms**, 123,708 allocs, 29 MB | once per process |
| `embed_query` | 5.4 ms | 3.7–4.0 ms |
| `search hybrid --no-expand` (excl. model load) | 8.8 ms | 8.8–13.4 ms |

One-shot CLI, end to end (median of 5): native `search` 171 ms / 76 MB RSS
vs hashed 29 ms / 21 MB; native `context` 170 ms vs 27 ms; `search -m
lexical` 24 ms either way (it never loads the model). **~82% of a native
one-shot call is model load + inference**, ~75% of it the load, which
`oxide mcp` pays once per process. The load is `fastembed`'s standard ORT
session construction; nothing at OXIDE's level shortens it, and it is
not part of the retrieval numbers above.

Cold vs warm retrieval (same process, `PROFILE_REPEAT=2`, pytest): the
corpus load drops 73 → 49 ms (page cache + allocator warm), `stats`
7.6 → 3.9 ms, the scan 6.4 → 6.0 ms; everything else is within noise.
The MCP process cache removes the corpus load and `stats` entirely:

| warm MCP (`scripts/mcp_bench.py`, 12 calls, median after the first) | baseline | after | server RSS |
| --- | ---: | ---: | --- |
| pytest `search` | 30.0 ms | 26.8 ms (−11%) | 57 → 45 MB (−21%) |
| pytest `context` | 26.0 ms | 23.3 ms (−10%) | |
| pylint `search` | 39.3 ms | 40.6 ms (noise) | 49 → 47 MB |
| pylint `context` | 37.9 ms | 31.0 ms (−18%) | |

## 4. Final numbers (one-shot CLI, hashed embedder, interleaved with the baseline binary on the same indexed databases)

Code changes only (both binaries see the new index once a writer created
it; §2.2's table isolates the index itself):

| repo | surface | baseline | final |
| --- | --- | ---: | ---: |
| pytest | `search` (expand) | 105–108 ms / 39 MB | 101–103 ms / 38 MB |
| pytest | `context` | 116–119 ms / 39 MB | 107–108 ms / 38 MB (−8%) |
| pylint | `search` (expand) | 140–142 ms | 138 ms |
| pylint | `context` | 154–155 ms / 40 MB | 145–151 ms / 39 MB (−4%) |
| requests | `context` | 25–28 ms | 23–27 ms |

Combined with the index (§2.2) on the surfaces that don't load the corpus:
pytest `search --no-expand` 37 → 32 ms, semantic 29 → 24 ms, lexical
21 → 17 ms. Retrieval quality: `oxide eval` rows identical; 440/440 and
55/55 outputs byte-identical, so recall/precision/context utility are
unchanged by construction. Indexing cost: +0.8% cold (noise), +0.06% db.

## 4.1 Python relative-import fix (`0859161`), finalized

Verified end to end on `fixtures/py_repo`: `oxide review` of a diff touching
`HttpClient.fetch` now emits `imported-definition←HttpClient.fetch` for
`TooManyAttemptsError`, `RetryPolicy.wait` and `RetryPolicy.should_retry`
through `from .retry import …` — the exact edge the Phase-4.2 held-out
report recorded as never firing. Two follow-ups landed on top of it:

- A bare `.`/`..` whose target is the repo root produced the candidate
  `"/__init__.py"` (leading slash; `joined` was empty), so a root-level
  package never resolved. Fixed; `bare_dot_import_resolves_at_the_repo_root`.
- Greptile (P1, first review): the dot-relative branch fed the
  language-agnostic candidate list, so `.store` from `pkg/mod.py` could
  resolve to `pkg/store.ts`/`store.rb` when no Python module existed, or
  become ambiguous when `store.py` and `store.ts` coexisted. `.name`
  syntax is Python-only, so its candidates are now Python's alone; a bare
  `.`/`..` keeps both forms (TypeScript's `import x from '.'`).
  `python_dotted_relative_import_only_considers_python_candidates`.

One scoring consequence to be aware of, not a bug: a Python symbol reached
through a resolved relative import now gets both `uses←` and
`imported-definition←` (score 2.0 in `review`), exactly as TypeScript's
`./x` imports always have.

## 4.1.1 Library semantics the byte-identity depends on, checked at the source

- `memchr` 2.8.3 (`src/memmem/mod.rs`, `FindIter::next`): `self.pos =
  pos + needle.len().max(1)` — `find_iter` is non-overlapping, exactly the
  old `cursor = match_start + needle.len()`. (A documentation-aggregator
  snippet showed overlapping offsets for `aba` in `ababab`; its source was
  a benchmark *haystack* text file, not the crate. The pinned crate source
  is what counts.)
- `rusqlite` 0.32.1 (`types/from_sql.rs`): `impl FromSql for String` is
  `value.as_str().map(ToString::to_string)`, so `get_ref()?.as_str()?`
  fails on the same rows `get::<String>()` did (non-text, invalid UTF-8);
  only the error's column index differs on that already-corrupt path.
- SQLite's `COUNT(*)` index choice is pinned empirically rather than
  cited: `tests/query_plans.rs` asserts the plan with and without
  `ANALYZE`, and `EXPLAIN QUERY PLAN` on a pre-change and a post-change
  database shows `SCAN embeddings` → `SCAN embeddings USING COVERING INDEX
  idx_embeddings_symbol` while the vector scan stays `SCAN embeddings`.
  A pre-change database serves `search`/`context` unchanged (file
  checksum identical after read-only use) and gains the index on its
  next writer open; results are byte-identical either way.

## 4.2 Independent review

`greptile review --agent --branch main` on a throwaway worktree branch
based on `origin/main` (the local `main` was one unpushed commit ahead,
which the reviewer's server cannot see; the branch and worktree were
deleted afterwards, nothing was pushed). First pass: confidence 4/5, one
P1 (the cross-language candidate list above), every other change judged
contract-preserving. Second pass after the fix: **confidence 5/5, no
comments**. `mise run verify` (fmt, clippy `-D warnings`, the
`--no-default-features` lint and test, the full suite, the fixture
benchmark, installer checks): all green on the final diff.

## 5. Remaining work, in priority order

1. **The corpus load is the one-shot ceiling** — 55–65% of `context` and
   of `search` with expansion, ~10 µs and ~40 allocations per symbol.
   Closing it means either not loading the corpus (SQL-probe structural
   expansion: `symbol_relations(kind, target)` and `symbols(name)` are
   indexable, but `related_tests` scans every test symbol's `references`
   by contract, and `neighbors()`' order comes from corpus order) or
   loading less per row (a compact `references` encoding; a side table so
   the ~1 KB row fits its page). Both are schema/type changes with a
   re-index, not a patch — they need the "architecture discussion first"
   note in AGENTS.md.
2. **`RelationGraph` per warm MCP request** (3.5 ms after this round,
   ~15% of a warm `context`): caching it alongside the snapshot needs an
   index-based graph (it currently borrows `&[Symbol]`), a moderate
   refactor of `relations.rs`.
3. **Literal search on many small files** is walk + `fs::read` bound
   (pylint: 30 of 45 ms); reading files on the walk's own threads, or in
   parallel over the sorted list with an in-order fold, would keep the
   deterministic order.
4. `scripts/perf.sh` requires `/usr/bin/time -v`; a `getrusage` fallback
   would let it run where GNU time is absent.
