use super::search::{describe_reasons, short_symbol_id};
use crate::retrieval::read_snippet;
use crate::term::{thousands, Paint};

pub(in crate::cli) fn render_query(pack: &crate::service::ContextResult, p: &Paint) {
    println!(
        "{} {}\n",
        p.bold("Relevant code for"),
        p.bold(&format!("\"{}\"", pack.task))
    );
    if let Some(git) = &pack.git {
        if !git.changed_files.is_empty() {
            println!(
                "{} {}",
                p.dim("git:"),
                p.dim(&format!(
                    "{} changed file{} ({})",
                    git.changed_files.len(),
                    if git.changed_files.len() == 1 {
                        ""
                    } else {
                        "s"
                    },
                    git.changed_files.join(", ")
                ))
            );
        }
        if let Some(latest) = git.recent_commits.first() {
            println!(
                "{} {}",
                p.dim("git:"),
                p.dim(&format!(
                    "latest commit {} \"{}\"",
                    latest.short_sha, latest.message
                ))
            );
        }
        println!();
    }
    if pack.items.is_empty() {
        println!(
            "Nothing matched. Try different words, or {}.",
            p.bold("oxide search <name>")
        );
        return;
    }
    // Grouped by file, files in order of their best-ranked item, items in
    // pack order within a file: a file heading is what the eye navigates
    // by, and the ranking still shows through the order of the headings.
    let name_width = pack
        .items
        .iter()
        .map(|item| item.evidence.qualified_name.chars().count())
        .max()
        .unwrap_or(0)
        .min(48);
    let mut files: Vec<&str> = Vec::new();
    for item in &pack.items {
        if !files.contains(&item.evidence.file.as_str()) {
            files.push(&item.evidence.file);
        }
    }
    for (i, file) in files.iter().enumerate() {
        if i > 0 {
            println!();
        }
        println!("{}", p.bold(file));
        for item in pack.items.iter().filter(|it| it.evidence.file == *file) {
            let ev = &item.evidence;
            let role = match item.role {
                crate::context::Role::Primary => String::new(),
                crate::context::Role::Dependency => "  dependency".to_string(),
                crate::context::Role::Test => "  test".to_string(),
            };
            let name = short_symbol_id(&ev.qualified_name);
            let pad = name_width.saturating_sub(name.chars().count());
            println!(
                "  {}{}  {}{}",
                p.accent(&name),
                " ".repeat(pad),
                p.dim(&format!(
                    "{}–{}  {}  ~{} tok",
                    ev.start_line,
                    ev.end_line,
                    ev.kind,
                    thousands(item.est_tokens)
                )),
                p.dim(&role)
            );
            println!(
                "    {}",
                p.dim(&format!("↳ {}", describe_reasons(&ev.reasons)))
            );
        }
    }
    if !pack.omitted.is_empty() {
        println!("\n{}", p.bold("Left out of the budget"));
        let names: Vec<String> = pack
            .omitted
            .iter()
            .map(|o| short_symbol_id(&o.id))
            .collect();
        let width = names.iter().map(|n| n.chars().count()).max().unwrap_or(0);
        for (name, omitted) in names.iter().zip(&pack.omitted) {
            println!(
                "  {name}{}  {}",
                " ".repeat(width - name.chars().count()),
                p.dim(&omitted.why)
            );
        }
    }
    println!(
        "\n{}",
        p.dim(&format!(
            "{} items · {} / {} context tokens · embedder {}",
            pack.items.len(),
            thousands(pack.used_tokens),
            thousands(pack.budget_tokens),
            pack.embedder
        ))
    );
}

pub(in crate::cli) fn render_review(
    ctx: &crate::review::ReviewContext,
    root: &std::path::Path,
    p: &Paint,
) {
    println!(
        "{} {} {}",
        p.bold("Review context for"),
        p.bold(&root.display().to_string()),
        p.dim(&format!("({})", ctx.range))
    );
    println!(
        "{}",
        p.dim(&format!("changed files: {}", ctx.changed_files.join(", ")))
    );
    if !ctx.recent_commits.is_empty() {
        let commits: Vec<String> = ctx
            .recent_commits
            .iter()
            .map(|c| format!("{} {}", c.short_sha, c.message))
            .collect();
        println!(
            "{}",
            p.dim(&format!("recent commits: {}", commits.join(" · ")))
        );
    }
    if !ctx.co_change.is_empty() {
        let pairs: Vec<String> = ctx
            .co_change
            .iter()
            .map(|e| format!("{}↔{} ({})", e.file, e.co_changed_with, e.count))
            .collect();
        println!("{}", p.dim(&format!("co-change: {}", pairs.join(", "))));
    }
    for c in &ctx.changed_symbols {
        println!(
            "\n{} {} {} {}",
            p.warn("● changed"),
            p.accent(&c.symbol.qualified_name),
            p.dim(&c.symbol.kind.to_string()),
            p.dim(&format!(
                "{}:{}–{} (+{})",
                c.symbol.file, c.symbol.start_line, c.symbol.end_line, c.added_lines
            ))
        );
    }
    for r in &ctx.related {
        println!(
            "\n{} {} {} {}\n  {}\n{}",
            p.dim("◇ related"),
            p.accent(&r.symbol.qualified_name),
            p.dim(&r.symbol.kind.to_string()),
            p.dim(&format!(
                "{}:{}–{}",
                r.symbol.file, r.symbol.start_line, r.symbol.end_line
            )),
            p.dim(&describe_reasons(&r.reasons)),
            read_snippet(
                &root.join(&r.symbol.file),
                r.symbol.start_line,
                r.symbol.end_line,
                16
            )
            .lines()
            .map(|line| format!("  {} {line}", p.dim("│")))
            .collect::<Vec<_>>()
            .join("\n")
        );
    }
}
