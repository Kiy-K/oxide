//! Fixed production retrieval and context-allocation settings.
//!
//! This is the sole source for values that affect ranking or packed context.
//! Change one only with a fresh canonical benchmark and an intentional
//! re-baseline.

pub(crate) const FUSION_RRF_K: f32 = 60.0;
pub(crate) const FUSION_LEXICAL_WEIGHT: f32 = 0.6;
pub(crate) const FUSION_SEMANTIC_WEIGHT: f32 = 0.4;
pub(crate) const FUSION_CANDIDATE_LIMIT: usize = 200;
pub(crate) const EXPANSION_STRONG_SEED_FRACTION: f32 = 0.55;

pub(crate) const CONTEXT_DEFAULT_BUDGET_TOKENS: usize = 4096;
pub(crate) const CONTEXT_MAX_CANDIDATES: usize = 16;
pub(crate) const CONTEXT_CHARS_PER_TOKEN: f32 = 4.0;
pub(crate) const CONTEXT_ITEM_OVERHEAD_TOKENS: usize = 12;
pub(crate) const CONTEXT_PER_ITEM_TOKEN_CAP: usize = 350;
pub(crate) const CONTEXT_RELEVANCE_FLOOR_FRACTION: f32 = 0.15;
pub(crate) const CONTEXT_EXPANSION_PER_SEED: usize = 2;
pub(crate) const CONTEXT_EXPANSION_TOTAL: usize = 2;
pub(crate) const CONTEXT_MAX_ITEMS_PER_FILE: usize = 2;
/// Overridden only via `$OXIDE_CONTEXT_MAX_PRIMARIES` for the primary-cap
/// sensitivity experiment (docs/primary-cap-sensitivity/README.md); unset is
/// byte-identical to this value. Promoting a different number to the shipped
/// default here requires the same fresh canonical-benchmark re-baseline as any
/// other constant in this file.
pub(crate) const CONTEXT_MAX_PRIMARIES: usize = 5;
pub(crate) const CONTEXT_MAX_TESTS: usize = 1;

/// Term-coverage corroboration boost (experiment, see
/// docs/term-coverage-eval/README.md). `0.0` is a no-op — byte-identical to
/// pre-experiment fusion scoring — and is overridden only via
/// `$OXIDE_TERM_COVERAGE_ALPHA` for the experiment itself. Promoting a
/// nonzero value to the shipped default here requires the same fresh
/// canonical-benchmark re-baseline as any other constant in this file.
pub(crate) const TERM_COVERAGE_ALPHA_DEFAULT: f32 = 0.0;

/// Caps the term-coverage bonus as a fraction of the current query's top
/// fused score: `bonus = alpha * coverage * top_score * this`. The 21-task
/// corroboration sweep (docs/term-coverage-eval/README.md) found the
/// original multiplicative `1 + alpha*coverage` reweight let corroboration
/// overpower a dominant exact-identifier match starting at alpha=0.2 — a
/// candidate whose own score was far behind the leader could still win
/// outright once its coverage share was large enough, because the boost
/// scaled with *that candidate's own* score, not with how far behind it
/// was. Bounding the bonus to a small fraction of the *leader's* score
/// instead means a leader whose margin over the runner-up exceeds the
/// largest possible bonus (`alpha * this`, since coverage is clamped to
/// [0,1]) can never be dethroned by coverage alone, while still being able
/// to break near-ties in favor of genuine multi-term corroboration.
pub(crate) const TERM_COVERAGE_MAX_BONUS_FRACTION: f32 = 0.15;

