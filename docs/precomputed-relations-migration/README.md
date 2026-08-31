# Migration: query-time structural search → precomputed relations

Executes the narrow-hybrid recommendation from
`docs/precomputed-structural-relations/README.md`: fold structural-relation
extraction into the normal indexing pipeline, persist it in
`symbol_relations`, serve `context.rs`'s bounded expansion from
`RelationGraph`'s reverse indexes instead of a live AST scan, and — since
parity held — delete the query-time backend (`src/structural.rs`,
`ast-grep-core`) rather than maintain two production code paths for the
same evidence.

## What changed

1. **Extraction folded into `update_index`'s existing per-file loop**
   (`index.rs`, same place `extract_references` already runs). Reuses that
   loop's already-open `pf.src` and already-parsed `pf.symbols` — one extra
   `tree_sitter::Query` pass per reparsed file
   (`tree_sitter_structural::all_calls_in_file`/`all_bases_in_file`), not a
   second file read. `structural_relations::compute_file_relations`
   replaces the experimental `index_structural_relations` second pass,
   which is deleted.
2. **`compute_file_relations` emits an entry for every symbol in a
   reparsed file**, including ones with empty `calls`/`bases` — not just
   ones with something to report (written via `replace_file`'s `relations`
   parameter, see the Codex review section below for why that's a single
   transaction, not two calls). This closes a real staleness bug the
   experiment had: a symbol whose only call gets edited away keeps the
   *same* `Symbol::id()` across reindexes, so skipping it when its
   relations become empty would leave the old, now-wrong relation rows in
   `symbol_relations` forever. Pinned by
   `editing_away_a_call_clears_the_stale_relation_on_reindex`
   (`structural_relations.rs`).
3. **`remove_files` sweeps orphaned `symbol_relations` rows**, mirroring
   the existing `drop_embeddings_without_symbols` pattern exactly (foreign
   keys aren't enforced in this codebase — `PRAGMA foreign_keys` is never
   turned on — so the table's `ON DELETE CASCADE` is declarative only). A
   symbol removed by a rename-within-a-still-present-file (via
   `replace_file`, not `remove_files`) leaves the same class of harmless
   orphan `embeddings` already leaves in that case — not a new gap, held
   to the existing standard rather than a stricter one.
4. **`context.rs`'s bounded expansion now reads `RelationGraph::callers_of`**
   (`retrieval.rs`) instead of calling `AstGrepProvider.find_callers` live.
   The bounding contract is unchanged: the same `scope_files` construction
   (union of the seed pool's files, capped by
   `RetrievalMode::structural_budget()`) that used to scope the file list
   handed to the AST scan now filters `callers_of`'s (repo-wide) result
   before use — required, not optional, since an unfiltered lookup returns
   far more than the seed pool would ever justify (see Reach/noise in the
   precomputed-relations experiment doc). `build_context` now loads
   symbols via `structural_relations::load_symbols_with_relations` instead
   of `store.all_symbols()` directly, so `RelationGraph` actually has
   `calls`/`bases` to serve.
5. **Parity proven before deletion**: `context::tests::bounded_ast_grep_expansion_is_mode_gated`
   (renamed test, same assertions — Balanced surfaces the AST-precise
   caller RelationGraph's `neighbors()` alone can't find, Fast skips it
   entirely) passes unchanged, all 13 `context.rs` tests pass, and
   `tests/benchmark_gate.rs`'s 19 tests (the frozen hybrid-retrieval
   regression gate) pass unchanged. Only then were
   `src/structural.rs`, `TreeSitterStructuralProvider` (the query-time half
   of `tree_sitter_structural.rs` — the extraction functions it shares
   with the index-time path stay), the `ast-grep-core` dependency, and the
   now-superseded conformance/benchmark examples deleted.

## Codex review: 2 real bugs found and fixed before this shipped

A second review pass, focused specifically on the migration's new wiring
(not re-litigating `enclosing()`'s attribution tie-breaks, already reviewed
in the prior experiment), found two real, distinct correctness gaps —
both fixed, not just documented:

1. **No backfill for a pre-existing on-disk index.** `update_index`'s
   incremental logic only reparses *changed* files; a file untouched since
   before this feature shipped would never get `symbol_relations` rows,
   silently under-delivering bounded expansion for it indefinitely. This
   was flagged as a real risk in this doc's first draft with a manual
   `rm -rf .oxide && oxide index` mitigation — the review treated that as
   insufficient (BLOCKER: real, permanent degradation for every existing
   user until they take that manual step) and it warranted an actual fix,
   not just a documented workaround. **Fixed**: `update_index` now detects
   the "symbols exist but `symbol_relations` is completely empty"
   condition once per run and backfills relations for every unchanged
   file's existing symbols before the normal per-file loop runs — using
   already-read source (`current`, the same in-memory map every file's
   content-hash check already built) and the standalone
   `put_symbol_relations_batch`. Self-limiting: once backfilled, every
   symbol has a `symbol_relations` entry (even empty ones, per fix #2
   below), so the cold-table condition is false on every later run.
   Pinned by `a_preexisting_index_with_no_relations_gets_backfilled_without_any_file_edit`,
   which asserts the backfilled file's `reparsed_files` count is 0 — the
   fix must not accidentally make every run reparse everything.
