use crate::service::IndexResult;
use crate::term::{duration, thousands, Paint};

/// The one line a user reads after `oxide index`, shaped by what the run
/// actually did: a full build (`Indexed`/`Reindexed` — the counts are the
/// whole corpus), an incremental one (`Updated` — just the files that
/// changed and the symbols in them), a derived-layer rebuild (`Refreshed`,
/// from `-e`/`-g`), or nothing at all (`Up to date`, no duration: it was
/// instant and the number says nothing). The breakdown lines beneath are
/// unchanged and still carry every counter.
pub(in crate::cli) fn print_index_summary(result: &IndexResult, rebuilt: bool, p: &Paint) {
    let touched = result.changed_files
        + result.removed_files
        + result.embedded_symbols
        + result.relations_refreshed_symbols;
    // A file that vanished or turned unreadable between scan and parse is
    // not stored and not counted as touched, so it must be reported on
    // every path — including the shortcut below, or "Up to date" would
    // hide it.
    let warn_errored = || {
        if result.errored_files > 0 {
            println!(
                "{} {}",
                p.bang(),
                p.warn(&format!(
                    "{} file(s) could not be read and were skipped",
                    thousands(result.errored_files)
                ))
            );
        }
    };
    if touched == 0 {
        println!(
            "{} {} {}",
            p.check(),
            p.bold("Up to date"),
            p.dim("· no changes found")
        );
        warn_errored();
        return;
    }
    let full_build = result.fresh_index || rebuilt;
    let files_touched = result.changed_files + result.removed_files;
    let took = p.dim(&format!("in {}", duration(result.duration_ms)));
    // Blank line first: on a terminal the stage lines just finished on
    // stderr, and the result should read as its own block.
    println!();
    if full_build {
        println!(
            "{} {} {} files · {} symbols {took}",
            p.check(),
            p.bold(if result.fresh_index {
                "Indexed"
            } else {
                "Reindexed"
            }),
            thousands(result.scanned_files),
            thousands(result.total_symbols),
        );
    } else if files_touched > 0 {
        println!(
            "{} {} {} files · {} symbols {took}",
            p.check(),
            p.bold("Updated"),
            thousands(files_touched),
            thousands(result.new_symbols + result.changed_symbols + result.deleted_symbols),
        );
    } else {
        // `-e`/`-g` on a clean tree: nothing on disk changed, one derived
        // layer was rebuilt. Say which, not "Updated 0 files".
        let what = if result.embedded_symbols > 0 {
            format!("{} embeddings", thousands(result.embedded_symbols))
        } else {
            format!(
                "graph for {} symbols",
                thousands(result.relations_refreshed_symbols)
            )
        };
        println!("{} {} {what} {took}", p.check(), p.bold("Refreshed"));
    }
    println!(
        "  {}",
        p.dim(&format!(
            "{} files · {} reparsed · {} unchanged · {} removed",
            thousands(result.scanned_files),
            thousands(result.changed_files),
            thousands(result.reused_files),
            thousands(result.removed_files)
        ))
    );
    println!(
        "  {}",
        p.dim(&format!(
            "{} symbols new · {} changed · {} deleted",
            thousands(result.new_symbols),
            thousands(result.changed_symbols),
            thousands(result.deleted_symbols)
        ))
    );
    let graph_note = if result.relations_refreshed_symbols > 0 {
        format!(
            " · graph refreshed for {} symbols",
            thousands(result.relations_refreshed_symbols)
        )
    } else {
        String::new()
    };
    println!(
        "  {}",
        p.dim(&format!(
            "{} embeddings written · {} reused{graph_note}",
            thousands(result.embedded_symbols),
            thousands(result.reused_embeddings)
        ))
    );
    warn_errored();
    // Closes the block only after a full build — the one run long enough
    // to have shown every stage. A 200ms incremental update doesn't need a
    // second confirmation under its one-line result. No marker: the ✓ above
    // already carries the state, so this reads as a footer, not a second
    // event.
    if full_build {
        println!("  {}", p.bold("Done!"));
    }
}

/// A large repo on the local embedder is a plausible candidate for remote
/// embeddings (better quality/throughput) — but this only ever *suggests*
/// `oxide setup`, never switches anything. Interactive-only (never
/// `--json`/MCP, matching how the privacy warning itself only ever appears
/// inside `oxide setup`'s own interactive path).
const LARGE_REPO_REMOTE_HINT_THRESHOLD: usize = 20_000;

pub(in crate::cli) fn maybe_recommend_remote_embeddings(result: &IndexResult) {
    if !result.embedder_is_remote && result.total_symbols > LARGE_REPO_REMOTE_HINT_THRESHOLD {
        eprintln!(
            "\nnote: this is a large repository ({} symbols). A remote embedding \
             provider (Voyage, Jina, or an OpenAI-compatible endpoint) can improve \
             semantic search quality/throughput at that scale — source-code excerpts \
             and queries would leave this machine. Run `oxide setup` to opt in; local \
             embedding remains the default either way.",
            thousands(result.total_symbols)
        );
    }
}

pub(in crate::cli) fn render_watch_start(root: &std::path::Path, p: &Paint) {
    println!(
        "{} Reconciling {} before watching...",
        p.dot(),
        p.bold(&root.display().to_string())
    );
}

pub(in crate::cli) fn render_watch_ready(root: &std::path::Path, p: &Paint) {
    println!(
        "{} Watching {} {}",
        p.check(),
        p.bold(&root.display().to_string()),
        p.dim("(ctrl-c to stop)")
    );
}

pub(in crate::cli) fn render_watch_batch(report: &crate::index::IndexReport, p: &Paint) {
    if report.scanned_files > 0 {
        let marker = if report.errored_files > 0 {
            p.bang()
        } else {
            p.check()
        };
        println!(
            "{marker} {} file(s) changed {}",
            thousands(report.scanned_files),
            p.dim(&format!(
                "· {} reparsed · {} removed · {} errored · {} embedded · {} reused",
                report.reparsed_files,
                report.removed_files,
                report.errored_files,
                report.embedded_symbols,
                report.reused_embeddings
            ))
        );
    }
}
