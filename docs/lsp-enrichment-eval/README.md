# LSP semantic enrichment: evaluation

Optional query-time LSP enrichment (`src/lsp/`, `--lsp` on `oxide query`/
`context`, opt-in on the MCP `query` tool) against Astral `ty` for Python.
This doc records what was measured and what remains open, honestly —
including where this implementation deliberately cut scope from the
originally approved plan for time.

## Setup

- `ty` installed via `uv tool install ty` (version 0.0.80 throughout).
- Fixture: `fixtures/py_repo` (8 files, 54 symbols), indexed with the
  offline hashed embedder (`OXIDE_EMBED_NATIVE=hashed`) — retrieval quality
  isn't the variable under test here, LSP evidence is.
- All numbers below: single-run, one machine, no statistical averaging —
  directional, not a rigorous benchmark. Good enough to answer "is this
  worth the risk," not enough to tune anything.

## Capability probe (grounds the whole design)

A hand-rolled `initialize` handshake against `ty server` (see
`src/lsp/client.rs`'s doc comment and the plan this was built from) found:

- Every capability the task named is real: `callHierarchyProvider`,
  `typeHierarchyProvider`, `referencesProvider`, `definitionProvider`,
  `implementationProvider`, `diagnosticProvider` (3.17 pull model),
  `workspaceSymbolProvider`.
- `positionEncoding` negotiates to `utf-8` when offered — no UTF-16
  code-unit math needed anywhere in this implementation.
- `ty` pushes unsolicited `publishDiagnostics` notifications interleaved
  with ordinary responses on the same stdout stream. A transport that reads
  "the next message" after a request breaks immediately; `transport.rs`'s
  id-keyed dispatch (unit-tested with the exact interleaving reproduced) is
  a direct consequence of this finding, not a defensive guess.

## Relationship precision: does LSP beat the bare-name heuristic?

`fixtures/py_repo`'s `oxidepy/notifiers.py` has a deliberate trap: a comment
mentions `should_retry(x, y)` in a way a bare-name/`uses←` heuristic could
false-positive on. `oxide query --lsp "should_retry retry policy"`:

```
"lsp-caller←RetryPolicy.should_retry"
"lsp-reference←RetryPolicy.should_retry"
```

correctly surfaced the 4 real call sites (`HttpClient.fetch`,
`notify_after_final_attempt`, 2 test methods, via `callHierarchy/
incomingCalls`) and never the comment. `tests/lsp_ty_integration.rs` pins
this. This is the concrete "prefer exact evidence over heuristic" case the
task asked for — not simulated, run against the real server.

As a bonus, unplanned finding: the same query's pack also surfaced a real
bug the fixture didn't know it had — `RetryPolicy.exhausted` (property)
references `self.attempts_left`, which doesn't exist on the class:

```
"lsp-diagnostic(error): Object of type `Self@exhausted` has no attribute `attempts_left`"
```

This is exactly the "why does this fail type checking?" workflow the task
names, produced for free by the same enrichment pass that answered a
retrieval query — diagnostics aren't a separate feature here, just another
evidence source over the same seed pool.

## Scope-then-cap: the repo-wide-response invariant

`tests/lsp_scope_regression.rs`: a common function name (`run`) called from
6 out-of-scope files plus 1 in-scope file, with the in-scope caller sorted
alphabetically last (worst case for a cap-then-scope bug) and a per-seed cap
of 1. Passes against the real server — the in-scope caller survives
regardless of wire order. `src/lsp/enrich.rs::scoped_and_capped` is also
unit-tested in isolation for the same property.

## Query latency (this fixture only — not representative of a real repo)

| | wall time |
|---|---|
| `oxide query` (no `--lsp`) | ~8ms |
| `oxide query --lsp` | ~91ms |

The added ~83ms covers process spawn + `initialize` + `didOpen` + 4 LSP
requests (definition, references, incoming-calls, diagnostics) for 1 Python
seed, sequentially. **Open**: this fixture has 8 files; a real repo's `ty`
`initialize` does real cross-file type resolution and will be slower — this
number is a floor, not a representative figure. Measuring against a
realistic-sized repo is the natural next step before shipping this beyond
an opt-in flag.

## Startup latency and RSS

- Cold `initialize` round-trip on the tiny fixture: ~9ms (this is
  `initialize` alone, not full spawn+handshake+first-request).
- Idle `ty server` process RSS (spawned, before any document opened):
  **~90MB**. Not negligible for a CLI tool whose base RSS is a few MB — this
  is the real cost of "optional": zero when unused, ~90MB+ for the duration
  of one `--lsp` query.

## Failure/fallback behavior

Verified by design and by the transport's unit tests, not yet by killing a
real `ty` mid-session:

- Binary not on `$PATH`: `LspClient::spawn` returns `Err`, `context.rs`'s
  `if let Ok(mut client) = ...` silently skips the whole LSP stage — pack is
  byte-identical to `--lsp` absent.
- `initialize` times out or errors: same fallback path, same result.
- A single request timing out or erroring mid-enrichment: `enrich_seeds`
  wraps every LSP call in `if let Ok(...)`, so one failed request drops only
  that seed's evidence of that kind, never aborts the pass.
- **Not exercised**: a server that crashes (process exits) partway through
  a multi-request enrichment pass for one query. The transport's reader
  thread would send `Closed`, subsequent calls on that `LspClient` would
  return `Err` and their evidence would be skipped the same way — this
  should already be safe by the same `if let Ok` discipline, but there is
  no test that actually kills `ty` mid-flight to confirm it end-to-end.

## Codex review (post-implementation)

Ran `codex exec` against the full uncommitted diff, scoped to the risk list
the task named (scope/fanout, stale state, URI/position mapping,
lifecycle/deadlocks, timeout fallback, capability assumptions, allocator
regressions). Five findings, three fixed:

- **Fixed (real bug) — process leak.** `LspClient::spawn` returning early on
  an `initialize` failure/timeout dropped `Transport` (and its `Child`)
  without ever killing it; `std::process::Child::drop` does not kill on its
  own, so every failed `--lsp` attempt against a hung/broken server leaked
  one `ty server` process. Fixed with `impl Drop for Transport` that kills
  and waits any child not already reaped — a no-op on the normal `close()`
  path (already waited), a real kill on every early-return path. Verified:
  spawning against a nonexistent binary now leaves zero lingering `ty`
  processes.
- **Fixed (real bug) — URI construction wasn't RFC 3986-safe.** `file_uri`
  built `file://` strings with plain `format!`, which `Uri::from_str`'s
  strict parser rejects outright for any path containing a space, `#`, `%`,
  or non-ASCII byte — meaning LSP enrichment silently produced zero evidence
  on any repo with such a path, not a cosmetic issue. Fixed with a minimal
  percent-encode/decode pair (`percent_encode_path`/`percent_decode`);
  `file_uri_round_trips_paths_with_spaces_and_special_characters` pins it.
- **Fixed (real bug) — unanswered server-to-client requests.** The
  transport buffered any non-matching message as a notification, including
  a genuine server-initiated *request* (has both `method` and `id`, e.g.
  `workspace/configuration`). Left unanswered, a spec-compliant server would
  wait indefinitely for that reply, potentially stalling the request this
  client is actually waiting on. Not exercised by `ty` in this evaluation
  (it never sent one), but a real spec-compliance gap the task's "design
  against standard LSP, not ty internals" directive rules out ignoring.
  Fixed: the transport now answers any such request with a
  `MethodNotFound`-style error immediately instead of buffering it forever.
- **Fixed (minor, hardened rather than left as a known gap) — position
  encoding never enforced.** `initialize`'s response was parsed but never
  checked; a server that didn't confirm `utf-8` (including the LSP 3.17
  default of an *absent* `positionEncoding` meaning UTF-16) would have every
  position this client sends silently miscomputed, since no UTF-16
  conversion exists here. Fixed: `LspClient::spawn` now fails closed
  (returns `Err`, same "unavailable" fallback as every other init failure)
  unless the server explicitly confirmed `utf-8`.
