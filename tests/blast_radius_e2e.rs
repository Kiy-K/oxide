//! End-to-end contract for `--blast-radius` on `oxide search` and
//! `oxide query`, over the real binary.
//!
//! The two properties that matter most here are the *negative* ones: with
//! the flag absent, both commands must be byte-identical to a build without
//! the feature (no field in the JSON, no change to which results come back
//! or how they rank), and with it present the extra evidence must stay
//! bounded and deterministic. `src/blast_radius.rs`'s unit tests pin the
//! caps against synthetic graphs; this pins the wiring — flag plumbing,
//! serialization, and the fact that ranking never consults it.

use serde_json::Value;
use std::path::Path;
use std::process::{Command, Output};

fn write(root: &Path, relative: &str, source: &str) {
    let path = root.join(relative);
    std::fs::create_dir_all(path.parent().unwrap()).unwrap();
    std::fs::write(path, source).unwrap();
}

fn run(root: &Path, args: &[&str]) -> Output {
    Command::new(env!("CARGO_BIN_EXE_oxide"))
        .args(args)
        .env("OXIDE_EMBED_NATIVE", "hashed")
        .current_dir(root)
        .output()
        .unwrap()
}

fn json_stdout(output: &Output) -> Value {
    assert!(
        output.status.success(),
        "stderr: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    serde_json::from_slice(&output.stdout).unwrap()
}

/// A repo where the thing being searched for is defined in one file and
/// used from several others that a name/embedding search has no reason to
/// surface on its own — which is the entire situation blast radius exists
/// for, and the reason it cannot reuse `context.rs`'s seed-pool file scope.
fn staged() -> tempfile::TempDir {
    let tmp = tempfile::tempdir().unwrap();
    write(
        tmp.path(),
        "src/store.py",
        "class TokenStore:\n    def refresh(self, token):\n        return token\n",
    );
    for (n, verb) in [(1, "alpha"), (2, "beta"), (3, "gamma")] {
        write(
            tmp.path(),
            &format!("src/handler{n}.py"),
            &format!(
                "from .store import TokenStore\n\n\ndef {verb}_handler(request):\n    store = TokenStore()\n    return store.refresh(request)\n"
            ),
        );
    }
    write(
        tmp.path(),
        "src/cache.py",
        "from .store import TokenStore\n\n\nclass MemoryStore(TokenStore):\n    def refresh(self, token):\n        return token\n",
    );
    write(
        tmp.path(),
        "tests/test_store.py",
        "from src.store import TokenStore\n\n\ndef test_token_store():\n    assert TokenStore()\n",
    );
    assert!(run(tmp.path(), &["index", ".", "--json"]).status.success());
    tmp
}

#[test]
fn absent_flag_leaves_search_and_query_json_byte_identical() {
    let tmp = staged();
    // Byte-level, not field-level: a `blast_radius: []` that serialized as
    // an empty array would still break an `additionalProperties`-strict
    // consumer and would still cost tokens in an agent's context window.
    let search = run(tmp.path(), &["search", "TokenStore", "--json"]);
    let search_text = String::from_utf8(search.stdout.clone()).unwrap();
    assert!(
        !search_text.contains("blast_radius"),
        "search --json must carry no blast_radius field when not asked: {search_text}"
    );
    let query = run(tmp.path(), &["query", "refresh a token", "--json"]);
    let query_text = String::from_utf8(query.stdout.clone()).unwrap();
    assert!(
        !query_text.contains("blast"),
        "query --json must carry no blast-radius evidence when not asked"
    );
    // And running twice agrees, so the comparison below is against a stable
    // baseline rather than against run-to-run noise.
    assert_eq!(
        search_text,
        String::from_utf8(run(tmp.path(), &["search", "TokenStore", "--json"]).stdout).unwrap()
    );
}

#[test]
fn search_ranking_is_untouched_and_evidence_is_attached_to_its_seed() {
    let tmp = staged();
    let plain = json_stdout(&run(tmp.path(), &["search", "TokenStore", "--json"]));
    let blasted = json_stdout(&run(
        tmp.path(),
        &["search", "TokenStore", "--json", "--blast-radius"],
    ));

    let ids = |v: &Value| -> Vec<String> {
        v.as_array()
            .unwrap()
            .iter()
            .map(|h| h["id"].as_str().unwrap().to_string())
            .collect()
    };
    let scores = |v: &Value| -> Vec<String> {
        v.as_array()
            .unwrap()
            .iter()
            .map(|h| h["score"].to_string())
            .collect()
    };
    assert_eq!(ids(&plain), ids(&blasted), "blast radius reordered results");
    assert_eq!(
        scores(&plain),
        scores(&blasted),
        "blast radius changed a score"
    );

    let hits = blasted.as_array().unwrap();
    let with_radius: Vec<&Value> = hits
        .iter()
        .filter(|h| h.get("blast_radius").is_some())
        .collect();
    assert!(
        !with_radius.is_empty(),
        "no hit carried a blast radius: {blasted:#}"
    );
    let mut saw_caller = false;
    for hit in &with_radius {
        for item in hit["blast_radius"].as_array().unwrap() {
            // Every member names the seed it was reached from, and that seed
            // is the hit it is attached to.
            assert_eq!(item["via"], hit["qualified_name"]);
            assert!(item["id"].as_str().unwrap().contains('#'));
            assert!(item["distance"].as_u64().unwrap() >= 1);
            if item["relation"] == "caller" {
                saw_caller = true;
            }
        }
    }
    assert!(saw_caller, "expected direct callers: {blasted:#}");
}

#[test]
fn search_blast_radius_reaches_files_the_search_itself_did_not_return() {
    let tmp = staged();
    let blasted = json_stdout(&run(
        tmp.path(),
        &[
            "search",
            "TokenStore",
            "--limit",
            "2",
            "--json",
            "--blast-radius",
        ],
    ));
    let hits = blasted.as_array().unwrap();
    let returned: Vec<&str> = hits.iter().map(|h| h["file"].as_str().unwrap()).collect();
    let reached: Vec<&str> = hits
        .iter()
        .filter_map(|h| h.get("blast_radius"))
        .flat_map(|b| b.as_array().unwrap())
        .map(|i| i["file"].as_str().unwrap())
        .collect();
    assert!(
        reached.iter().any(|f| !returned.contains(f)),
        "blast radius only re-reported files search already returned: {blasted:#}"
    );
}

#[test]
fn output_is_deterministic_and_bounded_across_repeated_runs() {
    let tmp = staged();
    let once = json_stdout(&run(
        tmp.path(),
        &["search", "TokenStore", "--json", "--blast-radius"],
    ));
    for _ in 0..4 {
        assert_eq!(
            once,
            json_stdout(&run(
                tmp.path(),
                &["search", "TokenStore", "--json", "--blast-radius"],
            ))
        );
    }
    let total: usize = once
        .as_array()
        .unwrap()
        .iter()
        .filter_map(|h| h.get("blast_radius"))
        .map(|b| b.as_array().unwrap().len())
        .sum();
    // Matches `config::BLAST_RADIUS_MAX_ITEMS`. Asserted as a literal on
    // purpose: the whole contract is "small and bounded", so a change to
    // that constant should have to be made twice, deliberately.
    assert!(total <= 12, "{total} members is not a neighborhood");
}

#[test]
fn query_keeps_blast_radius_inside_the_existing_token_budget() {
    let tmp = staged();
    for budget in ["256", "1024"] {
        let pack = json_stdout(&run(
            tmp.path(),
            &[
                "query",
                "refresh a token",
                "--budget-tokens",
                budget,
                "--json",
                "--blast-radius",
            ],
        ));
        let used = pack["used_tokens"].as_u64().unwrap();
        let limit = pack["budget_tokens"].as_u64().unwrap();
        assert_eq!(limit, budget.parse::<u64>().unwrap());
        assert!(
            used <= limit,
            "blast radius pushed the pack past its budget: {used} > {limit}"
        );
    }
}

#[test]
fn query_labels_blast_radius_evidence_in_its_reasons() {
    let tmp = staged();
    let pack = json_stdout(&run(
        tmp.path(),
        &[
            "query",
            "TokenStore refresh",
            "--budget-tokens",
            "2048",
            "--json",
            "--blast-radius",
        ],
    ));
    let reasons: Vec<String> = pack["items"]
        .as_array()
        .unwrap()
        .iter()
        .flat_map(|i| i["reasons"].as_array().unwrap())
        .map(|r| r.as_str().unwrap().to_string())
        .collect();
    assert!(
        reasons.iter().any(|r| r.starts_with("blast-radius:")),
        "blast-radius members must carry their own provenance tag: {reasons:?}"
    );
}