/// Blast radius (`--blast-radius`): the bounded impact neighborhood of a
/// query's top seeds. Every value here is a hard cap, not a tuning knob —
/// `RelationGraph::callers_of`/`implementors_of` are repo-wide by
/// construction (AGENTS.md), so these are what stop a neighborhood from
/// becoming a graph dump. See `src/blast_radius.rs` for why this feature
/// cannot reuse `context.rs`'s seed-pool file scope and carries its own.
///
/// All of it is inert unless the caller opts in: with the flag absent, no
/// lookup runs, no snapshot is loaded for it, and output is byte-identical.
pub(crate) const BLAST_RADIUS_MAX_SEEDS: usize = 3;
/// **Direct** members per seed (callers, implementors, tests). The
/// transitive hop is budgeted separately by [`BLAST_RADIUS_TRANSITIVE_MAX`]
/// and does *not* count against this, so one seed's worst case is
/// `BLAST_RADIUS_PER_SEED + BLAST_RADIUS_TRANSITIVE_MAX` members, and
/// `BLAST_RADIUS_MAX_ITEMS` is the only cap on the whole neighborhood.
///
/// Deliberate, and the same shape `context.rs`'s own
/// `STRUCTURAL_CALLER_HITS_PER_SEED` already takes against
/// `CONTEXT_EXPANSION_PER_SEED`: an independently-bounded evidence source
/// gets its own budget rather than competing for another one's. Folding the
/// hop into this cap would starve it exactly when there are four direct
/// callers — and the hop's whole value is reaching past them.
pub(crate) const BLAST_RADIUS_PER_SEED: usize = 4;
/// The hard ceiling on a whole neighborhood, across every seed and both
/// distances. Unlike the per-seed caps this one is absolute.
pub(crate) const BLAST_RADIUS_MAX_ITEMS: usize = 12;
pub(crate) const BLAST_RADIUS_MAX_FILES: usize = 8;
/// The single transitive hop's own budget, across all seeds. Separate from
/// [`BLAST_RADIUS_PER_SEED`] on purpose — see its note.
pub(crate) const BLAST_RADIUS_TRANSITIVE_MAX: usize = 3;

/// How many blast-radius members reach `build_context`'s candidate pool.
/// Deliberately far below `BLAST_RADIUS_MAX_ITEMS`: in `oxide query` these
/// compete for the same token budget as the primaries, so the pack takes
/// the most direct few rather than the whole neighborhood. `oxide search`
/// attaches the full bounded list instead — it carries no snippets there.
pub(crate) const BLAST_RADIUS_CONTEXT_ITEMS: usize = 4;

/// Score a blast-radius candidate receives in the context pack, as a
/// fraction of its seed's. Below the 0.4 the two existing expansion sources
/// use, so an opt-in extra evidence source can break into the pack but
/// never outranks the structural expansion that was already there — and
/// above the relevance floor (0.15), so a direct caller of the top hit is
/// not immediately dropped as a weak tail.
pub(crate) const BLAST_RADIUS_SCORE_FRACTION: f32 = 0.35;

/// Git-aware context (`--git` on `oxide query`, always-on for `oxide
/// review`): current diff, recent commits, and bounded co-change history —
/// see `src/gitctx.rs`. Every value here is a hard cap on local `git`
/// subprocess calls, not a tuning knob: co-change in particular is capped
/// on commits-per-file scanned, files-per-commit tolerated (a mass
/// reformat/dependency-bump commit is noise, not signal), and how many
/// changed files get co-change computed at all, so subprocess count never
/// scales with diff size. No index-time precompute in v1 — measure first.
///
pub(crate) const GIT_RECENT_COMMITS_LIMIT: usize = 10;
/// Commits scanned (`git log -n`) per file when computing co-change.
pub(crate) const GIT_COCHANGE_COMMIT_WINDOW: usize = 50;
/// A commit touching more files than this is dropped from the co-change
/// tally entirely, not truncated — a partial file list from a mass
/// reformat/dependency bump is still noise.
pub(crate) const GIT_COCHANGE_MAX_FILES_PER_COMMIT: usize = 20;
/// How many of the current diff's changed files get co-change computed at
/// all, sorted by path first — bounds subprocess count regardless of how
/// large the diff is.
pub(crate) const GIT_COCHANGE_MAX_TARGET_FILES: usize = 5;
/// Top co-changed files kept per target file, sorted `(count desc, path
/// asc)` before truncating — most pairs co-change once, so the tie-break is
/// load-bearing for determinism, not cosmetic.
pub(crate) const GIT_COCHANGE_MAX_FANOUT: usize = 5;

/// Symbols pulled per co-changed file into the context pack (first
/// non-module symbols by declaration order).
pub(crate) const GIT_COCHANGE_SYMBOLS_PER_FILE: usize = 2;
/// Changed-symbol candidates admitted to the context pool.
pub(crate) const GIT_CHANGED_CONTEXT_ITEMS: usize = 8;
/// Callers/tests pulled per changed symbol via the same `RelationGraph`
/// structural expansion already built for the query's seeds.
pub(crate) const GIT_NEIGHBOR_HITS_PER_CHANGED: usize = 2;

