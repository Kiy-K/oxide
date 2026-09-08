# Filesystem watcher / auto-indexing architecture

`oxide watch` is implemented in [`src/watcher.rs`](../../src/watcher.rs). It
keeps a repository index fresh from native filesystem events while preserving
the same incremental indexing and embedding contracts as `oxide index`.

## Current architecture

1. A `notify::RecommendedWatcher` discards `EventKind::Access` events and
   batches remaining paths behind one 400 ms settle delay. Filtering access
   events is required on Linux: reading a candidate while indexing otherwise
   creates another event and livelocks the watcher.
2. `IgnoreCache` starts from `scanner::scan_repo`'s indexable set. Known paths
   are O(1); unknown source-shaped paths refresh once; `language_for_path` and
   `has_denied_ancestor` cheaply reject build/cache/VCS noise. `.gitignore`,
   `.ignore`, and `.git/info/exclude` edits trigger a full reconciliation and
   refresh the cache.
3. `process_batch` calls `index::update_base_for_files` for the surviving
   repo-relative paths, then `update_embeddings`. That reuses the normal
   parser, reference, relation, cache-invalidation, and provider-migration
   paths rather than maintaining watcher-specific indexing semantics.
4. A watcher failure is recoverable: ordinary `oxide index` remains the full
   reconciliation path. `RepositoryService::status` exposes base freshness
   and pending embeddings; `index::pending_embedding_count` exposes the
   provider-aware read-only comparison used to predict semantic work.

## Frozen contracts

- Watch batches must converge to the same stored symbols and relations as a
  full `update_base`; `tests/update_base_for_files.rs` pins this.
- Structural relation, lexical, and semantic retrieval behavior is unchanged;
  the watcher changes scheduling, not retrieval.
- Provider fingerprints and the in-flight migration marker remain the only
  authority for vector-space compatibility. A watcher must never publish
  stale or incompatible vectors as current.
- The watcher does not recursively watch or write its own `.oxide` state.
  Manual indexing remains valid concurrently under SQLite's normal WAL
  semantics.

## Deliberate limits

The watcher uses a flat debounce window, not a timer per path. This is enough
for editor-save and atomic-rename bursts and keeps the event loop simple. If
real workloads show unrelated high-rate trees delaying important edits, measure
that before replacing the batch policy.

For implementation detail and regression evidence, see
[`src/watcher.rs`](../../src/watcher.rs),
[`tests/update_base_for_files.rs`](../../tests/update_base_for_files.rs), and
[`tests/pending_embedding_count.rs`](../../tests/pending_embedding_count.rs).
