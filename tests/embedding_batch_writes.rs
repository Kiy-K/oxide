//! `IndexBackend::put_embeddings_batch` must be observationally identical
//! to calling `put_embedding` once per item — it's a write-path
//! optimization (one transaction per chunk instead of one autocommit per
//! row; docs/indexing-rebuild-scopes/README.md), not a behavior change.

use oxide::embeddings::{EmbeddingProvider, HashedEmbedder};
use oxide::index::{IndexBackend, SqliteStore};
use oxide::symbols::{content_hash, Language, Symbol, SymbolKind};
use std::path::Path;

fn make_symbols(n: usize) -> Vec<Symbol> {
    (0..n)
        .map(|i| Symbol {
            qualified_name: format!("module{i}.func{i}"),
            name: format!("func{i}"),
            kind: SymbolKind::Function,
            language: Language::Python,
            file: format!("src/module{i}.py"),
            start_line: 1,
            end_line: 5,
            content_hash: content_hash(&format!("def func{i}(): pass")),
            signature: format!("def func{i}():"),
            imports: vec![],
            exported: true,
            parent: None,
            references: vec![],
            calls: vec![],
            bases: vec![],
        })
        .collect()
}

#[test]
fn batched_writes_are_identical_to_one_call_per_item() {
    let symbols = make_symbols(50);
    let emb = HashedEmbedder::default();
    let vectors: Vec<(u64, Vec<f32>)> = symbols
        .iter()
        .map(|s| (s.id(), emb.embed_document(&s.signature)))
        .collect();

    let mut per_item = SqliteStore::open(Path::new(":memory:")).unwrap();
    for s in &symbols {
        per_item
            .replace_file(&s.file, s.content_hash, std::slice::from_ref(s), &[])
            .unwrap();
    }
    for (id, vec) in &vectors {
        per_item.put_embedding(*id, vec).unwrap();
    }

    let mut batched = SqliteStore::open(Path::new(":memory:")).unwrap();
    for s in &symbols {
        batched
            .replace_file(&s.file, s.content_hash, std::slice::from_ref(s), &[])
            .unwrap();
    }
    for chunk in vectors.chunks(7) {
        // Odd chunk size on purpose: exercises a final partial chunk.
        batched.put_embeddings_batch(chunk).unwrap();
    }

    let a = per_item.all_embeddings().unwrap();
    let b = batched.all_embeddings().unwrap();
    assert_eq!(a.len(), b.len());
    for (id, (hash_a, vec_a)) in &a {
        let (hash_b, vec_b) = b.get(id).expect("symbol missing from batched store");
        assert_eq!(hash_a, hash_b, "content_hash mismatch for symbol {id}");
        assert_eq!(vec_a, vec_b, "vector mismatch for symbol {id}");
    }
}

#[test]
fn batched_writes_skip_symbol_ids_with_no_matching_row_same_as_put_embedding() {
    let symbols = make_symbols(2);
    let mut store = SqliteStore::open(Path::new(":memory:")).unwrap();
    for s in &symbols {
        store
            .replace_file(&s.file, s.content_hash, std::slice::from_ref(s), &[])
            .unwrap();
    }
    let bogus_id = 999_999_999_u64;
    let items = vec![
        (symbols[0].id(), vec![1.0_f32, 2.0]),
        (bogus_id, vec![3.0_f32, 4.0]),
    ];
    store.put_embeddings_batch(&items).unwrap();

    let stored = store.all_embeddings().unwrap();
    assert!(stored.contains_key(&symbols[0].id()));
    assert!(
        !stored.contains_key(&bogus_id),
        "a symbol id with no matching row must be silently skipped, not stored"
    );
}

#[test]
fn empty_batch_is_a_harmless_no_op() {
    let mut store = SqliteStore::open(Path::new(":memory:")).unwrap();
    store.put_embeddings_batch(&[]).unwrap();
    assert!(store.all_embeddings().unwrap().is_empty());
}
