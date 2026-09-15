# Evidence coordinator: isolated async evidence layer + post-LSP audit fixes

## Goal

Fix the two MAJOR correctness findings from the 2026-09-15 post-LSP audit
(`review --diff` swallowing invalid ranges, LSP `ProcessCache` document
staleness) and, in the same pass, move independent evidence collection
(structural, Git, LSP, blast-radius) behind an isolated async worker layer
so slow evidence sources overlap instead of serializing — without making
OXIDE's CLI, MCP service API, retrieval core, SQLite/indexing path, or
allocator async. Address the audit's MINOR findings (shared scoping,
LSP lifecycle, capability-fallback coverage) and DOCUMENTATION findings
(AGENTS.md, README, git-aware and LSP docs, canonical baseline framing)
where they're touched by this work. Add a real `oxide lsp install ty`
command so CI can stop silently skipping the LSP real-server tests.

Non-goals: converting retrieval/indexing/allocation to async; changing
ranking weights or allocator constants; adding LSP support for any
language beyond Python/`ty`; running the large agent-outcome benchmark;
tagging a release.

## Architecture

```text
CLI / MCP
    ↓
RepositoryService              (unchanged: synchronous)
    ↓
build_context_with()           (unchanged signature)
    ↓
EvidenceCoordinator::collect() (new: synchronous entry point)
    ↓
  ┌─── OS thread (std::thread::scope) ───┐  ┌─── isolated Tokio runtime ───┐
  │ structural evidence                  │  │ Git evidence (subprocess)    │
  │ blast-radius evidence                │  │ LSP evidence (process I/O)   │
  └───────────────────────────────────────┘  └───────────────────────────┘
                    ↓ (joined, fixed source order)
         deterministic merge → order_note (unchanged)
                    ↓
              existing allocator (unchanged)
                    ↓
                  context
```