2. **A process interrupted between `replace_file` and the (then-separate)
   relations write could permanently strand stale relations.** The
   original fold-in called `store.replace_file(...)` (its own transaction:
   symbols + `files.content_hash`) and then `store.put_symbol_relations_batch(...)`
   (a second, separate transaction) right after. A crash between the two
   left `symbols`/`content_hash` already reflecting the new content while
   `symbol_relations` still held the old, wrong values — and because the
   hash already matched, no future run would ever reparse that file to
   correct it. This is exactly the class of bug `tests/interrupted_index_recovery.rs`
   exists to catch for symbols/embeddings/meta; relations had the same
   exposure through a different door. **Fixed**: `IndexBackend::replace_file`
   now takes a `relations` parameter and writes symbols, embeddings
   restoration, and relations inside the same transaction — architecturally
   the same fix the embeddings-restoration logic already got when
   `replace_file` was written, just extended to cover the new table. This
   touched ~25 call sites (mechanically — the diff is `symbols)` →
   `symbols, &[])` for every test that doesn't care about relations);
   `put_symbol_relations_batch` survives as a standalone method for the
   backfill path above and for tests injecting a relation onto a
   hand-built symbol without going through a full file replacement.

Both fixes were verified structurally (the transaction boundary is visible
in the code — one `tx.commit()` covers both writes) and behaviorally (full
suite green afterward, including `tests/interrupted_index_recovery.rs`
6/6 and `tests/full_incremental_parity.rs` 1/1 unchanged). Index-time and
storage numbers below were re-measured after both fixes; unaffected
(542ms vs. 541-548ms pre-fix, within run-to-run noise — merging two
transactions into one didn't change the total work, just its grouping).

## What did NOT change

- `context.rs`'s ordering, scoring (`seed.score * 0.4`), hit caps
  (`STRUCTURAL_CALLER_HITS_PER_SEED = 2`, same value as the old
  `AST_GREP_HITS_PER_SEED`), `CONTEXT_EXPANSION_TOTAL`/`CONTEXT_EXPANSION_PER_SEED`,
  relevance floor, budgeted fill, or role ordering.
- `RetrievalMode`'s mode-gating contract (`structural_budget()`) — same
  seed/file caps per mode, same modes skipped/included.
- `index::embed_text` and `content_hash` — `calls`/`bases` are excluded
  from both, confirmed by reading `embed_text`'s body; embeddings are
  bit-for-bit unaffected by this migration.
- The `reasons` string `"ast-grep-caller"` in `ContextItem.reasons` — kept
  verbatim even though ast-grep is gone. It's stable, JSON-exposed
  provenance vocabulary (like `"uses"`, `"test"`, `"imported-definition"`),
  not an implementation reference; renaming it would be an observable
  output change for zero behavioral gain, and an existing committed
  example (`examples/coordinator_benchmark.rs`) pattern-matches on the
  exact string.
- MCP tool surface, CLI subcommands, retrieval weights/fusion constants,
  the reranker hook (still a no-op).

## Acceptance criteria: evidence

**Same structural conformance behavior and fixture recall.**
`tests/precomputed_relations_conformance.rs`: 10/10 (unchanged from the
experiment — the fold-in didn't touch attribution logic).
`examples/structural_migration_evidence.rs`'s fixture-recall check, run
through the *production* `build_context`/`RelationGraph` path on
`fixtures/structural_benchmark.json`'s 4 gold tasks:

```
task                             recall
ts-implementors-retrypolicy       1.000
ts-callers-shouldretry             1.000
py-implementors-notifier          1.000
py-callers-should-retry           1.000
```

**Same bounded-context hit counts/noise as current production.**
`context::tests::bounded_ast_grep_expansion_is_mode_gated` (13/13
`context.rs` tests) and `tests/benchmark_gate.rs` (19/19) pass unchanged —
the same evidence class the two prior experiments used as their
frozen-path proof, now covering the actual production switch instead of an
unused code path.

**Materially lower query-time structural latency.** `RelationGraph::callers_of`
on the 902-file synthetic repo (`scripts/gen_bench_repo.py`, 300
modules/lang): 0.12ms first call (builds+queries the reverse index over
5,112 symbols), 0.003ms warm. The query-time backends this replaced are
deleted, so a same-run A/B isn't possible anymore; citing the
already-recorded numbers against those backends
(`docs/precomputed-structural-relations/README.md`): 45-845ms for the same
whole-repo answer. The latency category is unchanged by folding extraction
into `update_index` — this was always the query-time side of the
comparison, decided in the prior experiment.

**Index-time cost, remeasured after removing the second-read shape.**
Same-run A/B on the same 902-file synthetic repo (temporarily disabling
the fold-in call to get a clean baseline, then restoring it — same
methodology as the binary-size measurement in
`docs/treesitter-structural-eval/README.md`):

```
update_index, no relations at all (baseline):     287ms
update_index, folded-in relations (production):   541-548ms  (3 runs)
delta:                                             +254-261ms (+89-91%)
```

Compare to the experiment's unwired second-pass shape, which added
~252-283ms *on top of* its own baseline measurement in an earlier session
(343-356ms, different session — cross-session timing isn't perfectly
comparable, flagged honestly rather than overclaiming precision). The
folded-in total (541-548ms) is measurably less than what the old two-pass
total would have been (base + second pass, ~595-639ms) — a real but
**modest** win, roughly 8-14%, from eliminating the second file read and
the `store.all_symbols()`/directory-walk reload. This is smaller than the
original experiment's write-up speculated ("would not pay that second full
read+parse... not a floor on what precomputation costs architecturally") —
that claim undersold how much of the cost is the tree-sitter **query**
work itself (parsing + `Query::matches` over ~1,200 relevant files, twice
each), which both the old and new shapes pay equally, since it was never
about disk I/O in the first place. Local SSD reads of already-`open()`-cached
small text files are cheap; tree-sitter parsing and query execution are
not. See "Remaining risks" below for where the real cost reduction
opportunity is.

**Storage.** +188,416 bytes / +1.4% on the same repo — unchanged from the
experiment (same data, different code path writes it).

**No retrieval-weight, embedding, MCP/API, or reranking changes.** Verified
structurally (read `embed_text`, confirmed no reference to `calls`/`bases`)
and behaviorally (`tests/benchmark_gate.rs` 19/19 unchanged,
`context::tests::*` 13/13 unchanged, no `config.rs` constant touched, no
MCP tool signature touched).

## Deletions

`src/structural.rs` (the `ast-grep-core` adapter and
`StructuralSearchProvider` trait), `TreeSitterStructuralProvider` and its
trait impl (`tree_sitter_structural.rs` — the module survives, trimmed to
just the shared extraction functions and their tests),
`structural_relations::index_structural_relations` (the experimental
second pass), the `ast-grep-core` Cargo dependency, and six
now-superseded test/example files: `tests/structural_conformance.rs`,
`examples/structural_backend_compare.rs`, `examples/structural_backend_scale.rs`,
`examples/structural_cost.rs`, `examples/structural_benchmark.rs`,
`examples/precomputed_relations_compare.rs`,
`examples/precomputed_relations_scale.rs`. Replaced by
`tests/precomputed_relations_conformance.rs` (the sole conformance suite
now) and `examples/structural_migration_evidence.rs` (this doc's numbers).

`docs/astgrep-structural-search/`, `docs/astgrep-hardening/`,
`docs/treesitter-structural-eval/`, `docs/precomputed-structural-relations/`,
and `docs/retrieval-coordinator/` are kept as historical record with a
superseded-pointer added at the top of each — not rewritten, since they're
evidence for decisions that were actually made in sequence, not a false
start. `docs/review/structural-and-language.md` and
`docs/review/retrieval-and-config.md` (active review policy, not
historical record) were rewritten for the current architecture — leaving
those stale would have caused future reviews to flag this migration's own
changes as invariant violations.

## Remaining risks

Two risks originally listed here — no backfill for a pre-existing index,
and staleness from an interrupted process between two separate writes —
were upgraded from "documented risk" to "fixed" after review; see the
Codex review section above. What's left:

1. **Index-time overhead (+89-91% on the synthetic repo) is real and
   currently single-threaded.** The relations extraction pass runs in
   `update_index`'s sequential per-file loop (the same loop
   `extract_references` already uses), not the parallel worker-thread pool
   the initial parse stage uses (`std::thread::scope`, 1-4 workers). Since
   the measured dominant cost is the tree-sitter query work itself, moving
   this computation into that parallel stage is the concrete follow-up
   most likely to matter for large repos — not attempted here to keep this
   migration to what was asked (fold in, wire up, prove parity, delete
   duplicates), and worth its own before/after measurement rather than
   folding an unverified change into this one.
2. **Attribution is a name/line heuristic, not a real call graph** — three
   specific ties were found and fixed (see `docs/review/structural-and-language.md`
   LANG-002), each with its own regression test, but a fourth undiscovered
   edge case is plausible in source shapes none of the fixtures or
   synthetic repos happened to exercise. Same risk class the query-time
   backends always had (bare-name matching, no scope analysis) — not
   worse, but also not eliminated by this migration.
3. **`symbol_relations` orphan cleanup only runs on whole-file removal**
   (`remove_files`), matching `embeddings`' existing behavior exactly. A
   symbol deleted via a rename-within-a-still-present-file leaves an
   orphaned relation row (harmless — `load_symbols_with_relations` only
   ever looks up by a *live* symbol's id, so an orphan is inert dead data,
   not a correctness bug) until the next time any file in the repo is
   fully removed. Consistent with existing precedent, not a regression,
   but also not swept more eagerly than embeddings are.

## Verification

`cargo fmt --check`, `cargo clippy -j 2 --all-targets` (warning-free),
`cargo test -j 2` (all suites green, including `tests/benchmark_gate.rs`),
`cargo tree` (confirms zero `ast-grep`/`ast_grep_core` in the resolved
dependency graph).
