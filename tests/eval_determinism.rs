//! Regression: `oxide eval` must print the same bytes on every run.
//! `BenchConfig.repos` was a `HashMap`, so the per-repo row order (and the
//! f32 summation order behind the aggregate means) followed the process's
//! random hash seed; it is ordered by repo name now.

use oxide::eval::run_benchmark;
use std::collections::HashMap;
use std::path::PathBuf;
use std::process::Command;

const RUNS: usize = 4;

fn config() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("fixtures/benchmark.json")
}

#[test]
fn benchmark_report_is_byte_stable_and_grouped_by_sorted_repo_name() {
    let reports: Vec<String> = (0..RUNS)
        .map(|_| serde_json::to_string(&run_benchmark(&config()).unwrap()).unwrap())
        .collect();
    assert!(
        reports.windows(2).all(|w| w[0] == w[1]),
        "run_benchmark produced different reports across runs"
    );

    // Rows come repo by repo in repo-name order, queries in config order.
    let cfg: serde_json::Value =
        serde_json::from_str(&std::fs::read_to_string(config()).unwrap()).unwrap();
    let repo_of: HashMap<&str, &str> = cfg["queries"]
        .as_array()
        .unwrap()
        .iter()
        .map(|q| (q["id"].as_str().unwrap(), q["repo"].as_str().unwrap()))
        .collect();
    let report: serde_json::Value = serde_json::from_str(&reports[0]).unwrap();
    let repos: Vec<&str> = report["per_query"]
        .as_array()
        .unwrap()
        .iter()
        .map(|r| repo_of[r["id"].as_str().unwrap()])
        .collect();
    let mut sorted = repos.clone();
    sorted.sort();
    assert_eq!(
        repos, sorted,
        "per-query rows are not grouped in repo-name order"
    );
    assert!(
        sorted.first() != sorted.last(),
        "fixture should span more than one repo"
    );
}

#[test]
fn oxide_eval_stdout_is_identical_across_processes() {
    // Separate processes get separate hash seeds — the case that varied.
    // Byte equality alone would pass by chance when every process happens
    // to draw the same order, so the printed rows must also be grouped in
    // repo-name order (a buggy build passes both with probability 2^-6).
    let outputs: Vec<String> = (0..6)
        .map(|_| {
            let out = Command::new(env!("CARGO_BIN_EXE_oxide"))
                .args(["eval", "--config"])
                .arg(config())
                .env_remove("OXIDE_EMBED_URL")
                .output()
                .unwrap();
            assert!(
                out.status.success(),
                "{}",
                String::from_utf8_lossy(&out.stderr)
            );
            String::from_utf8(out.stdout).unwrap()
        })
        .collect();
    assert!(
        outputs.windows(2).all(|w| w[0] == w[1]),
        "oxide eval printed different output across runs"
    );
    let cfg: serde_json::Value =
        serde_json::from_str(&std::fs::read_to_string(config()).unwrap()).unwrap();
    let repo_of: HashMap<&str, &str> = cfg["queries"]
        .as_array()
        .unwrap()
        .iter()
        .map(|q| (q["id"].as_str().unwrap(), q["repo"].as_str().unwrap()))
        .collect();
    let repos: Vec<&str> = outputs[0]
        .lines()
        .filter_map(|l| l.split_whitespace().next())
        .filter_map(|id| repo_of.get(id).copied())
        .collect();
    let mut sorted = repos.clone();
    sorted.sort();
    assert!(!repos.is_empty());
    assert_eq!(
        repos, sorted,
        "printed rows are not grouped in repo-name order"
    );
}
