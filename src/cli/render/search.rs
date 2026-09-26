use crate::service::Evidence;
use crate::term::Paint;

pub(in crate::cli) fn describe_reasons(reasons: &[String]) -> String {
    let mut out: Vec<String> = Vec::with_capacity(reasons.len());
    for reason in reasons {
        let human = if let Some((rel, seed)) = reason.split_once('←') {
            let seed = short_symbol_id(seed);
            match rel {
                "test" => format!("tests {seed}"),
                "uses" => format!("used by {seed}"),
                "imported-definition" => format!("imported by {seed}"),
                "caller" | "ast-grep-caller" => format!("calls {seed}"),
                "blast-radius:caller" => format!("blast radius: calls {seed}"),
                "blast-radius:transitive-caller" => format!("blast radius: reaches {seed}"),
                "blast-radius:implementor" => format!("blast radius: implements {seed}"),
                "blast-radius:test" => format!("blast radius: tests {seed}"),
                "parent" => format!("parent of {seed}"),
                "child" => format!("child of {seed}"),
                "sibling" => format!("sibling of {seed}"),
                other => format!("{other} ← {seed}"),
            }
        } else {
            reason
                .split_once('=')
                .map_or(reason.as_str(), |(k, _)| k)
                .to_string()
        };
        if !out.contains(&human) {
            out.push(human);
        }
    }
    out.join(", ")
}

/// `caller` → `calls this`, keeping the human line readable without
/// repeating the seed's name on every row (the heading already carries it).
fn describe_blast_relation(relation: &str) -> &'static str {
    match relation {
        "caller" => "calls this",
        "transitive-caller" => "reaches this",
        "implementor" => "implements this",
        "test" => "tests this",
        _ => "related",
    }
}

pub(in crate::cli) fn render_evidence(hit: &Evidence, p: &Paint) -> String {
    let location = format!("{}:{}–{}", hit.file, hit.start_line, hit.end_line);
    let mut out = format!(
        "{}  {}  {}\n  {}\n{}",
        p.bold(&location),
        p.accent(&hit.qualified_name),
        p.dim(&hit.kind.to_string()),
        p.dim(&format!(
            "{} · score {:.4}",
            describe_reasons(&hit.reasons),
            hit.score
        )),
        hit.snippet
            .lines()
            .map(|line| format!("  {} {line}", p.dim("│")))
            .collect::<Vec<_>>()
            .join("\n")
    );
    if !hit.blast_radius.is_empty() {
        out.push_str(&format!("\n  {}", p.dim("blast radius")));
        for item in &hit.blast_radius {
            out.push_str(&format!(
                "\n    {} {}  {}",
                p.dim("·"),
                p.accent(&format!("{}:{}", item.file, item.start_line)),
                p.dim(&format!(
                    "{} {}",
                    item.qualified_name,
                    describe_blast_relation(item.relation)
                ))
            ));
        }
    }
    out
}

/// `path#Qualified.Name` -> `Qualified.Name`, and the module pseudo-symbol
/// `path#path:__module__` -> `path (module)`. The full id stays in `--json`;
/// this is only so the human "left out" list reads like symbol names.
pub(in crate::cli) fn short_symbol_id(id: &str) -> String {
    let name = id.split_once('#').map(|(_, rest)| rest).unwrap_or(id);
    match name.strip_suffix(":__module__") {
        Some(file) => format!("{file} (module)"),
        None => name.to_string(),
    }
}
pub(in crate::cli) fn render_search(hits: &[Evidence], p: &Paint) {
    for hit in hits {
        println!("{}", render_evidence(hit, p));
        println!();
    }
    if hits.is_empty() {
        eprintln!("no results");
    }
}

pub(in crate::cli) fn render_literal_search(result: &crate::literal::LiteralSearchResult) {
    for hit in &result.hits {
        println!("{}:{}:{}  {}", hit.file, hit.line, hit.column, hit.snippet);
    }
    if result.hits.is_empty() {
        eprintln!("no results");
    } else if result.truncated {
        eprintln!("(more matches exist; results truncated)");
    }
}
