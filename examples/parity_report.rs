//! Phase 3.4a Step 3/4 raw evidence: runs the handwritten extractor and the
//! tree-sitter-tags extractor over the same fixture files and prints a
//! structured diff. `cargo run --example parity_report > FILE`.

use oxide::parser::{extractor_for, extractor_for_handwritten, parse_file_with};
use oxide::symbols::{Language, Symbol};
use std::fs;
use std::path::Path;

fn walk(root: &str, ext: &str, out: &mut Vec<String>) {
    let Ok(rd) = fs::read_dir(root) else { return };
    for entry in rd.flatten() {
        let p = entry.path();
        if p.is_dir() {
            walk(p.to_str().unwrap(), ext, out);
        } else if p.extension().and_then(|e| e.to_str()) == Some(ext) {
            out.push(p.to_string_lossy().to_string());
        }
    }
}

fn fmt_sym(s: &Symbol) -> String {
    format!(
        "{:<9} {:<40} lines={:>4}-{:<4} exported={:<5} parent={:?}",
        format!("{:?}", s.kind),
        s.qualified_name,
        s.start_line,
        s.end_line,
        s.exported,
        s.parent
    )
}

fn compare(label: &str, files: &[String], lang: Language) {
    println!("\n########## {label} ##########");
    let mut total_old = 0usize;
    let mut total_new = 0usize;
    for f in files {
        let rel = f
            .strip_prefix(&format!(
                "{}/",
                Path::new(f).ancestors().nth(2).unwrap().display()
            ))
            .unwrap_or(f);
        let src = fs::read_to_string(f).unwrap();
        let old = parse_file_with(extractor_for_handwritten(lang), rel, &src, lang);
        let new = parse_file_with(extractor_for(lang), rel, &src, lang);
        total_old += old.len();
        total_new += new.len();

        let old_names: Vec<&str> = old.iter().map(|s| s.qualified_name.as_str()).collect();
        let new_names: Vec<&str> = new.iter().map(|s| s.qualified_name.as_str()).collect();
        let only_old: Vec<&&str> = old_names
            .iter()
            .filter(|n| !new_names.contains(n))
            .collect();
        let only_new: Vec<&&str> = new_names
            .iter()
            .filter(|n| !old_names.contains(n))
            .collect();

        if only_old.is_empty() && only_new.is_empty() {
            println!("--- {rel}: {} symbols, names match ---", old.len());
        } else {
            println!("--- {rel}: old={} new={} DIFF ---", old.len(), new.len());
            for n in &only_old {
                let s = old.iter().find(|s| &s.qualified_name == *n).unwrap();
                println!("  ONLY-OLD  {}", fmt_sym(s));
            }
            for n in &only_new {
                let s = new.iter().find(|s| &s.qualified_name == *n).unwrap();
                println!("  ONLY-NEW  {}", fmt_sym(s));
            }
        }
        // Field-level diff on names present in both.
        for n in old_names.iter().filter(|n| new_names.contains(n)) {
            let o = old
                .iter()
                .find(|s| &s.qualified_name.as_str() == n)
                .unwrap();
            let nw = new
                .iter()
                .find(|s| &s.qualified_name.as_str() == n)
                .unwrap();
            if o.kind != nw.kind {
                println!("  KIND-DIFF {n}: old={:?} new={:?}", o.kind, nw.kind);
            }
            if (o.start_line, o.end_line) != (nw.start_line, nw.end_line) {
                println!(
                    "  SPAN-DIFF {n}: old=({},{}) new=({},{})",
                    o.start_line, o.end_line, nw.start_line, nw.end_line
                );
            }
            if o.exported != nw.exported {
                println!("  EXPORT-DIFF {n}: old={} new={}", o.exported, nw.exported);
            }
            if o.parent != nw.parent {
                println!("  PARENT-DIFF {n}: old={:?} new={:?}", o.parent, nw.parent);
            }
        }
    }
    println!("TOTAL old={total_old} new={total_new} ({label})");
}

fn main() {
    let mut py = Vec::new();
    walk("fixtures/py_repo", "py", &mut py);
    py.sort();
    compare("PYTHON", &py, Language::Python);

    let mut ts = Vec::new();
    walk("fixtures/ts_repo", "ts", &mut ts);
    ts.sort();
    compare("TYPESCRIPT", &ts, Language::TypeScript);

    let mut tsx = Vec::new();
    walk("fixtures/ts_repo", "tsx", &mut tsx);
    tsx.sort();
    compare("TSX", &tsx, Language::Tsx);
}
