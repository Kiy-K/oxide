//! End-to-end proof of `lsp::enrich`'s scope-then-cap discipline
//! (`src/lsp/enrich.rs`'s `scoped_and_capped`, unit-tested in isolation
//! there too) against a *real* `ty` server: a common function name is
//! called from several files, only one of which is in the seed pool's file
//! scope, and a small per-seed cap. A cap-then-scope bug would drop the
//! in-scope caller whenever it isn't near the front of the wire response;
//! scope-then-cap must always find it regardless of wire order.
//!
//! Skipped, not failed, when `ty` isn't installed — same optionality
//! contract as `native-embed`'s model-download tests. Install with
//! `uv tool install ty` to run this locally.

use oxide::lsp::enrich::enrich_seeds;
use oxide::lsp::LspClient;
use oxide::symbols::{Language, Symbol, SymbolKind};
use std::io::Write;
use std::path::Path;
use std::time::Duration;

fn ty_available() -> bool {
    std::process::Command::new("ty")
        .arg("--version")
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .status()
        .map(|s| s.success())
        .unwrap_or(false)
}

fn write_file(root: &Path, rel: &str, content: &str) {
    let p = root.join(rel);
    std::fs::create_dir_all(p.parent().unwrap()).unwrap();
    let mut f = std::fs::File::create(p).unwrap();
    f.write_all(content.as_bytes()).unwrap();
}

fn sym(file: &str, qname: &str, name: &str, start: u32, end: u32) -> Symbol {
    Symbol {
        qualified_name: qname.into(),
        name: name.into(),
        kind: SymbolKind::Function,
        language: Language::Python,
        file: file.into(),
        start_line: start,
        end_line: end,
        content_hash: 0,
        signature: String::new(),
        imports: vec![],
        exported: true,
        parent: None,
        references: vec![],
        calls: vec![],
        bases: vec![],
    }
}

#[test]
fn in_scope_caller_survives_even_when_many_out_of_scope_callers_sort_first() {
    if !ty_available() {
        eprintln!("skipping: `ty` not on PATH (install with `uv tool install ty`)");
        return;
    }

    let tmp = tempfile::tempdir().unwrap();
    let root = tmp.path();

    write_file(root, "target.py", "def run():\n    return 1\n");

    // Several genuine callers OUTSIDE the seed pool's scope — alphabetically
    // first, so a server that returns results in file order puts them
    // ahead of the one in-scope caller on the wire.
    for i in 0..6 {
        write_file(
            root,
            &format!("noise_{i}.py"),
            "import target\n\n\ndef caller():\n    return target.run()\n",
        );
    }

    // The one in-scope caller — sorts LAST alphabetically among callers, so
    // it's the worst case for a cap-then-scope bug.
    write_file(
        root,
        "zzz_seed_pool_file.py",
        "import target\n\n\ndef caller_in_scope():\n    return target.run()\n",
    );

    let target_fn = sym("target.py", "run", "run", 1, 2);
    let caller_sym = sym(
        "zzz_seed_pool_file.py",
        "caller_in_scope",
        "caller_in_scope",
        4,
        5,
    );
    let symbols = vec![target_fn.clone(), caller_sym.clone()];
    let seeds: Vec<&Symbol> = vec![&target_fn];
    // Scope deliberately excludes every noise file.
    let scope_files = vec!["zzz_seed_pool_file.py".to_string()];

    let mut client = LspClient::spawn("ty", root, Duration::from_secs(15), Duration::from_secs(10))
        .expect("spawn ty");

    // A tight cap of 1: with 6 out-of-scope callers ahead of the in-scope
    // one, a cap-then-scope bug truncates to those 6 (or however many sort
    // first) before ever checking scope, and the in-scope caller never
    // survives.
    let evidence = enrich_seeds(&mut client, root, &symbols, &seeds, &scope_files, 1, 1);
    client.close();

    let found_in_scope = evidence
        .iter()
        .any(|e| e.symbol.qualified_name == "caller_in_scope");
    assert!(
        found_in_scope,
        "scope-then-cap must surface the in-scope caller regardless of wire order; got: {:?}",
        evidence
            .iter()
            .map(|e| (&e.symbol.qualified_name, &e.reason))
            .collect::<Vec<_>>()
    );
    assert!(
        evidence
            .iter()
            .all(|e| e.symbol.file == "zzz_seed_pool_file.py" || e.symbol.file == "target.py"),
        "no out-of-scope evidence should ever appear: {:?}",
        evidence.iter().map(|e| &e.symbol.file).collect::<Vec<_>>()
    );
}
