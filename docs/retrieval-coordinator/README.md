# Concurrent evidence providers + retrieval modes

> **Partially superseded.** The "bounded ast-grep expansion" evidence below
> describes the live `AstGrepProvider` AST-scan stage that used to sit
> behind `context.rs`'s bounded expansion; that stage now reads from a
> precomputed `RelationGraph` lookup instead (`AstGrepProvider`/
> `src/structural.rs` are gone) — see
> `docs/precomputed-relations-migration/README.md`. The mode-gating
> contract, the file-scope bounding rationale, and the coordinator/threading
> design this doc evidences are unchanged by that migration. Kept as-is
> below for historical record; read the migration doc for what's current.

Exit evidence for the coordinator refactor. Architecture summary lives in
the `AGENTS.md` load-bearing-invariants entries added alongside this doc;
this file is the raw benchmark data and the reasoning behind the design
choices that aren't self-evident from the code.

## Architecture

Three changes to the existing pipeline, in place — no new indexing-time
architecture, per the brief:

1. **Lexical and semantic scoring run concurrently.** `RetrievalEngine::search`
   used to do `lexical.search()` then `embedder.embed_query()` serially. It
   now loads the (lazily-cached) embeddings vector map synchronously — cheap
   on a cache hit, and the one operation that's actually fallible
   (`store.all_embeddings()`) — then spawns both stages on separate OS
   threads via `std::thread::scope` and joins both. A request now pays
   `max(lexical_ms, semantic_ms)` instead of their sum. Controlled proof (a
   `HashedEmbedder` wrapped with an artificial 150ms `embed_query` delay):
   observed end-to-end search latency was 150ms, not 150ms + lexical — see
   `examples/coordinator_benchmark.rs`'s `latency_evidence`.

   `std::thread::scope`, not a tokio task, because `cli.rs::cmd_context`
   (the `oxide context`/`oxide search` CLI path) runs fully synchronously
   with **no tokio runtime at all** — `run_mcp`'s own comment says a runtime
   is built "only for the `mcp` subcommand, so every other CLI command stays
   fully synchronous." MCP's `context`/`search` handlers already wrap the
   whole synchronous service call in `tokio::task::spawn_blocking`. Plain OS
   threads work identically and portably from both call sites; a tokio task
   would need a runtime present in the fully-sync CLI path, which is exactly
   the cost `run_mcp`'s design was written to avoid paying everywhere else.

2. **Bounded ast-grep expansion, wired into `context.rs`'s existing
   expansion loop.** `build_context` already ran its own RelationGraph
   expansion pass (parent/child/uses/imported-definition/test — identifier-
   name-based, no scope analysis). A second pass now runs alongside it,
   anchored on the same top seeds, calling `structural.rs`'s
   `AstGrepProvider::find_callers` — an AST-precise relation RelationGraph
   cannot express at all (a caller with no other traceable relation to the
   seed, e.g. no shared identifier, is invisible to it). File scope is the
   union of the seed pool's own files, capped at a mode-dependent count —
   matching `structural.rs`'s documented contract ("scoped to an explicit
   file list — the files of already-retrieved symbols — never a whole-repo
   scan") exactly, not a per-seed RelationGraph-neighbor lookup. Skipped
   entirely in `Fast` mode; every hit still goes through the existing
   `Candidate`/`order_note` merge, so it accumulates onto an existing
   candidate's score/reasons rather than creating a parallel data path.

3. **A reranker hook, not a reranker.** `context.rs::rerank_candidates` is a
   real function with the signature a scoring reranker needs (`query`, `&mut
   [Candidate]`), called once — after dedup/subsumption, before role
   ordering — only when `RetrievalMode::Quality`. Its body is empty. Wiring
   a real cross-encoder or LLM reranker later is a body change at this one
   call site, not new plumbing, and it can only ever adjust `Candidate.score`
   — role ordering, the relevance floor, and packing all already consume
   `score` as data, so nothing downstream needs to change to support it.

### Common candidate/evidence representation

The task asked for one; the codebase already had it and this refactor
extends rather than replaces it. Every hit — lexical, semantic, the existing
RelationGraph relations, and now ast-grep — funnels into the same
`(symbol id, score contribution, human-readable reason)` triple, accumulated
per symbol id (`retrieval.rs`'s `reasons: HashMap<u64, Vec<String>>` /
`context.rs`'s `Candidate.reasons` + `order_note`'s merge-by-id). A prior
provenance-audit doc comment in `retrieval.rs` (`RelationGraph::neighbors`)
had already tiered these into Direct (`lexical=`/`semantic=`) / Resolved
(`parent←`/`imported-definition←`, backed by parsed structure) / Heuristic
(`uses←`/`test←`, identifier-name intersection) — this refactor adds
`ast-grep-caller←` as a fourth tier: **AST-verified**, stronger than
Heuristic (a real call expression, not a name match) but scoped to a bounded
file list rather than the whole repo, unlike Resolved relations which are
exact wherever they fire. Introducing a real `Provenance` enum was
considered and rejected: nothing downstream branches on the tier today
(every consumer just reads the reason strings), so a typed enum would be
speculative machinery with no caller — exactly what the task's own "no
unrequested abstractions" framing (and this repo's own ponytail-mode
default) argues against. If a future consumer needs to branch on
provenance tier programmatically, promoting the prefix convention to an enum
is a small, localized change.

