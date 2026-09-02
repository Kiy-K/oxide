# Constraints for a future filesystem-watcher / auto-indexing daemon

Not implemented. This documents what the current indexing/embedding APIs
already give a future watcher for free, and the seams that need to open
before one can be built, so the embedding-layer work in
`docs/embedding-profile-comparison/` doesn't have to be revisited later.

The desired model (native fs events, debounce/coalesce, cheap
lexical/graph refresh first, semantic update afterward for changed symbols
only, explicit pending/stale semantic state, startup/reconnect
reconciliation, visible degradation on watcher failure with `oxide index`
as the manual recovery path) is achievable without any change to what
Tasks C/D already built — but three seams are missing today and should be
opened when that work actually starts, not before.

## Already compatible, no change needed

- **Cheap-first, semantic-after staging.** `RepositoryService::index_staged`
  (added in the rebuild-scopes work, `docs/indexing-rebuild-scopes/README.md`)
  already runs `index::update_base` (scan/parse/lexical/graph) to completion
  and hands the caller an `IndexResult` *before* `index::update_embeddings`
  runs. A watcher can call `update_base` on every debounced batch and defer
  `update_embeddings` to its own schedule (e.g. every N seconds, or once a
  batch of base updates accumulates) with zero new API surface.
- **Embedding fingerprint/provenance.** `EmbeddingSpaceFingerprint`-based
  staleness detection (`update_embeddings`, `src/embeddings.rs`) does not
  care who calls it or how often — a watcher re-embedding one changed
  symbol and a human running `oxide index -e` hit the exact same
  compatibility check.
- **Idempotent recovery.** `oxide index` (no flags) is already a safe,
  correct reconciliation of "however long we weren't watching" — it never
  assumes prior watcher state, just compares the tree to `index.db` from
  scratch. This is why "watcher failure degrades to manual `oxide index`"
  needs no new code: that command's contract already *is* "reconcile
  whatever changed since last time," regardless of cause (missed events,
  watcher crash, cold start).

## Seams to open before the watcher is built (not now)

1. **`DENYLIST_DIRS` (`src/scanner.rs`) is private.** A watcher must ignore
   the same directories/files indexing excludes — `.git`, `node_modules`,
   `target`, venvs, build output, etc. — plus whatever `.gitignore` says.
   Today only `scanner::scan_repo` can evaluate that combined predicate.
   Before building the watcher, expose a `pub fn is_ignored(path, root)`
   (or equivalent) that both `scan_repo` and the watcher's fs-event filter
   call, so the exclusion set has one definition instead of a
   watcher-side copy that silently drifts from the indexer's.
2. **No standalone "pending embeddings" query.** `update_embeddings`
   computes exactly the right set today — symbols whose stored
   `content_hash` doesn't match their current one, or that have no stored
   vector at all (`src/index.rs`, the `to_embed` filter) — but only as a
   private step inside the function that also immediately embeds them.
   "Explicitly track pending/stale semantic state instead of pretending
   the index is fully current" needs this same comparison exposed as a
   read-only call (e.g. `pending_embedding_count(store) -> usize` or
   `stale_embedding_symbol_ids(store) -> Vec<u64>`) that a watcher can poll
   or report from without triggering actual embedding work.
3. **`update_base` always re-walks the whole tree.** `scanner::scan_repo`
   plus a full content-hash comparison against every stored file runs on
   every `update_base` call — correct, but shaped for "run this
   occasionally," not "run this on every debounced fs-event batch." A
   watcher wants a variant that takes the specific changed-path set the OS
   already told it about (`update_base_for_files(root, store, opts,
   changed: &[PathBuf])`) and skips the full walk, while still reusing the
   same per-file hash/parse/relations logic `update_base` already has. This
   is an additive seam — `update_base` stays as the "reconcile everything"
   fallback used by startup/reconnect reconciliation and manual
   `oxide index`.

None of these require touching embedding semantics, retrieval scoring, or
the `IndexOptions` flag contract (`docs/indexing-rebuild-scopes/README.md`)
— they're pure factoring-out of logic that already exists in the right
shape, kept for whoever picks up the watcher task next.