- **Accepted as a documented limitation, not fixed** — `didOpen` staleness:
  if a file is modified on disk between two seeds that both touch it within
  the same query, the second seed's *position* is computed from the new
  text while the server still has the version sent at the first `didOpen`.
  Not fixed: this client's lifetime is one query (seconds), the realistic
  window for an external edit landing mid-query is narrow, and adding
  `didChange` tracking is real complexity for an edge case the plan already
  scoped `didOpen`-only for ("no `didChange` support needed — this client
  only ever reads").

All fixes verified: `cargo fmt`, `cargo clippy --all-targets` (clean), full
`cargo test` (all green, including both real-`ty` integration tests
re-run against the stricter encoding check), and the byte-identical
benchmark-gate comparison re-run after the fixes.

## Byte-identical guarantee (flag off)

`oxide eval --config fixtures/benchmark.json`, pre- vs. post-change
(pre-change captured via `git stash` of every tracked file this session
touched, `src/lsp/` left as an orphaned untracked directory): identical
`recall@5`/`precision@5` for both `vector-only` and `hybrid`
(0.818/0.182 and 0.909/0.200), and identical per-task numbers once sorted.
The raw per-task *row order* differs between runs — confirmed to be
pre-existing nondeterminism in the eval harness itself (reproduced between
two consecutive runs of the *same unchanged* binary), not something this
change introduced.

## Deliberate scope cuts from the approved plan (disclosed, not hidden)

- **No MCP `ProcessCache` reuse.** The plan's Task 3 called for a
  `lsp_clients: Mutex<HashMap<PathBuf, Arc<LspHandle>>>` on `oxide mcp`'s
  existing process cache so a warm `ty` session survives across calls.
  This implementation spawns and closes a fresh `LspClient` per
  `build_context_with` call, for both the CLI and MCP paths — correct and
  simple, but every `--lsp` MCP call currently pays full `ty` init latency
  (see the RSS/latency numbers above for what that costs). This is the
  single highest-value follow-up if `--lsp` sees real use: the natural
  shape is a `Mutex<Option<LspClient>>` per repository root, respawned on
  the next call after any failure, no circuit-breaker state needed beyond
  that.
- **No `definition`-at-reference-site resolution** ("where is this value
  actually defined?" for an ambiguous bare-name reference). `enrich_seeds`
  runs `references`/`incoming_calls`/`implementations`/`diagnostics` at a
  seed's own declaration position, which already answers "who calls/uses
  this exact symbol" and "why does this fail type checking" precisely. It
  does not resolve a *seed's own* ambiguous references (e.g. disambiguating
  `RelationGraph`'s `uses←` heuristic edges) — that needs locating a
  specific reference occurrence's position inside a symbol's body, which is
  more code for a workflow the task's example list treats as one of four,
  not the load-bearing one.
- **No `oxide search --lsp`.** `search` uses a separate `SearchOptions`
  path that never reaches `ContextOptions`; wiring it in would need a
  second integration point for no example workflow `oxide query --lsp`
  doesn't already cover.

## Verdict

**Useful, worth keeping as an opt-in flag; not yet worth expanding beyond
Python/`ty`.** The precision delta over the bare-name heuristic is real and
demonstrated on a case built to expose exactly that gap, diagnostics
evidence is a genuinely new capability with zero heuristic equivalent, and
the byte-identical-when-off guarantee holds. The open items before
recommending this for routine use are the process-reuse gap (real latency
cost per call today) and a measurement against a repo large enough for
`ty`'s `initialize` cost to be representative — both are follow-up work,
not blockers to shipping this as `--lsp`.
