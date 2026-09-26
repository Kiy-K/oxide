//! The storage contract: [`IndexBackend`] and the data it exchanges with
//! callers. Nothing here depends on SQLite; `sqlite.rs` implements it.

use crate::symbols::Symbol;
use anyhow::Result;
use serde::Serialize;
use std::collections::HashMap;

/// Storage abstraction. Small by design: swap SQLite for something else by
/// implementing this trait.
/// One parsed file: (repo-relative path, content hash, source text, symbols).
pub struct ParsedFile {
    pub file: String,
    pub hash: u64,
    pub src: String,
    pub symbols: Vec<Symbol>,
}

/// Per symbol id: `(calls, bases)`, the precomputed-relations side-table
/// shape (`IndexBackend::all_symbol_relations`).
pub type SymbolRelations = HashMap<u64, (Vec<String>, Vec<String>)>;

pub trait IndexBackend {
    fn get_meta(&self, key: &str) -> Result<Option<String>>;
    fn set_meta(&mut self, key: &str, value: &str) -> Result<()>;
    /// Set several meta keys as one atomic transaction: either all of them
    /// land or none do. `update_index` uses this for its closing
    /// root/embedder/dim/schema_version/extraction_version writes so a
    /// process interrupted mid-write can never leave a torn subset behind —
    /// `validate_index`'s "index predates version tracking" fallback for a
    /// missing `schema_version` key would otherwise treat that torn state
    /// as a compatible legacy index instead of an incomplete one.
    ///
    /// `expected_space` is the value [`EMBEDDING_MIGRATION_KEY`] must still
    /// hold for this publication to be honest — see
    /// [`IndexBackend::put_embeddings_batch`] for what that guard buys and
    /// what it does not.
    ///
    /// [`EMBEDDING_MIGRATION_KEY`]: crate::storage::EMBEDDING_MIGRATION_KEY
    fn set_meta_all(&mut self, expected_space: &str, pairs: &[(&str, &str)]) -> Result<()>;
    fn file_hashes(&self) -> Result<HashMap<String, u64>>;
    /// Replaces `file`'s symbols (and, since a symbol whose body didn't
    /// change keeps its embedding across the rewrite, its embeddings) and
    /// its precomputed relations, as **one** transaction. Relations were
    /// briefly a separate `put_symbol_relations_batch` call issued right
    /// after this one from `update_index` — a process interrupted between
    /// the two left `symbols`/`files.content_hash` already updated to the
    /// new content while `symbol_relations` still held the old, wrong
    /// values, and because content_hash already matched, no future run
    /// would ever reparse that file to fix it (`tests/interrupted_index_recovery.rs`
    /// pins the general class of bug this pattern already guards against
    /// for symbols+embeddings; this closes the same class for relations).
    /// `relations` is typically `structural_relations::compute_file_relations`'s
    /// output for `symbols`; pass `&[]` when the caller has no relations to
    /// write (every non-`update_index` test call site).
    fn replace_file(
        &mut self,
        file: &str,
        hash: u64,
        symbols: &[Symbol],
        relations: &[(u64, Vec<String>, Vec<String>)],
        postings: &[crate::lexical::DocPostings],
    ) -> Result<()>;
    fn remove_files(&mut self, files: &[String]) -> Result<()>;
    /// `(document count, summed document length)` over the persisted lexical
    /// index — BM25's `avg_len` denominator and numerator. Summed in SQL as
    /// an integer rather than by folding `f32` document lengths in hash-map
    /// order, which is what the in-memory index does; the two agree exactly
    /// while the total stays under `f32`'s 2^24 integer limit, and past it
    /// the SQL sum is the one that stays deterministic across processes.
    /// Rewrite lexical postings for symbols whose rows are missing or stale
    /// while the symbols themselves are unchanged — the backfill path for an
    /// index that predates the persisted lexical tables, or whose earlier
    /// backfill was interrupted.
    ///
    /// Separate from [`Self::replace_file`] because it must not touch
    /// `symbols`: reparsing an unchanged file to repair a derived table
    /// would be wasted work and would misreport `reparsed_files`. Safe
    /// outside `replace_file`'s transaction only because the symbols are not
    /// moving underneath it, and because an interrupted backfill leaves
    /// [`LEXICAL_INDEX_KEY`] unpublished, which makes the whole persisted
    /// lexical index unreadable rather than partially trusted.
    /// Returns `false` without writing if `file`'s stored `content_hash` no
    /// longer equals `expected_hash` — another process replaced that file
    /// between this run's scan and this write, and the caller's postings
    /// describe the older revision.
    ///
    /// [`LEXICAL_INDEX_KEY`]: crate::storage::LEXICAL_INDEX_KEY
    fn put_file_lexical(
        &mut self,
        file: &str,
        expected_hash: u64,
        postings: &[crate::lexical::DocPostings],
    ) -> Result<bool>;
    fn lexical_totals(&self) -> Result<(usize, i64)>;
    /// Postings for one query term: `(symbol_id, weighted tf, document
    /// length)`, one row per matching document.
    fn lexical_postings(&self, term: &str) -> Result<Vec<(u64, u32, u32)>>;
    /// Every symbol, ordered by `(file, start_line)`; rows tied on both
    /// come out in `id` (rowid) order, so two loads of the same index
    /// always agree and everything downstream that iterates the corpus
    /// (`RelationGraph::build`'s per-file and per-name lists,
    /// `update_embeddings`' batch composition) is deterministic.
    /// `calls`/`bases` are left empty.
    fn all_symbols(&self) -> Result<Vec<Symbol>>;
    /// [`Self::all_symbols`]' rows in the same order, as
    /// [`Completeness::Partial`](crate::symbols::Completeness) symbols:
    /// `imports` empty and `references` decoded only where
    /// [`crate::symbols::is_test_symbol`] holds — everything
    /// `RelationGraph` reads of a non-seed symbol, at a fraction of the
    /// decode (docs/retrieval-profile/corpus-load-baseline/
    /// lean-snapshot-screen/). The corpus-snapshot loader; the default is
    /// the full load, which is always correct, just not lean.
    fn all_symbols_lean(&self) -> Result<Vec<Symbol>> {
        self.all_symbols()
    }
    /// `COUNT(*)` over `symbols` — BM25's document count, without loading a
    /// single row. Must be read in the same snapshot as the postings it
    /// normalizes (`open_read_only` holds one for the connection's life).
    fn symbol_count(&self) -> Result<usize>;
    /// The symbols with these ids, in no particular order; ids with no row
    /// are simply absent. `calls`/`bases` are left empty, as in
    /// [`Self::all_symbols`]. This is the hydration step of candidate-first
    /// retrieval: scoring runs over postings and vector rows alone, and
    /// only the bounded candidate set is ever turned into `Symbol`s.
    fn symbols_by_ids(&self, ids: &[u64]) -> Result<Vec<Symbol>>;
    fn symbol_hash(&self, id: u64) -> Result<Option<u64>>;
    fn put_embedding(&mut self, symbol_id: u64, vec: &[f32]) -> Result<()>;
    /// Same effect as calling [`Self::put_embedding`] once per item, but as
    /// one transaction instead of one autocommit per row — profiling the
    /// embedding stage found the per-symbol `execute()` calls (each an
    /// implicit transaction under SQLite's default autocommit behavior,
    /// each paying its own fsync) were a real, avoidable cost independent
    /// of embedder latency, unlike the batch/thread-chunking around it
    /// (already near the empirically-measured optimum — see
    /// docs/indexing-rebuild-scopes/README.md). A symbol id with no
    /// matching row in `symbols` is skipped, same as `put_embedding`.
    ///
    /// Fails, in the same transaction, unless [`EMBEDDING_MIGRATION_KEY`]
    /// still holds `expected_space` — the writer's own fingerprint while a
    /// migration is in flight, or `""` for an ordinary incremental run.
    /// Without this, `begin_embedding_migration`'s atomicity only holds for
    /// one process: a second `oxide index` starting its own migration
    /// mid-run would empty the table under the first, which would then keep
    /// appending its vectors alongside the second's and one of them would
    /// publish a single identity over the mix. With it, the loser fails
    /// loudly and the table only ever holds the last migration's rows.
    ///
    /// What this does **not** close: a run whose compatibility check
    /// happened *before* another run's migration completed, and whose first
    /// write lands after it — the marker is legitimately `""` at both
    /// moments, so the guard cannot see the difference. Closing that needs
    /// run-level writer serialization (one transaction spanning the whole
    /// embedding phase, or an advisory lock), which is deliberately out of
    /// scope: embedding takes minutes, and holding SQLite's write lock that
    /// long would block every other writer and grow the WAL without bound.
    ///
    /// [`EMBEDDING_MIGRATION_KEY`]: crate::storage::EMBEDDING_MIGRATION_KEY
    fn put_embeddings_batch(
        &mut self,
        expected_space: &str,
        items: &[(u64, Vec<f32>)],
    ) -> Result<()>;
    fn embedding_with_hash(&self, symbol_id: u64) -> Result<Option<(u64, Vec<f32>)>>;
    /// All embeddings in one shot. The indexer's staleness pass and tests
    /// use it; retrieval no longer does (see [`Self::for_each_embedding`]).
    fn all_embeddings(&self) -> Result<HashMap<u64, (u64, Vec<f32>)>>;
    /// Visit every stored embedding as `(symbol_id, stored dim, raw
    /// little-endian f32 bytes)` without materializing the table: one row
    /// is decoded, scored and dropped before the next is read, so an
    /// exhaustive scan holds O(1) vectors in memory instead of O(N). The
    /// blob is the same bytes [`Self::all_embeddings`] decodes; callers
    /// apply the same `take(dim)` rule.
    fn for_each_embedding(&self, visit: &mut dyn FnMut(u64, usize, &[u8])) -> Result<()>;
    /// Clear every vector AND record `fingerprint_json` under
    /// [`EMBEDDING_MIGRATION_KEY`] as **one** transaction — the crash-safety
    /// primitive for a provider switch, and deliberately the *only* way to
    /// empty the embeddings table. A plain `clear_embeddings` used to sit
    /// beside it; it was removed rather than left available, because an
    /// unmarked clear is precisely the state this method exists to make
    /// unreachable.
    ///
    /// `update_embeddings` publishes the new provider identity only at the
    /// very end (`set_meta_all`), so a process killed after the replacement
    /// vectors commit but before that write used to leave rows from provider
    /// B under metadata naming provider A. Nothing downstream could tell:
    /// row counts matched, and a same-dimension switch scored queries
    /// against the wrong vector space rather than failing loudly.
    ///
    /// The marker closes that window because the atomicity runs the right
    /// way round: the marker is never present unless the table was emptied
    /// in the same transaction that set it, so "marker == the provider I am
    /// about to use" proves every surviving row belongs to that provider's
    /// space. Setting the marker *before* clearing (two statements) would
    /// prove nothing — a crash between them leaves the old provider's rows
    /// under a marker claiming the new one, which is the original bug with
    /// extra steps.
    ///
    /// This alone only proves it within one process. Extending it across
    /// concurrent `oxide index` runs is [`IndexBackend::put_embeddings_batch`]'s
    /// and [`IndexBackend::set_meta_all`]'s `expected_space` guard, which
    /// re-reads the marker inside each write's own transaction; read that
    /// method's doc for the interleaving it closes and the one it does not.
    ///
    /// [`EMBEDDING_MIGRATION_KEY`]: crate::storage::EMBEDDING_MIGRATION_KEY
    fn begin_embedding_migration(&mut self, fingerprint_json: &str) -> Result<()>;
    /// Replaces precomputed call/base relations for every `(symbol_id,
    /// calls, bases)` triple in `relations`, as one transaction per call —
    /// `update_index` calls this once per reparsed file
    /// (`structural_relations::compute_file_relations`), so one transaction
    /// per file, not per symbol (an earlier per-symbol-transaction version
    /// left a file only partially updated if interrupted mid-run; per-file
    /// batching narrows that window — full run-level atomicity would need
    /// a completion marker, not added here). `calls`/`bases` are bare
    /// names, same heuristic tier as `Symbol::references`. Every entry is
    /// written even when both are empty — that's what clears a symbol's
    /// stale relations after an edit removes its last call/base; see
    /// `compute_file_relations`'s doc comment.
    fn put_symbol_relations_batch(
        &mut self,
        relations: &[(u64, Vec<String>, Vec<String>)],
    ) -> Result<()>;
    /// All precomputed relations, keyed by symbol id, as `(calls, bases)`.
    /// Empty/absent for any symbol with no calls/bases at all. Read by
    /// `structural_relations::load_symbols_with_relations`, which
    /// `context.rs::build_context` uses instead of calling this crate's
    /// `all_symbols` directly whenever `RelationGraph::callers_of`/
    /// `implementors_of` are needed.
    fn all_symbol_relations(&self) -> Result<SymbolRelations>;
    /// Bracket a full-corpus write pass (`update_base`). The SQLite backend
    /// raises its WAL auto-checkpoint threshold for the duration — see
    /// [`BULK_WAL_AUTOCHECKPOINT_PAGES`] — and restores the default after,
    /// so a long-lived writer (`oxide watch`) keeps normal checkpointing
    /// for its small per-batch writes. No-ops by default.
    ///
    /// [`BULK_WAL_AUTOCHECKPOINT_PAGES`]: crate::storage::BULK_WAL_AUTOCHECKPOINT_PAGES
    fn begin_bulk_writes(&mut self) -> Result<()> {
        Ok(())
    }
    fn end_bulk_writes(&mut self) -> Result<()> {
        Ok(())
    }
}

/// Index statistics for `oxide stats`.
#[derive(Debug, Clone, Serialize)]
pub struct IndexStats {
    pub files: usize,
    pub symbols: usize,
    pub embeddings: usize,
}