**Why structural/blast-radius stay off the Tokio runtime.** Both operate
over `store: &dyn IndexBackend` (`context.rs:147`) and `SymbolSnapshot`'s
`std::cell::OnceCell`-cached data — non-`Send`, tied to the calling
thread's SQLite connection, per the existing AGENTS.md invariant that
`SqliteStore` is not `Sync`. They cannot become Tokio tasks without either
violating that invariant or cloning an owned snapshot first (unnecessary
work for data that's already fast). They run instead on a plain OS thread
via `std::thread::scope`, the same pattern `retrieval.rs` already uses for
`embed_query` — concurrent wall-clock overlap with the async I/O sources,
without pretending async helps CPU/SQLite-bound work.

**New module `src/evidence/`:**
- `coordinator.rs` — `EvidenceCoordinator`, the single synchronous entry
  point `context.rs` calls. Owns the OS-thread scope, drives the runtime,
  joins everything, folds results in one fixed order.
- `runtime.rs` — one isolated Tokio runtime, `OnceLock<tokio::runtime::Runtime>`,
  process-lifetime (safe for one-shot CLI and long-lived `oxide mcp`
  alike; never rebuilt per query). Git/LSP internals stay blocking
  `std::process`/`std::thread` code (unchanged in shape — see below), so
  the coordinator dispatches each as a `spawn_blocking` task and
  `tokio::join!`s them; existing timeouts (`recv_timeout`-based in
  `Transport::call`, already `Duration`-driven in `gitutil`) are untouched
  and don't need `tokio::time`. Feature set is therefore just `rt-multi-thread`
  — no `time`, `process`, or `net` (the last only once a real network
  evidence source exists — not added speculatively now). Runtime built
  with 2 async worker threads and `max_blocking_threads(4)` (only up to
  two blocking sources — git, LSP — run concurrently today; matches the
  shared-machine CPU-budget convention elsewhere in this repo, well under
  Tokio's default 512).
- `contract.rs` — shared `EvidenceRequest`/`EvidenceResult`/`Outcome`/
  `DegradeReason` types (below).
- `src/gitctx.rs` and `src/lsp/*` become evidence *providers* invoked from
  here; their internals (subprocess spawning, JSON-RPC transport) are
  unchanged in shape, only their caller moves.

`context.rs`'s four inline evidence blocks (structural/git/LSP/blast-radius)
collapse into one `coordinator.collect(request)` call feeding the existing
`order_note`/dedup/allocator pipeline, which does not change.

## Evidence contract

```rust
enum EvidenceSource { Structural, Git, Lsp, BlastRadius }

struct EvidenceRequest {
    seeds: Vec<SeedRef>,
    scope_files: Vec<PathBuf>,  // from one shared scoping helper (fixes MINOR-1)
    deadline: Instant,
    fanout: FanoutLimits,       // per-source caps, one shared type, source-specific values
}

struct EvidenceResult {
    source: EvidenceSource,
    candidates: Vec<Candidate>, // existing type, unchanged; providers keep their own scoring/reason conventions
    provenance: Vec<ProvenanceTag>,
    status: EvidenceStatus,
}

enum DegradeReason {
    Timeout { elapsed: Duration, deadline: Duration },
    Unavailable { detail: String },
    ProtocolError { detail: String },
    SubprocessError { exit_code: Option<i32>, stderr_tail: Option<String> }, // bounded tail, structured field only — never spliced into evidence text or raw stdout
    Cancelled,
}

struct Degraded { source: EvidenceSource, reason: DegradeReason, elapsed: Duration }

enum Outcome<T> { Ready(T), Degraded(Degraded) }
```

The contract standardizes shape and execution controls (deadline, fanout,
scope, cancellation), not scoring — each provider keeps its own
scoring/reason-string conventions (`lsp-caller←...`, git's tags).

## Shared scoping helper (fixes MINOR-1)

One `scope_files_from_seeds(seeds, max_files) -> Vec<PathBuf>` replaces the
three independent implementations in `context.rs` (structural capped to
3/6 files via `RetrievalMode::structural_budget()`; git/LSP previously
uncapped except by `CONTEXT_MAX_CANDIDATES=16`). Any source that still
needs a different bound documents why in a comment at its call site rather
than diverging silently.

## Determinism

`collect()` runs on the calling thread: starts the OS-thread scope, drives
Git+LSP concurrently via `runtime.block_on(async { tokio::join!(...) })`,
joins the OS thread, then folds all four `EvidenceResult`s into
`order_note` in a **fixed hardcoded source order**
(`Structural → Git → LSP → BlastRadius`, matching today's code order) —
never insertion or completion order. Since `order_note`'s only
completion-order-sensitive behavior is first-seen provenance and float
summation order, pinning the fold order preserves today's byte-identical
output regardless of which provider actually finishes first.

**Test**: a `cfg(test)`-only artificial-delay wrapper deliberately varies
which provider finishes first across repeated runs of the same query;
assert byte-identical `--json` output across all orderings.

## Failure semantics

`collect()` never returns `Err` for a degraded optional source — a
`Degraded` entry contributes nothing to the merge and surfaces under a new
`diagnostics` field in `--json` output (previously invisible: a git
failure today is silent). This applies only to the query/context path.

`oxide review --diff` is a separate, CLI-only, explicit-user-request path
that never touches `EvidenceCoordinator`. Core retrieval failure (empty/
erroring seed pool) is unchanged, upstream of evidence collection.

## Audit fix: `review --diff` invalid ranges (MAJOR-1)

`gitutil::diff_text`/`gitctx::build_git_context`/`review.rs` stop
swallowing git errors via `.unwrap_or_default()`. An invalid or
nonexistent range propagates a real error through `RepositoryService::review`,
surfaced as `review_failed`. A fresh single-commit repository's implicit
`HEAD~1` (the CLI default) is detected specifically and reported as "no
prior commit to diff against" rather than a silent empty review.

**Tests**: `oxide review --diff nonexistent-ref` asserts `review_failed`;
a single-commit fixture repo with no explicit `--diff` asserts the
truthful no-prior-commit message.

## Audit fix: LSP ProcessCache staleness (MAJOR-2)

`LspClient` tracks a content hash per opened file instead of a bare
presence `HashSet<String>`. `ensure_open` behavior:
- never opened → `textDocument/didOpen` (unchanged)
- opened, content unchanged → no-op (unchanged)
- opened, content changed → full-document `textDocument/didChange`
  (`TextDocumentSyncKind::Full`, whole new text, incremented version) —
  spec-correct per LSP 3.17 (a second `didOpen` without an intervening
  `didClose` is not permitted; full-sync `didChange` is the correct
  "this document changed" signal and needs no incremental diffing, which
  the client deliberately still doesn't implement).

This closes the staleness gap for both the CLI's spawn-per-call sessions
and the MCP `ProcessCache`'s cross-call-reused sessions, without adding
`didClose` cycling or incremental sync complexity.

**Test**: two `service.context(...)` calls against the same cached
session with a real file edit between them; assert the second call's
evidence reflects the new content, not the first `didOpen`'s snapshot.

## LSP lifecycle (fixes MINOR-2)

`ProcessCache.lsp_clients` gets a small cap,
`LSP_MAX_CACHED_SESSIONS` (`config.rs`, default `4`, overridable via
`OXIDE_LSP_MAX_CACHED_SESSIONS` — matches the existing
`OXIDE_CONTEXT_MAX_PRIMARIES` override convention), plus a last-used
timestamp per slot, checked lazily on access — no background reaper
thread. Over the cap, the least-recently-used *idle* session is evicted
before a new one spawns. The existing `is_alive()` non-blocking check
remains the "usable" gate; a dead/wedged slot is still detected and
respawned the same way, just now bounded. Hard request deadlines are
unchanged.

Deliberately not addressed: detecting a wedged-but-alive server before its
next request deadline fires (would need a heartbeat or blocking `wait`,
both more supervision machinery than this scope calls for).

## `oxide lsp install <name>` (new, small)

Same spirit as `oxide install` for agent integrations: a registry of
known LSP-server installers. Only `ty` gets a real entry (`uv tool
install ty==<pinned version>`) since it's the only language with a
working `LspClient` profile today; other names (`pyright`, `clangd`, ...)
return a clear "not supported yet" error rather than installing a binary
OXIDE can't use for enrichment, or silently no-op-ing. Registry designed
to be a one-entry addition when another language gets a real profile —
no speculative stubs for unimplemented languages.

The runtime (`--lsp` on `query`/`context`) is unchanged by this: it only
ever *checks* for a server on `PATH` and degrades if absent — it never
installs anything itself.

## Capability-fallback coverage (fixes MINOR-3)

A deterministic protocol-fixture mock (a minimal fake JSON-RPC server,
not a second real LSP binary) whose `initialize` response withholds an
optional capability (e.g. no `referencesProvider`); asserts OXIDE degrades
that evidence cleanly rather than erroring the whole pass. Covers the gap
without depending on `ty`'s specific capability set.

## CI

New required `lsp-integration` job: runs `oxide lsp install ty` (pinned
version, dogfooding the new command) as its setup step, then runs the
three currently-skipped real-server tests as an actual required gate —
isolated into its own job so a slower network install doesn't block the
existing fast `quality`/`test` jobs. This does not change OXIDE's own
runtime behavior: `--lsp` still only checks for a server on `PATH` and
degrades if absent, never installs anything itself.

Interleaving/capability-fallback/URI/position-encoding edge cases stay as
existing or new deterministic unit/protocol-fixture tests — CI-independent
of any real server, real-`ty`-independent.

## Compatibility gates

- Default, git-disabled, lsp-disabled `oxide query --json` → byte-identical
  snapshots against pre-refactor output, in addition to the unchanged
  `oxide eval --config fixtures/benchmark.json` gate (weights/constants
  untouched).
- Git-enabled deterministic fixture → pinned expected JSON on a fixed
  small repo (extends `tests/git_context_e2e.rs`).
- LSP-enabled deterministic fixture → pinned against the protocol-fixture
  mock, not real `ty` (real-`ty` version drift would make byte-identical
  assertions fragile); the existing real-`ty` integration test keeps its
  current "surfaces the right callers" property check, not exact JSON.
- New: the completion-order determinism test (above).

## Performance evaluation

Before touching code: capture wall time, per-source collection time (now
free from `EvidenceResult`/`Degraded.elapsed`), RSS, process count, and
`--json` output for base/git-enabled/lsp-enabled/git+lsp-enabled queries
against current HEAD. Repeat the same matrix after the refactor lands.
Write both up in `docs/evidence-coordinator-refactor/README.md` (matching
the `docs/lsp-enrichment-eval/`, `docs/git-aware-context/` convention),
stating the `old ≈ structural + git + lsp` vs. `new ≈ max(independent
waits) + merge overhead` relationship only if the numbers actually
support it.

## Documentation cleanup (in the same pass)

- `AGENTS.md`: language list corrected to the current 10 groupings;
  "Embeddings / providers" section gains the remote-provider
  resolution/consent step.
- `README.md` (finish and commit the in-progress rewrite): document
  `--git`, `--lsp`, Voyage/Jina/OpenAI-compatible remote providers, and an
  explicit privacy warning that a configured remote provider sends code to
  a third-party API — the current Privacy section's language is written
  entirely around the pre-existing self-hosted endpoint.
- `docs/git-aware-context/README.md`: add untracked-file exclusion and
  pure-rename-without-hunks as stated Limitations; note Git-enabled
  retrieval quality remains unvalidated by any agent-outcome benchmark.
- New architecture note (`docs/evidence-coordinator-refactor/README.md` or
  a short addition to `context-engineering-notes.md`): duplicate/
  cross-source score accumulation is a property of `order_note` generally
  (all four sources), not a git-specific caveat — stated once, not
  per-source.
- `docs/lsp-enrichment-eval/README.md`: remove/update the "this client's
  lifetime is one query (seconds)" framing now that `ProcessCache` and the
  didChange fix change the actual lifetime and staleness story.
- `docs/canonical-baseline.md`: add a header line stating explicitly that
  it is a historical snapshot (2026-08-27, pre-git/LSP/remote-embeddings),
  not a current full-repo rerun — no new rerun is performed as part of
  this work.

None of these claim Git or LSP improves agent outcomes.

## Commits

1. Audit correctness fixes: `review --diff` error propagation, LSP
   didChange-based staleness fix, LSP session cap/eviction — independently
   testable, independently revertable, no architecture change yet.
2. `EvidenceCoordinator` / isolated async runtime refactor: new
   `src/evidence/` module, `context.rs` call-site collapse, determinism
   test, compatibility-gate fixtures.
3. Lifecycle and CI hardening: `oxide lsp install ty`, capability-fallback
   protocol-fixture test, `lsp-integration` CI job.
4. Documentation cleanup: all items above.

Codex review runs against the completed state (all four commits) before
final report, focused on: runtime leaks, nested Tokio runtimes, blocking
calls on executor threads, nondeterministic candidate merge, cancellation/
process leaks, stale LSP documents, Git error swallowing, SQLite/thread-
safety assumptions, altered scoring from completion order, `ProcessCache`
lifetime, shutdown behavior. No release tag.

## Verification

`cargo fmt --check`, `cargo clippy --all-targets -- -D warnings`,
`cargo test`, `cargo build --release`,
`oxide eval --config fixtures/benchmark.json` (frozen gate, unchanged
weights/constants) — run after each commit, not just at the end.