## Mode semantics

| Mode | Lexical+semantic | RelationGraph expansion | Bounded ast-grep | Reranker |
|---|---|---|---|---|
| `Fast` | concurrent (always) | skipped | skipped | no |
| `Balanced` (default) | concurrent (always) | on | on, 2 anchor seeds × 3 files | no |
| `Quality` | concurrent (always) | on | on, 3 anchor seeds × 6 files | yes (no-op) |

Resolution order, mirroring the existing embedder-selection precedence in
`cli.rs` exactly: explicit `--retrieval-mode` flag / MCP `mode` argument >
`$OXIDE_RETRIEVAL_MODE` > `Balanced`. An unconfigured agent — no flag, no
env var — always gets `Balanced`; this is asserted directly
(`retrieval_mode_resolve_prefers_explicit_then_defaults_to_balanced`, `retrieval.rs`).
An explicit but unparseable value fails loudly (`CliError`/`McpError`,
matching how `--mode` for `SearchMode` already behaves) rather than
silently falling back — silently doing the wrong thing on a typo was
explicitly out of scope.

Naming note: `oxide search` already had a `-m/--mode` flag for
`SearchMode` (lexical/semantic/hybrid) before this change. `RetrievalMode`
reuses that word in its own vocabulary ("retrieval mode"), so the new flag
is `--retrieval-mode` everywhere (CLI) and `mode` in MCP tool arguments
(where no prior `mode` argument existed, so no collision). `SearchRequest`
and `ContextOptions` both gained a `retrieval_mode` field; `RepositoryService::context`
gained a third parameter. `search()`'s `SearchOptions.retrieval_mode` only
has an effect through the `Fast`-skips-RelationGraph-expansion rule above —
bounded ast-grep expansion is a `context.rs`-only concept, since raw
`search()` has no candidate-pool/packing stage for it to feed into.

## Benchmark evidence

`examples/coordinator_benchmark.rs` (`cargo run --release --example
coordinator_benchmark`), against the committed `fixtures/py_repo`/
`fixtures/ts_repo` and `fixtures/structural_benchmark.json` (Phase 3.4b's
fixture, reused as-is):

```
== concurrency ==
embed_query delay=150ms, observed search latency=150ms
PASS: latency tracks max(lexical, semantic), not their sum.

== RetrievalMode effect on build_context ==
task                             mode     recall   tokens latency_ms   ast_grep
ts-callers-shouldretry           Fast      0.000      389       1.15          0
ts-callers-shouldretry       Balanced      0.000      389      12.91          0
ts-callers-shouldretry        Quality      0.000      389      23.77          0
py-callers-should-retry          Fast      1.000      756       1.57          0
py-callers-should-retry      Balanced      1.000      809      20.08          1
py-callers-should-retry       Quality      1.000      909      52.01          3

== provider contribution (Balanced, all 4 tasks) ==
  semantic                         21
  lexical                          19
  relationgraph-test               2
  ast-grep-caller                  1
```

Reading this honestly, not just favorably:

- **Latency**: the concurrency change is unconditionally a win — confirmed
  by direct measurement, not inference. Balanced/Quality's higher latency
  numbers above are the ast-grep stage's real, expected cost (file I/O +
  re-parse), not a regression from the concurrency change; `Fast` mode's
  1-2ms numbers show the always-on lexical+semantic stage itself is cheap
  regardless of mode.
- **`py-callers-should-retry`**: recall was already 1.000 at `Fast` (the
  gold caller was independently reachable via lexical/semantic match on this
  small fixture), so the ast-grep additions (1 at Balanced, 3 at Quality)
  don't move recall here — but they do add distinct, independently-verified
  provenance (`ast-grep-caller←should_retry`) to items that were previously
  included on lexical/semantic grounds alone. On a query where the caller
  is *not* independently reachable, the gain is a pack item that wouldn't
  exist at all under the old code — the isolated unit test
  `bounded_ast_grep_expansion_is_mode_gated` constructs exactly that case:
  a caller whose `references` deliberately don't mention the callee, so
  `RelationGraph` has nothing to find it by, and confirms it only appears
  under `Balanced`/`Quality`.
