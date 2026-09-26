use crate::service::StatusResult;
use crate::term::{thousands, Paint};

fn language_label(language: crate::symbols::Language) -> &'static str {
    use crate::symbols::Language::*;
    match language {
        Python => "Python",
        TypeScript => "TypeScript",
        Tsx => "TSX",
        JavaScript => "JavaScript",
        Rust => "Rust",
        Go => "Go",
        Java => "Java",
        Ruby => "Ruby",
        Php => "PHP",
        C => "C",
        Cpp => "C++",
        Markdown => "Markdown",
    }
}

pub(in crate::cli) fn render_status(status: &StatusResult, verbose: bool, p: &Paint) {
    // Headline: the one fact a user came for, with the marker doubling as
    // the word so the state survives without color.
    let (marker, state) = if !status.index_exists {
        (p.cross(), p.err("Index not found"))
    } else if status.is_current {
        (p.check(), p.ok("Index up to date"))
    } else {
        (p.bang(), p.warn("Index stale"))
    };
    println!("{marker} {}", p.bold(&state));
    println!("  {}", p.dim(&status.root));
    if status.index_exists {
        println!(
            "  {}",
            p.dim(&format!(
                "{} files · {} symbols",
                thousands(status.files),
                thousands(status.symbols)
            ))
        );
    }

    let (semantic_marker, semantic) = if !status.index_exists {
        (p.dot(), p.dim("Semantic search not indexed"))
    } else if !status.embedder_current {
        (
            p.bang(),
            p.warn("Semantic search built with a different embedding provider"),
        )
    } else if status.pending_embeddings > 0 {
        (
            p.bang(),
            p.warn(&format!(
                "Semantic search {} symbols pending",
                thousands(status.pending_embeddings)
            )),
        )
    } else if !status.base_fresh {
        // Every stored vector is current for what is stored — but files
        // have changed since, so "ready" would overstate it.
        (
            p.bang(),
            p.warn("Semantic search ready for the indexed content only"),
        )
    } else {
        (p.check(), p.ok("Semantic search ready"))
    };
    println!("{semantic_marker} {semantic}");
    // Every language this *build* extracts, not the ones present in this
    // repo (the index does not track that) — labelled for what it reports.
    println!(
        "{} {}",
        p.dot(),
        p.dim(&format!(
            "Supports {}",
            status
                .supported_languages
                .iter()
                .map(|l| language_label(*l))
                .collect::<Vec<_>>()
                .join(" · ")
        ))
    );

    if verbose {
        println!();
        // Pad before styling: a width applied to an escaped string counts
        // the escape bytes and misaligns the column on a terminal.
        let row =
            |label: &str, value: &str| println!("  {}{value}", p.dim(&format!("{label:<12}")));
        row(
            "Embedder",
            status.embedder.as_deref().unwrap_or("not indexed"),
        );
        row(
            "Embeddings",
            &format!(
                "{} stored · {} pending",
                thousands(status.embeddings),
                thousands(status.pending_embeddings)
            ),
        );
        row(
            "Files",
            if status.base_fresh {
                "all indexed content matches disk"
            } else {
                "some indexed content is out of date"
            },
        );
        let index_path = std::path::Path::new(&status.root)
            .join(".oxide")
            .join("index.db");
        let size = std::fs::metadata(&index_path)
            .map(|m| format!("{:.1} MB", m.len() as f64 / 1_048_576.0))
            .unwrap_or_else(|_| "absent".to_string());
        row("Index file", &format!("{} ({size})", index_path.display()));
        row("Schema", &status.schema_version.to_string());
    }

    if !status.index_exists || !status.is_current {
        println!("\nRun:\n  {}", p.bold("oxide index"));
    }
}
