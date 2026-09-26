use super::super::{
    render::{print_json, render_query},
    CliError,
};
use crate::retrieval::RetrievalMode;
use crate::service::RepositoryService;
use crate::term::Paint;

/// The subset of `oxide query`'s flags that shape retrieval rather than
/// output — bundled so `cmd_query` stays under clippy's argument-count
/// lint without inventing a wider "options" abstraction nothing else needs.
pub(super) struct QueryFlags {
    pub(super) budget_tokens: usize,
    pub(super) retrieval_mode: RetrievalMode,
    pub(super) blast_radius: bool,
    pub(super) git: bool,
}

pub(super) fn cmd_query(
    path: Option<&str>,
    task: &str,
    flags: QueryFlags,
    json: bool,
    p: Paint,
) -> Result<(), CliError> {
    let service = RepositoryService::discover(path).map_err(|e| CliError::service(e, json))?;
    let pack = service
        .context(
            task,
            flags.budget_tokens,
            flags.retrieval_mode,
            flags.blast_radius,
            flags.git,
        )
        .map_err(|e| CliError::service(e, json))?;
    if json {
        print_json(&pack)?;
        return Ok(());
    }
    render_query(&pack, &p);
    Ok(())
}
