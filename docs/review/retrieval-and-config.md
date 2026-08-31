# Retrieval and config review rules

Scope: `src/config.rs`, `src/retrieval.rs`, `src/context.rs`'s use of
`RetrievalMode`, `cli.rs`/`mcp.rs` mode resolution.

---

### RET-001 — Frozen fusion/context weights must not drift
**Severity:** BLOCKER · **Scope:** `src/config.rs` and any literal that
duplicates one of its values elsewhere.

**Invariant:** `FUSION_RRF_K`, `FUSION_LEXICAL_WEIGHT`,
`FUSION_SEMANTIC_WEIGHT`, `EXPANSION_STRONG_SEED_FRACTION`, and every
`CONTEXT_*` constant change only with a fresh canonical benchmark and an
intentional re-baseline — this is `config.rs`'s own doc comment, and
`CLAUDE.md`'s "Before touching retrieval scoring" rule points at
`docs/canonical-baseline.md` as the thing to diff against.

**What constitutes a violation:** a diff touching any value in `config.rs`
(or a hardcoded number elsewhere that has the same ranking effect) without
an explicit statement that this is a benchmark-affecting change, a fresh
`oxide eval` (or `docs/canonical-baseline.md`-style) comparison, and
`tests/benchmark_gate.rs` passing. Also a violation when the weight change
is a side effect inside a PR framed as something else ("refactor X" that
also nudges `FUSION_LEXICAL_WEIGHT") — config drift smuggled into an
unrelated change is exactly as much a regression as a deliberate one, and
harder to catch later.

**Evidence required:** the diff hunk touching `config.rs` (or the
duplicated literal), and whichever of the PR/commit description or an
attached benchmark run shows the re-baseline. If neither is present, that
absence is the finding — don't try to guess whether the new value is
"probably fine."

**Exceptions:** comment-only changes to `config.rs`; adding a new named
constant for a genuinely new, additive feature that doesn't change any
existing value's effect.

---

### RET-002 — Balanced stays the default `RetrievalMode`
**Severity:** MAJOR · **Scope:** `RetrievalMode::resolve`/`default`,
every call site that constructs a `SearchOptions`/`ContextOptions`.

**Invariant:** an unconfigured caller — no `--retrieval-mode`/MCP `mode`
argument, no `$OXIDE_RETRIEVAL_MODE` — always resolves to `Balanced`.
Precedence is explicit flag/arg > env var > `Balanced`, mirroring the
existing embedder-selection precedence in `cli.rs` (`RetrievalMode::resolve`'s
own doc comment states this explicitly).

**What constitutes a violation:** reordering or weakening
`RetrievalMode::resolve`'s precedence chain; a new CLI subcommand or MCP
tool that constructs `SearchOptions`/`ContextOptions` with a hardcoded mode
instead of routing through `resolve()`; changing `RetrievalMode`'s `Default`
impl away from `Balanced`.

**Evidence required:** the call site's mode construction, compared against
`RetrievalMode::resolve`. Confirm
`retrieval_mode_resolve_prefers_explicit_then_defaults_to_balanced` (or its
replacement, if renamed) still exists and asserts this.

**Exceptions:** benchmark/example harnesses under `examples/*.rs` that
intentionally pin a specific mode for measurement — those aren't
agent-facing defaults.

---

### RET-003 — Lexical and semantic scoring must stay concurrent
**Severity:** BLOCKER · **Scope:** `RetrievalEngine::search`'s
`std::thread::scope` block in `retrieval.rs`.

**Invariant:** lexical (`BM25`) and semantic (embed + dot-product) scoring
run as two independently spawned threads inside one `std::thread::scope`,
joined afterward — a request pays `max(lexical_ms, semantic_ms)`, not their
sum. This has to stay `std::thread::scope`, not a tokio task: `oxide
context`/`oxide search` run with no tokio runtime present at all (see
`AGENTS.md`'s load-bearing invariant on this), so a tokio-based rewrite
would need to introduce a runtime into a path deliberately kept synchronous.

**What constitutes a violation:** collapsing the two spawned threads back
into a sequential `lexical.search(...)` then `embed_query(...)` call chain;
wrapping either stage in `tokio::spawn` without threading a runtime through
both the CLI's fully-sync path and MCP's `spawn_blocking` wrapper; changing
`lex_handle.join()`/`vec_handle.join()` from `.unwrap_or_default()` back to
`.unwrap()`, which turns one provider's panic into a failed whole request
instead of degraded-but-successful evidence.

**Evidence required:** the diff to `search()`'s body. If timing-sensitive
code nearby changed, re-run `examples/coordinator_benchmark.rs`'s
`latency_evidence` and confirm it still shows `max()`, not `sum()`,
behavior.

**Exceptions:** none. This is pinned by the dedicated regression-guard test
in `retrieval.rs` (search for "Regression guard for the `std::thread::scope`
refactor").

---

### RET-004 — `RetrievalMode` only gates the bounded structural-relation stage
**Severity:** MAJOR · **Scope:** `RetrievalMode`'s effect throughout
`retrieval.rs`/`context.rs`.

**Invariant:** `Fast`/`Balanced`/`Quality` control only: (a) whether
`context.rs`'s bounded structural-relation expansion (`RelationGraph::callers_of`,
scoped to the seed pool's files — see LANG-001 in
`structural-and-language.md`) runs and its seed/file budget, and (b) whether
`context.rs::rerank_candidates`'s (currently no-op) hook runs (`Quality`
only). `Fast` additionally skips `RetrievalEngine::search`'s own
`RelationGraph` expansion (`opts.expand`). The mode must never gate the
always-on lexical+semantic stage — that stage is unconditional in every
mode, by design. (Historical note: this stage used to be a live
`AstGrepProvider` AST scan; migrated to a precomputed `RelationGraph` lookup
in `docs/precomputed-relations-migration/README.md`. The mode-gating
contract itself — what's gated, what isn't — is unchanged by that
migration.)

**What constitutes a violation:** any code path where `Fast`/`Balanced`
skips or weakens lexical or semantic scoring itself (as opposed to the
structural-relation/rerank stages); a new "cheap mode" concept introduced
elsewhere that duplicates what `RetrievalMode` already owns.

**Evidence required:** cite `RetrievalMode`'s doc comment and
`structural_budget()`/`rerank()`, then point to the specific code path that
diverges from the table those methods define.

**Exceptions:** none stated.
