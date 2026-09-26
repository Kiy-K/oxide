//! Embedding stage: embedding-space compatibility (`incompatible_stored_space`,
//! the single decision point), the provider-migration marker, (re)embedding,
//! and the closing `set_meta_all` that publishes the index identity.

use super::{count_summary, IndexOptions, IndexReport, NoProgress, ProgressSink, Stage};
use crate::embeddings::symbol_embed_text;
use crate::storage::{IndexBackend, EMBEDDING_MIGRATION_KEY, EXTRACTION_VERSION, SCHEMA_VERSION};
use crate::symbols::Symbol;
use anyhow::Result;
use std::path::Path;

/// Embedding stage only: fingerprint/embedder staleness check, then
/// (re)embed. Must run after a base stage (`update_base`/`update_index`)
/// that reflects current file content — never call this against a store
/// whose symbols might be stale, or it will embed symbols against text
/// they no longer match. Adds this stage's elapsed time to
/// `report.duration_ms` rather than overwriting it, so a caller running
/// both stages back to back ends up with their sum.
pub fn update_embeddings(
    root: &Path,
    store: &mut dyn IndexBackend,
    embedder: &dyn crate::embeddings::EmbeddingProvider,
    opts: &IndexOptions,
    report: &mut IndexReport,
) -> Result<()> {
    update_embeddings_reporting(root, store, embedder, opts, report, &NoProgress)
}

