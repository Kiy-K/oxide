//! The persisted format: the exact schema SQL, the version constants that
//! gate compatibility, and the `meta` keys that carry an index's identity,
//! write generation and in-flight state. `SCHEMA_SQL` is executed verbatim
//! by `SqliteStore::open`; do not reformat it.

/// SQLite table/column layout version. Bump when the physical schema changes
/// in a way that is not purely additive (existing `CREATE TABLE IF NOT
/// EXISTS` statements would not pick up the change on their own).
pub const SCHEMA_VERSION: u32 = 1;
/// Symbol-extraction semantics version: id composition, hashing, or which
/// fields feed comparisons. Bump when a change would make an old index's
/// stored symbols not directly comparable to freshly-parsed ones.
///
/// A bump alone is not enough, and must be paired with the forced reparse in
/// `update_base` — `validate_index` refuses to serve a mismatched index, but
/// plain `oxide index` compares source hashes, so an unchanged file would
/// never be revisited and the version would be republished over stale rows.
///
/// 2: decorated definitions span their decorators (spans, `content_hash`,
/// `signature` all move), base clauses capture qualified/generic names, JSX
/// element usage counts as a call, and `mod`/`namespace` blocks qualify
/// their members.
pub const EXTRACTION_VERSION: u32 = 3;

/// Meta key holding the in-flight embedding-space fingerprint while a
/// provider migration is running. Non-empty means "the vectors in this index
/// belong to *this* fingerprint, and the published identity metadata has not
/// caught up yet" — see [`IndexBackend::begin_embedding_migration`]. Cleared
/// (set to the empty string, matching the `filter(|s| !s.is_empty())` idiom
/// used for every other optional meta value) in the same atomic
/// `set_meta_all` that publishes the completed identity.
///
/// [`IndexBackend::begin_embedding_migration`]: crate::storage::IndexBackend::begin_embedding_migration
pub const EMBEDDING_MIGRATION_KEY: &str = "embedding_migration";

/// Meta key holding a per-database random identity, written once by
/// [`SqliteStore::open`] when the schema is created (or on the first
/// writer open of an index that predates it). Pairs with
/// [`INDEX_GENERATION_KEY`] to form a cache key: the generation alone
/// restarts at 1 whenever `.oxide` is deleted and rebuilt, so a
/// long-lived process holding "generation 7 of the old database" must not
/// mistake the new database's generation 7 for the same content.
///
/// [`SqliteStore::open`]: crate::storage::SqliteStore::open
pub const INDEX_ID_KEY: &str = "index_id";

/// Meta key holding a monotonically increasing counter bumped inside
/// **every** write transaction this store commits (symbols, embeddings,
/// relations, lexical postings, meta). Two reads of the same
/// `(index_id, index_generation)` are guaranteed to see identical
/// database content, which is what lets a long-running process
/// (`oxide mcp`) reuse a loaded symbol snapshot across requests instead of
/// reloading every symbol per call. Explicit rather than `PRAGMA
/// data_version` because the latter is only comparable between two reads
/// on the *same* connection, and every request opens its own.
pub const INDEX_GENERATION_KEY: &str = "index_generation";