/// Score fractions (of the top seed's score), all below
/// [`BLAST_RADIUS_SCORE_FRACTION`] (git evidence stays lower priority than
/// direct/structural evidence, per design) and all above
/// [`CONTEXT_RELEVANCE_FLOOR_FRACTION`] (0.15) so they don't get silently
/// floored out of every query that also has one strong primary. Three
/// tiers, not one: "changed in the current diff" is a fact, "callers of a
/// changed symbol" is one structural hop removed from that fact, and
/// "historically co-changed" is a heuristic — co-change is deliberately the
/// lowest of the three.
pub(crate) const GIT_CHANGED_SCORE_FRACTION: f32 = 0.28;
pub(crate) const GIT_NEIGHBOR_SCORE_FRACTION: f32 = 0.20;
pub(crate) const GIT_COCHANGE_SCORE_FRACTION: f32 = 0.16;

/// Optional LSP semantic enrichment (`--lsp`, `src/lsp/`): definitions,
/// references, call/type hierarchy, and diagnostics sourced from a real
/// language server (Astral `ty` for Python first). Off by default and
/// zero-cost unless a caller opts in — every value here is a hard cap on a
/// repo-wide-by-construction LSP response (`textDocument/references`,
/// `callHierarchy/incomingCalls`, `textDocument/implementation`), the exact
/// same discipline [`BLAST_RADIUS_MAX_SEEDS`]/`RelationGraph::callers_of`
/// already carry (see AGENTS.md).
pub(crate) const LSP_MAX_SEEDS: usize = 3;
/// Cap per evidence kind (callers, references, implementations, diagnostics)
/// per seed, applied AFTER intersecting with the seed pool's file scope —
/// see `lsp::enrich`'s module doc for why the order matters.
pub(crate) const LSP_PER_SEED_ITEMS: usize = 3;
/// Score a piece of LSP evidence receives in the context pack, as a fraction
/// of its seed's score. Above [`BLAST_RADIUS_SCORE_FRACTION`] (0.35) and the
/// bounded-structural-caller fraction (0.4): LSP evidence is exact
/// (semantic, server-verified), not a bare-name heuristic, so it should win
/// a shared role/budget slot over an equivalent heuristic candidate for the
/// same relationship through the existing allocator — no separate
/// suppression logic needed, `order_note` already merges/ranks by score.
pub(crate) const LSP_SCORE_FRACTION: f32 = 0.55;
/// Deadline for a single LSP request. A slow or hung server must never stall
/// a query indefinitely — see `lsp::transport::Transport::call`.
pub(crate) const LSP_REQUEST_TIMEOUT_MS: u64 = 3000;
/// Deadline for the `initialize` handshake specifically — generous relative
/// to [`LSP_REQUEST_TIMEOUT_MS`] because a server's first response can
/// include cold-start work (e.g. resolving the Python environment) a later
/// request never repeats.
pub(crate) const LSP_INIT_TIMEOUT_MS: u64 = 10_000;
/// Cap on live `ty server` (or other LSP) child processes `oxide mcp`'s
/// `ProcessCache` keeps warm across repository roots — each one is a real OS
/// process (~90MB RSS measured for `ty`, docs/lsp-enrichment-eval/README.md),
/// unlike other MCP caches, so this needs an explicit bound. Overridden via
/// `$OXIDE_LSP_MAX_CACHED_SESSIONS`; unset is this default.
pub(crate) const LSP_MAX_CACHED_SESSIONS: usize = 4;
/// Cap on documents one `LspClient` session keeps `didOpen`'d/tracked for
/// resync at once. `ensure_open`/`resync_open_documents` are what keep a
/// cached session's view of a file fresh — without a bound, a long-lived
/// MCP session querying many different areas of a repo over time would
/// accumulate an ever-growing open-document set, unbounded filesystem work
/// per query. Over the cap, the least-recently-touched document is
/// `didClose`'d before a new one opens. Overridden via
/// `$OXIDE_LSP_MAX_OPEN_DOCUMENTS`; unset is this default.
pub(crate) const LSP_MAX_OPEN_DOCUMENTS: usize = 20;
