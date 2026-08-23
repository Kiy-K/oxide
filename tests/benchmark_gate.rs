//! The committed benchmark must run and hybrid retrieval must not lose to
//! vector-only on the aggregate. This is the honest-evidence gate.

use oxide::eval::run_benchmark;
use std::path::Path;

#[test]
fn benchmark_runs_and_hybrid_matches_or_beats_vector_only() {
    let manifest = Path::new(env!("CARGO_MANIFEST_DIR"));
    let report = run_benchmark(&manifest.join("fixtures/benchmark.json")).unwrap();

    assert_eq!(report.per_query.len(), 22, "11 queries × 2 modes");
    for r in &report.per_query {
        assert!(r.recall_at_k >= 0.0 && r.recall_at_k <= 1.0);
    }

    let get = |mode: &str| report.aggregate.iter().find(|a| a.mode == mode).unwrap();
    let vec_only = get("vector-only");
    let hybrid = get("hybrid");

    // Measured gate: structural + lexical evidence must not hurt, and should
    // help. If this fails after a ranking change, fix or honestly re-baseline.
    assert!(
        hybrid.mean_recall_at_k >= vec_only.mean_recall_at_k,
        "hybrid recall {:.3} lost to vector-only {:.3}",
        hybrid.mean_recall_at_k,
        vec_only.mean_recall_at_k
    );
}