pub(super) const SCHEMA_SQL: &str = r#"
    PRAGMA journal_mode = WAL;
    CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS files(
        path TEXT PRIMARY KEY,
        content_hash INTEGER NOT NULL
    );
    CREATE TABLE IF NOT EXISTS symbols(
        id INTEGER PRIMARY KEY,
        file TEXT NOT NULL,
        qualified_name TEXT NOT NULL,
        name TEXT NOT NULL,
        kind TEXT NOT NULL,
        language TEXT NOT NULL,
        start_line INTEGER NOT NULL,
        end_line INTEGER NOT NULL,
        content_hash INTEGER NOT NULL,
        signature TEXT NOT NULL,
        imports_json TEXT NOT NULL,
        exported INTEGER NOT NULL,
        parent TEXT,
        references_json TEXT NOT NULL DEFAULT '[]'
    );
    CREATE INDEX IF NOT EXISTS idx_symbols_file ON symbols(file);
    CREATE INDEX IF NOT EXISTS idx_symbols_name ON symbols(name);
    -- The `ON DELETE CASCADE` clauses below are load-bearing, not
    -- decorative: `replace_file` and `remove_files` delete only from
    -- `symbols` and rely on both dependent tables following. `open` turns
    -- foreign-key enforcement on explicitly for that reason.
    CREATE TABLE IF NOT EXISTS embeddings(
        symbol_id INTEGER PRIMARY KEY REFERENCES symbols(id) ON DELETE CASCADE,
        content_hash INTEGER NOT NULL,
        dim INTEGER NOT NULL,
        vec BLOB NOT NULL
    );
    -- Only for `COUNT(*)`: every request validates the index by comparing
    -- the embedding and symbol row counts (`service/repository.rs::validate_index`),
    -- and without a secondary index that count has to walk the table
    -- b-tree, whose ~1 KB `vec` blobs spread the rows over every page —
    -- 7.5 ms at 15k symbols, 20% of a `--no-expand` search. SQLite counts
    -- through the smallest covering b-tree it has (`symbols` already gets
    -- this from `idx_symbols_name`), and an index on the rowid alias is a
    -- few bytes per row. The exhaustive vector scan itself still reads the
    -- table (`tests/query_plans.rs` pins both plans).
    CREATE INDEX IF NOT EXISTS idx_embeddings_symbol ON embeddings(symbol_id);
    -- Precomputed AST-precise call/base relations (structural_relations.rs),
    -- one row per (symbol, target). Populated by update_index itself, one
    -- reparsed file at a time. A side table, not new columns on `symbols`
    -- — `CREATE TABLE IF NOT EXISTS` is a no-op against an already-created
    -- `symbols` table on an existing on-disk index.db, so new columns
    -- there would never appear on an upgrade; a brand-new table name is
    -- picked up cleanly by the same `IF NOT EXISTS` on any existing
    -- database, no SCHEMA_VERSION bump needed.
    CREATE TABLE IF NOT EXISTS symbol_relations(
        symbol_id INTEGER NOT NULL REFERENCES symbols(id) ON DELETE CASCADE,
        kind TEXT NOT NULL,
        target TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_symbol_relations_symbol_id ON symbol_relations(symbol_id);
    -- Persisted BM25 postings (lexical.rs). One row per (symbol, term) with
    -- the weighted term frequency, and one `lexical_docs` row per symbol —
    -- for EVERY symbol, including those that produce no terms, so BM25's
    -- length normalization never silently falls back to the corpus average.
    --
    -- `WITHOUT ROWID` with the primary key in (term, symbol_id) order makes
    -- a query-term lookup a covering range scan: term, symbol_id and tf all
    -- live in the one b-tree, so scoring never touches a second structure.
    -- The secondary index on symbol_id is what the `ON DELETE CASCADE` uses;
    -- without it, deleting one symbol would scan the whole postings table,
    -- and `replace_file` deletes every symbol in a file.
    CREATE TABLE IF NOT EXISTS lexical_postings(
        term TEXT NOT NULL,
        symbol_id INTEGER NOT NULL REFERENCES symbols(id) ON DELETE CASCADE,
        tf INTEGER NOT NULL,
        PRIMARY KEY(term, symbol_id)
    ) WITHOUT ROWID;
    CREATE INDEX IF NOT EXISTS idx_lexical_postings_symbol ON lexical_postings(symbol_id);
    CREATE TABLE IF NOT EXISTS lexical_docs(
        symbol_id INTEGER PRIMARY KEY REFERENCES symbols(id) ON DELETE CASCADE,
        len INTEGER NOT NULL
    );
"#;

/// Format generation of the persisted lexical index. The value stored under
/// [`LEXICAL_INDEX_KEY`] when, and only when, a full-corpus base pass has
/// finished writing postings for every symbol. Bump when the tokenizer,
/// field weights, or table layout change, so an index built by an older
/// binary is rebuilt rather than scored under new rules.
pub const LEXICAL_INDEX_VERSION: u32 = 1;

/// Meta key carrying [`LEXICAL_INDEX_VERSION`] for a **complete** persisted
/// lexical index.
///
/// Absence is the in-flight state, and that is the whole design: the tables
/// existing, or even holding rows, proves nothing about whether every symbol
/// is covered. An index upgraded from a build that predates them starts with
/// zero rows; a backfill interrupted halfway leaves some files covered and
/// some not, and the covered files' `content_hash` values already match, so
/// no later incremental run would ever revisit them. Publishing this key
/// only at the end of a full pass — and treating any other value, including
/// none, as "not usable, rebuild it" — makes a partial index unreadable
/// instead of quietly wrong. Same shape as [`EMBEDDING_MIGRATION_KEY`], and
/// the same lesson as `schema_version`: a torn write must not be able to
/// present itself as a healthy index.
pub const LEXICAL_INDEX_KEY: &str = "lexical_index_version";
