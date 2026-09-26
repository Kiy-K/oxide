use super::super::{
    render::{maybe_recommend_remote_embeddings, print_index_summary, print_json},
    CliError,
};
use crate::index::IndexOptions;
use crate::service::RepositoryService;
use crate::term::{ColorChoice, Paint, StderrProgress};

pub(super) fn cmd_index(
    path: Option<&str>,
    embedder_url: Option<&str>,
    opts: &IndexOptions,
    json: bool,
    color: ColorChoice,
) -> Result<(), CliError> {
    let service = RepositoryService::discover(path).map_err(|e| CliError::service(e, json))?;
    // Stage progress goes to stderr (live on a terminal, one line per stage
    // otherwise) so stdout is only ever the summary. `--json` gets exactly
    // the result on stdout and nothing else (`tests/cli_e2e.rs` asserts
    // stderr stays empty).
    let progress = (!json).then(|| StderrProgress::new(color));
    let result = match &progress {
        Some(sink) => service.index_staged(embedder_url, opts, sink),
        None => service.index(embedder_url, opts),
    }
    .map_err(|e| CliError::service(e, json))?;
    drop(progress);
    if json {
        print_json(&result)?;
    } else {
        print_index_summary(&result, opts.force_reparse, &Paint::for_stdout(color));
        maybe_recommend_remote_embeddings(&result);
    }
    Ok(())
}