/// [`update_embeddings`] with stage/progress reporting; identical work.
pub fn update_embeddings_reporting(
    root: &Path,
    store: &mut dyn IndexBackend,
    embedder: &dyn crate::embeddings::EmbeddingProvider,
    opts: &IndexOptions,
    report: &mut IndexReport,
    progress: &dyn ProgressSink,
) -> Result<()> {
    let started = std::time::Instant::now();
    let workers = std::thread::available_parallelism()
        .map(|n| n.get())
        .unwrap_or(1)
        .clamp(1, 4);

    // Vectors from a different vector space are not comparable: wipe them
    // once so everything below re-embeds under the current model. Clearing
    // and marking the migration in flight is one transaction, so from here
    // on "marker present" implies "every surviving vector is the marker's"
    // — see `IndexBackend::begin_embedding_migration`.
    let current_fp = embedder.fingerprint();
    let fingerprint_json = serde_json::to_string(&current_fp)?;
    let resuming = store
        .get_meta(EMBEDDING_MIGRATION_KEY)?
        .is_some_and(|s| !s.is_empty());
    if let Some(reason) = incompatible_stored_space(store, embedder)? {
        eprintln!("oxide: {reason}");
        store.begin_embedding_migration(&fingerprint_json)?;
    } else if resuming {
        // Compatible *and* marked means an earlier run of this same provider
        // was interrupted: its rows are ours to finish. Rewrite the marker
        // with this run's canonical serialization so the guard below can
        // compare it as bytes rather than re-parsing it on every write.
        store.set_meta(EMBEDDING_MIGRATION_KEY, &fingerprint_json)?;
    }

    // The value every write below asserts the marker still holds. Empty for
    // an ordinary incremental run (no migration is in flight, and none may
    // start under us); this run's fingerprint while one is. See
    // `IndexBackend::put_embeddings_batch` for the concurrency this closes.
    // After the branch above this is either "" or this run's
    // `fingerprint_json`; read it back rather than reconstructing it.
    let expected_space = store.get_meta(EMBEDDING_MIGRATION_KEY)?.unwrap_or_default();

    // Embed only symbols whose embedding is missing or whose content
    // changed; everything else reuses its stored vector untouched — unless
    // `opts.force_embeddings` (`-e`/`--embeddings`) says recompute
    // everything regardless of the hash match. Vector computation is pure
    // CPU: fan out over the same bounded pool, write serially after.
    let embeddings = store.all_embeddings()?;
    let all = store.all_symbols()?;
    let to_embed: Vec<&Symbol> = all
        .iter()
        .filter(|s| match embeddings.get(&s.id()) {
            Some((old_hash, _)) if *old_hash == s.content_hash && !opts.force_embeddings => {
                report.reused_embeddings += 1;
                false
            }
            _ => true,
        })
        .collect();
    let chunk_size = to_embed.len().div_ceil(workers.max(1));
    let embed_total = to_embed.len();
    let embed_done = std::sync::atomic::AtomicUsize::new(0);
    progress.begin(Stage::Embed, Some(embed_total));
    // Batched path: providers with batch endpoints (HTTP) get one request per
    // chunk; the thread pool stays useful for per-text providers.
    if to_embed.len() < 8 || std::env::var("OXIDE_EMBED_URL").is_ok() {
        for chunk in to_embed.chunks(64) {
            let texts: Vec<String> = chunk.iter().map(|s| symbol_embed_text(s)).collect();
            let vectors = embedder.embed_documents(&texts);
            // One transaction per chunk instead of one autocommit per
            // symbol — see `IndexBackend::put_embeddings_batch`'s doc
            // comment for why this was worth doing and the batch/thread
            // chunking around it wasn't.
            let mut batch: Vec<(u64, Vec<f32>)> = Vec::with_capacity(chunk.len());
            for (s, vec) in chunk.iter().zip(vectors) {
                if vec.iter().all(|f| *f == 0.0) || vec.is_empty() {
                    report.embed_failures += 1;
                    continue;
                }
                batch.push((s.id(), vec));
            }
            report.embedded_symbols += batch.len();
            store.put_embeddings_batch(&expected_space, &batch)?;
            let done = embed_done.fetch_add(chunk.len(), std::sync::atomic::Ordering::Relaxed)
                + chunk.len();
            progress.advance(Stage::Embed, done, embed_total);
        }
    } else {
        let computed: Vec<Vec<(u64, Vec<f32>)>> = std::thread::scope(|scope| {
            let mut handles = Vec::new();
            for chunk in to_embed.chunks(chunk_size.max(1)) {
                let embed_done = &embed_done;
                handles.push(scope.spawn(move || {
                    chunk
                        .iter()
                        .map(|s| {
                            let vector = embedder.embed_document(&symbol_embed_text(s));
                            let done =
                                embed_done.fetch_add(1, std::sync::atomic::Ordering::Relaxed) + 1;
                            progress.advance(Stage::Embed, done, embed_total);
                            (s.id(), vector)
                        })
                        .collect::<Vec<_>>()
                }));
            }
            let mut out = Vec::new();
            for h in handles {
                out.push(
                    h.join()
                        .map_err(|_| anyhow::anyhow!("embed worker panicked"))?,
                );
            }
            Ok::<_, anyhow::Error>(out)
        })?;
        // Same rejection the batched path above applies: an empty or
        // all-zero vector is a provider failure, not an embedding. Counting
        // it as a success (and storing it) let a fully-failed run report
        // `embed_failures: 0`, which `index_staged` reads as "provider
        // healthy" — and left rows that satisfy the `embeddings == symbols`
        // completeness check while carrying no signal at all.
        for part in computed {
            let kept: Vec<(u64, Vec<f32>)> = part
                .into_iter()
                .filter(|(_, vec)| {
                    let failed = vec.is_empty() || vec.iter().all(|f| *f == 0.0);
                    if failed {
                        report.embed_failures += 1;
                    }
                    !failed
                })
                .collect();
            report.embedded_symbols += kept.len();
            store.put_embeddings_batch(&expected_space, &kept)?;
        }
    }

    progress.end(Stage::Embed, &count_summary(embed_total, embed_total));

    progress.begin(Stage::Finalize, None);
    let root_str = root.display().to_string();
    let dim_str = embedder.dim().to_string();
    let schema_str = SCHEMA_VERSION.to_string();
    let extraction_str = EXTRACTION_VERSION.to_string();
    // Publishing the completed identity and retiring the in-flight marker
    // must be the same transaction as everything else here: this is the one
    // instant at which the index stops being "mid-migration" and starts
    // being "built by this provider", and a torn version of it is exactly
    // the state `begin_embedding_migration` exists to make impossible.
    store.set_meta_all(
        &expected_space,
        &[
            ("root", root_str.as_str()),
            ("embedder", embedder.name()),
            ("dim", dim_str.as_str()),
            ("schema_version", schema_str.as_str()),
            ("extraction_version", extraction_str.as_str()),
            ("embedding_fingerprint", fingerprint_json.as_str()),
            (EMBEDDING_MIGRATION_KEY, ""),
        ],
    )?;
    progress.end(Stage::Finalize, "done");
    // Additive, not an overwrite: a caller running this right after
    // `update_base` (the normal case) already has that stage's duration in
    // `report.duration_ms` and wants the combined total, not just this
    // stage's time.
    report.duration_ms += started.elapsed().as_millis();
    Ok(())
}

/// Read-only count of symbols whose embedding is missing, stale (content
/// changed since last embedded), or from a different embedding space than
/// `embedder` currently provides — exactly what `update_embeddings` would
/// (re)compute if called right now, without calling it. For the
/// auto-indexing watcher's "track base and semantic freshness independently"
/// / "stale/pending embeddings must never be presented as current"
/// requirements (`docs/auto-indexing-watcher-constraints/README.md` seam
/// #2): a caller can report "N symbols pending embedding" without
/// triggering the embedding work itself (which may be slow or
/// network-bound). Mirrors `update_embeddings`'s own staleness checks
/// exactly — the two must never diverge, or a watcher could report "0
/// pending" while a real `update_embeddings` run would still find work.
pub fn pending_embedding_count(
    store: &dyn IndexBackend,
    embedder: &dyn crate::embeddings::EmbeddingProvider,
) -> Result<usize> {
    if incompatible_stored_space(store, embedder)?.is_some() {
        return Ok(store.all_symbols()?.len());
    }
    content_stale_embedding_count(store)
}

