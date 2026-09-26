use super::super::{
    render::{render_watch_batch, render_watch_ready, render_watch_start},
    CliError,
};
use crate::service::RepositoryService;
use crate::storage::SqliteStore;
use crate::term::Paint;

pub(super) fn cmd_watch(
    path: Option<&str>,
    embedder_url: Option<&str>,
    p: Paint,
) -> Result<(), CliError> {
    let json = false; // `oxide watch` is an interactive/long-running command, no --json mode.
    let service = RepositoryService::discover(path).map_err(|e| CliError::service(e, json))?;
    let root = service.root().to_path_buf();

    // `watcher::run` registers the filesystem watch before reconciling, so
    // no edit in this command's startup window can be missed.
    render_watch_start(&root, &p);

    let lock = crate::watcher::WatchLock::acquire(&root).map_err(|e| CliError::generic(e, json))?;
    let embedder =
        crate::embeddings::open_embedder(embedder_url).map_err(|e| CliError::generic(e, json))?;
    let mut store = SqliteStore::open(&root.join(".oxide").join("index.db"))
        .map_err(|e| CliError::generic(e, json))?;

    render_watch_ready(&root, &p);
    let stop = std::sync::atomic::AtomicBool::new(false);
    crate::watcher::run(
        &root,
        &mut store,
        embedder.as_ref(),
        &lock,
        &stop,
        |report| render_watch_batch(report, &p),
    )
    .map_err(|e| CliError::generic(e, json))
}
