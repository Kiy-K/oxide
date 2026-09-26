use super::CliError;

mod agents;
mod context;
mod index;
mod search;
mod setup;
mod status;

pub(in crate::cli) use agents::{
    print_detection, print_plans, render_agents_cancelled, render_agents_changed,
    render_agents_dry_run, render_agents_noop,
};
pub(in crate::cli) use context::{render_query, render_review};
pub(in crate::cli) use index::{
    maybe_recommend_remote_embeddings, print_index_summary, render_watch_batch, render_watch_ready,
    render_watch_start,
};
pub(in crate::cli) use search::{render_literal_search, render_search};
pub(in crate::cli) use setup::{
    print_setup_status, render_setup_cancelled, render_setup_saved, render_setup_warning,
};
pub(in crate::cli) use status::render_status;

/// CLI JSON presentation retains serde field order and the existing pretty layout.
pub(in crate::cli) fn print_json<T: serde::Serialize>(value: &T) -> Result<(), CliError> {
    println!(
        "{}",
        serde_json::to_string_pretty(value).map_err(|e| CliError::generic(e, true))?
    );
    Ok(())
}

pub(in crate::cli) fn render_stats(stats: &crate::storage::IndexStats) {
    println!("files:      {}", stats.files);
    println!("symbols:    {}", stats.symbols);
    println!("embeddings: {}", stats.embeddings);
}