/// Whether the index's stored vectors are usable under `embedder`, and why
/// not when they aren't: `Some(reason)` means "these vectors belong to a
/// different embedding space, clear them", `None` means "reuse is safe".
///
/// The single decision point for [`update_embeddings`] (which clears on
/// `Some`) and [`pending_embedding_count`] (which reports every symbol
/// pending on `Some`). They used to duplicate this comparison, with a
/// comment on each warning that they must never diverge; sharing it is how
/// that guarantee stops depending on the comment.
///
/// Precedence, strongest evidence first:
/// 1. [`EMBEDDING_MIGRATION_KEY`] — an unfinished migration. Only ever
///    written in the same transaction that empties the embeddings table, so
///    it, not the published metadata (which the interrupted run never
///    reached), describes the surviving rows.
/// 2. `embedding_fingerprint` — the real compatibility contract when
///    present (Phase 3.3 item 3).
/// 3. `embedder` + `dim` — the legacy fallback for indices written before
///    fingerprints existed, so upgrading OXIDE doesn't force a reindex.
///
/// A stored value at any tier that is present but unparseable is treated as
/// incompatible, never guessed at and never ignored: "unreadable" must not
/// fail open into the weaker tier below it, or a corrupt fingerprint would
/// be waved through by a matching legacy name.
fn incompatible_stored_space(
    store: &dyn IndexBackend,
    embedder: &dyn crate::embeddings::EmbeddingProvider,
) -> Result<Option<String>> {
    let current_fp = embedder.fingerprint();
    let parse =
        |raw: &str| serde_json::from_str::<crate::embeddings::EmbeddingSpaceFingerprint>(raw);

    if let Some(raw) = store
        .get_meta(EMBEDDING_MIGRATION_KEY)?
        .filter(|s| !s.is_empty())
    {
        return Ok(match parse(&raw) {
            Ok(prev) if prev == current_fp => None,
            Ok(prev) => Some(format!(
                "an interrupted migration to {} left this index's vectors mid-flight; re-embedding all symbols under {}",
                prev.model, current_fp.model
            )),
            Err(_) => Some(
                "an interrupted embedding migration left an unreadable fingerprint; re-embedding all symbols".to_string(),
            ),
        });
    }

    if let Some(raw) = store
        .get_meta("embedding_fingerprint")?
        .filter(|s| !s.is_empty())
    {
        return Ok(match parse(&raw) {
            Ok(prev) if prev == current_fp => None,
            Ok(prev) => Some(format!(
                "embedding space changed ({} -> {}); re-embedding all symbols",
                prev.model, current_fp.model
            )),
            Err(_) => Some(
                "stored embedding fingerprint is unreadable; re-embedding all symbols".to_string(),
            ),
        });
    }

    // Legacy index. The name check this codebase has always used, plus the
    // dimension: the name does not imply the width. `HashedEmbedder` reports
    // one fixed name at every `dim`, and a served model can change output
    // width without changing its label, so a name-only comparison happily
    // reuses rows of the wrong shape.
    let prev_name = store.get_meta("embedder")?.filter(|s| !s.is_empty());
    let prev_dim = store.get_meta("dim")?.filter(|s| !s.is_empty());
    if let Some(prev) = prev_name.filter(|p| p != embedder.name()) {
        return Ok(Some(format!(
            "embedder changed ({} -> {}); re-embedding all symbols",
            prev,
            embedder.name()
        )));
    }
    if let Some(prev) = prev_dim.filter(|d| *d != embedder.dim().to_string()) {
        return Ok(Some(format!(
            "embedding dimension changed ({} -> {}); re-embedding all symbols",
            prev,
            embedder.dim()
        )));
    }
    Ok(None)
}

/// Count of symbols whose stored embedding is missing or whose content
/// changed since it was computed — the embedding-space-agnostic half of
/// [`pending_embedding_count`]'s check, split out so a caller that has
/// already established embedder compatibility some other way (e.g.
/// `RepositoryService::status`'s existing name-based `embedder_current`
/// check, which is deliberately network-free) doesn't need a live
/// `EmbeddingProvider` just to ask "how many symbols are stale."
pub fn content_stale_embedding_count(store: &dyn IndexBackend) -> Result<usize> {
    let all = store.all_symbols()?;
    let embeddings = store.all_embeddings()?;
    Ok(all
        .iter()
        .filter(|s| match embeddings.get(&s.id()) {
            Some((old_hash, _)) => *old_hash != s.content_hash,
            None => true,
        })
        .count())
}
