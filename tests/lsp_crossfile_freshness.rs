//! Regression test for hardening-pass item #1: a cached `LspClient`
//! session must not serve stale cross-file evidence for a file it opened
//! for an *earlier* query, once that file has since been edited on disk —
//! even when the *current* query's own bounded scope doesn't happen to
//! include that file again.
//!
//! Uses `diagnostics`, not `incoming_calls`/`references`: those are
//! filtered to the query's own `scope_files` by `enrich_seeds`'
//! scope-then-cap discipline (`lsp_scope_regression.rs`), which would
//! confound "is the *content* fresh" with "is this file in *scope*" —
//! dropping a non-seed file from `scope_files` correctly drops its
//! results regardless of freshness, so that shape can't isolate this
//! fix. `diagnostics` is computed only for the seed's own file
//! (`enrich.rs`: `client.diagnostics(&uri)` on the seed's URI, no
//! scope_files filtering at all) but its *content* depends on whatever
//! other files the seed's file imports — exactly the cross-file
//! dependency this issue is about, with no scope-filter confound.
//!
//! Same direct `enrich_seeds` testing style as `lsp_scope_regression.rs`
//! (manually-built symbols/seeds/scope, one real `ty` session reused
//! across two calls) rather than going through the full retrieval
//! pipeline, so which files become seeds/scope is deterministic instead of
//! depending on search ranking.
//!
//! Skipped, not failed, when `ty` isn't installed.

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
fn a_non_seed_file_edited_after_an_earlier_query_opened_it_is_resynced_before_the_next_query() {
    if !ty_available() {
        eprintln!("skipping: `ty` not on PATH (install with `uv tool install ty`)");
        return;
    }

    let tmp = tempfile::tempdir().unwrap();
    let root = tmp.path();

    // helper.foo takes one argument; target.run calls it with exactly one
    // — valid, no diagnostic expected.
    write_file(root, "helper.py", "def foo(a):\n    return a\n");
    write_file(
        root,
        "target.py",
        "import helper\n\n\ndef run():\n    return helper.foo(1)\n",
    );

    let run_fn = sym("target.py", "run", "run", 4, 5);
    let seeds: Vec<&Symbol> = vec![&run_fn];

    let mut client = LspClient::spawn("ty", root, Duration::from_secs(15), Duration::from_secs(10))
        .expect("spawn ty");

    // 1. First query: target.py (run) is the seed. helper.py is a
    // non-seed file this query's own scope includes (target calls into
    // it) — opened via the scope_files loop, same mechanism a real
    // query's scope_files would use. No diagnostic expected: the call is
    // valid against helper.py's current (one-argument) signature.
    let helper_sym_v1 = sym("helper.py", "foo", "foo", 1, 2);
    let symbols_v1 = vec![run_fn.clone(), helper_sym_v1.clone()];
    let scope_files_v1 = vec!["target.py".to_string(), "helper.py".to_string()];
    let evidence_v1 = enrich_seeds(
        &mut client,
        root,
        &symbols_v1,
        &seeds,
        &scope_files_v1,
        5,
        5,
    );
    assert!(
        !evidence_v1
            .iter()
            .any(|e| e.reason.contains("lsp-diagnostic")),
        "first query's call is valid against helper.py's current signature, expected no \
         diagnostic: {:?}",
        evidence_v1.iter().map(|e| &e.reason).collect::<Vec<_>>()
    );

    // 2. Edit helper.py to require a SECOND argument — target.py's own
    // call (still `foo(1)`) is now genuinely invalid. helper.py is a file
    // that is NOT this session's seed and, deliberately, is dropped from
    // the SECOND query's own scope_files below too, so nothing in this
    // query would otherwise ever tell the server about the edit except
    // resync_open_documents catching it via the session's own remembered
    // open-document set. `diagnostics` isn't scope-filtered either way —
    // it's computed for target.py, the seed's own file — so this isolates
    // whether ty's cross-file type info for helper.py itself is fresh.
    write_file(root, "helper.py", "def foo(a, b):\n    return a + b\n");

    // 3. Second query: target.py (run) is the seed again, but scope_files
    // this time does NOT include helper.py. Must now see a diagnostic
    // reflecting helper.py's CURRENT (two-argument) signature — proving
    // the fix is resync-the-open-set, not merely "open whatever's in this
    // query's own scope".
    let helper_sym_v2 = sym("helper.py", "foo", "foo", 1, 2);
    let symbols_v2 = vec![run_fn.clone(), helper_sym_v2.clone()];
    let scope_files_v2 = vec!["target.py".to_string()];
    let evidence_v2 = enrich_seeds(
        &mut client,
        root,
        &symbols_v2,
        &seeds,
        &scope_files_v2,
        5,
        5,
    );
    client.close();

    assert!(
        evidence_v2
            .iter()
            .any(|e| e.reason.contains("lsp-diagnostic")),
        "second query must surface a diagnostic reflecting helper.py's post-edit (two-argument) \
         signature, not a stale pre-edit view: {:?}",
        evidence_v2.iter().map(|e| &e.reason).collect::<Vec<_>>()
    );
}