- **`ts-callers-shouldretry`**: recall stayed 0.000 at every mode — a real,
  worth-stating limitation, not a hidden failure. This task's `text` field
  in the committed fixture is a copy of the *implementors* task's text ("the
  retry policy interface every backoff strategy must implement"), which
  never mentions `shouldRetry`. Bounded ast-grep expansion is anchored on
  the top seeds *for the actual query* — if the target symbol doesn't score
  well enough to become a seed, it's never chosen as an anchor, so its
  callers are never searched for, by design (`structural_budget()`'s "max
  anchored seeds" bound, and the "anchored on early high-confidence
  results" instruction this task was given). This is the same shape of
  ceiling as `RelationGraph`'s "no dependency graph, only what a few seeds'
  neighbors surface" — bounded evidence is not exhaustive evidence.
- **Provider contribution** across all 4 tasks: lexical and semantic
  dominate (40 of 43 evidence tags) — expected, since they're the
  always-on, unconditional stage. `ast-grep-caller` contributed 1 tag on
  this small fixture; `structural_benchmark.json` only has 2 "callers"
  tasks (the other 2 are "implementors," an intent this refactor did not
  wire in — see below), so this number is necessarily small on this
  fixture. It is real evidence a fully-lexical/semantic/RelationGraph
  pipeline could not have produced, not inflated by double-counting
  (`order_note` merges by symbol id).

### What wasn't benchmarked, and why

- **`find_implementors`** was not wired into `context.rs`. The task asked
  for "bounded graph/ast-grep expansion" without mandating a specific
  intent; `find_callers` was chosen because "who calls this" generalizes
  more directly to arbitrary function/method seeds (the common case for a
  natural-language task query) than "what implements this interface" does,
  which only fires for type/interface-shaped seeds. Wiring
  `find_implementors` in as a second, symmetric expansion source is a small,
  additive follow-up (same shared file scope, same seed-anchoring, a second
  `AstGrepProvider` call per anchor seed) — not attempted here to keep this
  change's blast radius to what was actually evidenced.
- **"Agent/tool-call reduction"** has no existing instrumentation in this
  repo (no agent-loop harness that counts tool calls per task), and building
  one was out of scope for this pass. The `bounded_ast_grep_expansion_is_mode_gated`
  test and the `py-callers-should-retry` row above are the honest proxy:
  when the pack already includes a caller relationship, an agent that would
  otherwise have needed a follow-up `find_callers`-shaped search doesn't —
  but "packs a symbol that used to require a follow-up call" was not
  converted into a measured call-count delta against a real agent loop.

## Failure / degradation behavior

- **Embedder failure**: unchanged and already graceful — `embed_query`
  is infallible by existing design (`AGENTS.md`: "HTTP failures return empty
  vectors by design"); a failed HTTP call yields empty/mismatched vectors
  that the existing length/emptiness check already discards, degrading to
  lexical-only influence on the fused score. Concurrency doesn't change this
  contract, it just runs the (possibly slow) call in parallel with lexical
  scoring instead of after it.
- **`store.all_embeddings()` failure** (new): previously propagated via `?`
  and failed the whole `search()` call. Now caught and treated as "no
  semantic evidence for this query" — logged nowhere yet (no logging
  framework in this codebase), but no longer fatal. A corrupt or
  transiently-unreadable embeddings table now degrades to lexical-only
  instead of erroring the whole request.
- **A panicking provider thread** (new, defensive): `lex_handle.join()`/
  `vec_handle.join()` use `.unwrap_or_default()` rather than `.unwrap()` —
  a panic in one provider (which none of the current code paths should ever
  do, but `std::thread::scope` makes this an explicit possibility that
  serial code didn't have to consider) degrades to empty evidence from that
  provider rather than propagating the panic across the thread boundary and
  failing the whole request.
- **True mid-flight timeout/abandonment of a slow provider was not
  implemented**, and this is a deliberate, scoped decision, not an
  oversight: `std::thread::scope` blocks until every spawned thread
  finishes, by design (that's what makes the borrows inside it sound) — a
  `recv_timeout`-style "give up and move on" requires genuinely detached,
  `'static` threads, which would mean `RetrievalEngine` owning (`Arc`-wrapped)
  its embedder and vector cache instead of borrowing them, a real
  structural change to a heavily test-pinned struct. The concrete risk this
  would guard against — an `HttpEmbedder` call hanging far longer than
  `Fast` mode should tolerate — is already bounded by the HTTP client's own
  120-second timeout (`embeddings.rs`), which is a real but generous ceiling,
  not "unbounded." Flagged here as a legitimate follow-up rather than
  silently declared solved.

## Keep / revise recommendation

**Keep.** All 3 changes compile clean under `clippy -D warnings` (no
suppressions), all existing tests pass unmodified in behavior (0 test
assertions changed, only `SearchOptions`/`SearchRequest`/`ContextOptions`
call sites updated for the new field), `tests/benchmark_gate.rs` — the
frozen hybrid-vs-vector-only regression gate — still passes, and the
concurrency benefit is measured, not asserted. The one intentionally
deferred piece (mid-flight provider timeout/abandonment) has a real, if
generous, existing bound and is called out rather than glossed over. The
`find_implementors` gap and the tool-call-reduction metric gap are both
named as follow-ups with a clear reason they weren't attempted in this pass,
not silently dropped requirements.
