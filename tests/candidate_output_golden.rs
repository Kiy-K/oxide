//! Byte-for-byte output of every surface that carries candidate provenance
//! (`score`, `reasons`, role, order): search, context and review as the MCP
//! tools serialize them (`serde_json::to_string` of the service result), the
//! CLI's `--json` and human renderings, and the `OXIDE_DEBUG_DUMP_KEPT`
//! pool. Captured from the code before #34 S3 introduced the typed
//! candidate/evidence boundary, and never regenerated (one deliberate
//! re-capture, below): any difference is a regression, not a new baseline.
//! On a mismatch the actual output is written next to the build's temp dir
//! for diffing.
//!
//! One test in this binary on purpose: it sets `OXIDE_EMBED_NATIVE`,
//! `OXIDE_DEBUG_DUMP_KEPT` and the git configuration below, which are
//! process-global.
//!
//! Git is isolated from system/global config. `review`/`--git` pin the
//! diff settings measured to reshape hunks (`gitutil::diff_text`, #35); the
//! isolation keeps this test's own git calls (init/commit) and the settings
//! `diff_text` does not pin (listed on `gitutil::HUNK_ARGS`) away from the
//! host. `GIT_DIFF_OPTS` is an environment variable, not config, so a host
//! that sets it can still fail this test.
//!
//! The py_repo review section was re-captured when #35 pinned myers: the
//! golden had been captured on a `diff.algorithm=histogram` host, which split
//! one `oxidepy/retry.py` hunk differently (changed-symbol order and
//! `RetryPolicy`'s `+19` vs `+23` lines). Nothing else changed.

use oxide::index::IndexOptions;
use oxide::retrieval::{RetrievalMode, SearchMode};
use oxide::service::{RepositoryService, SearchRequest};
use serde_json::Value;
use std::path::Path;
use std::process::Command;

fn git(dir: &Path, args: &[&str]) {
    let st = Command::new("git")
        .args(args)
        .current_dir(dir)
        .env("GIT_AUTHOR_NAME", "t")
        .env("GIT_AUTHOR_EMAIL", "t@t")
        .env("GIT_COMMITTER_NAME", "t")
        .env("GIT_COMMITTER_EMAIL", "t@t")
        .status()
        .unwrap();
    assert!(st.success(), "git {args:?}");
}

fn copy_dir(from: &Path, to: &Path) {
    std::fs::create_dir_all(to).unwrap();
    for e in std::fs::read_dir(from).unwrap() {
        let e = e.unwrap();
        let dst = to.join(e.file_name());
        if e.file_type().unwrap().is_dir() {
            copy_dir(&e.path(), &dst);
        } else {
            std::fs::copy(e.path(), dst).unwrap();
        }
    }
}

/// `files` committed with `changed` cut in half, then two commits touching
/// `changed` and `partner` together (co-change history), then `changed`
/// restored in the working tree: the index is the full tree and `git diff
/// HEAD` has added lines, so `--git` and `review` get real changed seeds.
fn repo(files: &dyn Fn(&Path), changed: &str, partner: &str) -> tempfile::TempDir {
    let tmp = tempfile::tempdir().unwrap();
    let root = tmp.path();
    files(root);
    let full = std::fs::read_to_string(root.join(changed)).unwrap();
    let lines: Vec<&str> = full.lines().collect();
    std::fs::write(root.join(changed), lines[..lines.len() / 2].join("\n")).unwrap();
    git(root, &["init", "-q"]);
    git(root, &["add", "-A"]);
    git(root, &["commit", "-qm", "base"]);
    let comment = if changed.ends_with(".py") { "#" } else { "//" };
    for rev in 1..=2 {
        for f in [changed, partner] {
            let mut text = std::fs::read_to_string(root.join(f)).unwrap();
            text.push_str(&format!("\n{comment} rev {rev}\n"));
            std::fs::write(root.join(f), text).unwrap();
        }
        git(root, &["commit", "-qam", &format!("rev {rev}")]);
    }
    std::fs::write(root.join(changed), &full).unwrap();
    tmp
}

/// Test helpers under a root `tests/` directory and `*_test.py` files: the
/// context allocator's own test predicate and `symbols::is_test_symbol`
/// disagree on some of these (#25), and both decisions are pinned here.
fn roles_repo(root: &Path) {
    let w = |p: &str, s: &str| {
        let p = root.join(p);
        std::fs::create_dir_all(p.parent().unwrap()).unwrap();
        std::fs::write(p, s).unwrap();
    };
    w(
        "pkg/core.py",
        "def normalize(value):\n    return value.strip().lower()\n\n\ndef compute(value):\n    return normalize(value) + \"!\"\n",
    );
    w(
        "pkg/service.py",
        "from pkg.core import compute\n\n\nclass Service:\n    def run(self, value):\n        return compute(value)\n",
    );
    w(
        "tests/helpers.py",
        "from pkg.core import compute\n\n\ndef build_case():\n    return compute(\" Case \")\n",
    );
    w(
        "tests/test_core.py",
        "from pkg.core import compute, normalize\n\n\ndef test_compute():\n    assert compute(\" A \") == \"a!\"\n\n\ndef test_normalize():\n    assert normalize(\" B \") == \"b\"\n",
    );
    w(
        "pkg/core_test.py",
        "from pkg.core import compute\n\n\ndef check_compute():\n    assert compute(\"x\") == \"x!\"\n",
    );
}

/// Git history (hashes, dates) differs per run; everything else is stable.
fn strip_commits(mut v: Value) -> Value {
    if let Some(o) = v.as_object_mut() {
        o.remove("recent_commits");
        if let Some(g) = o.get_mut("git").and_then(Value::as_object_mut) {
            g.remove("recent_commits");
        }
    }
    v
}

fn cli(root: &Path, args: &[&str]) -> String {
    let out = Command::new(env!("CARGO_BIN_EXE_oxide"))
        .args(args)
        .current_dir(root)
        .env("OXIDE_EMBED_NATIVE", "hashed")
        .env("NO_COLOR", "1")
        .env_remove("OXIDE_DEBUG_DUMP_KEPT")
        .output()
        .unwrap();
    assert!(
        out.status.success(),
        "{args:?}: {}",
        String::from_utf8_lossy(&out.stderr)
    );
    String::from_utf8(out.stdout).unwrap()
}

/// `(name, write files, changed file, co-change partner, queries)`.
type Case<'a> = (
    &'static str,
    Box<dyn Fn(&Path) + 'a>,
    &'static str,
    &'static str,
    &'static [&'static str],
);

#[test]
fn candidate_output_matches_the_pre_s3_golden() {
    // This binary holds a single test, so nothing else reads the
    // environment concurrently.
    unsafe {
        std::env::set_var("OXIDE_EMBED_NATIVE", "hashed");
        std::env::set_var("GIT_CONFIG_NOSYSTEM", "1");
        std::env::set_var("GIT_CONFIG_GLOBAL", "/dev/null");
    }
    let fixtures = Path::new(env!("CARGO_MANIFEST_DIR")).join("fixtures");
    let cases: [Case<'_>; 3] = [
        (
            "py_repo",
            Box::new(|r: &Path| copy_dir(&fixtures.join("py_repo"), r)),
            "oxidepy/retry.py",
            "oxidepy/http_client.py",
            &[
                "retry with exponential backoff",
                "refresh auth token",
                "cache",
            ],
        ),
        (
            "ts_repo",
            Box::new(|r: &Path| copy_dir(&fixtures.join("ts_repo"), r)),
            "src/net/retry.ts",
            "src/net/client.ts",
            &["retry policy backoff", "auth service login", "button click"],
        ),
        (
            "roles",
            Box::new(roles_repo),
            "pkg/core.py",
            "pkg/service.py",
            &["compute normalized value", "build case helper"],
        ),
    ];
    let dump = tempfile::NamedTempFile::new().unwrap();
    let mut out = String::new();
    for (name, files, changed, partner, queries) in cases {
        let tmp = repo(&*files, changed, partner);
        let root = tmp.path();
        let service = RepositoryService::discover(Some(root.to_str().unwrap())).unwrap();
        service.index(None, &IndexOptions::default()).unwrap();
        for q in queries {
            for (mode, expand, rm, blast) in [
                (SearchMode::Hybrid, true, RetrievalMode::Balanced, false),
                (SearchMode::Hybrid, true, RetrievalMode::Quality, true),
                (SearchMode::Hybrid, false, RetrievalMode::Fast, false),
                (
                    SearchMode::LexicalOnly,
                    true,
                    RetrievalMode::Balanced,
                    false,
                ),
                (
                    SearchMode::VectorOnly,
                    false,
                    RetrievalMode::Balanced,
                    false,
                ),
            ] {
                let hits = service
                    .search(
                        q,
                        SearchRequest {
                            limit: 10,
                            mode,
                            expand,
                            retrieval_mode: rm,
                            blast_radius: blast,
                        },
                    )
                    .unwrap();
                out += &format!(
                    "== {name} search {q:?} {mode:?} expand={expand} {rm:?} blast={blast}\n{}\n",
                    serde_json::to_string(&hits).unwrap()
                );
            }
            for (rm, blast, git) in [
                (RetrievalMode::Balanced, false, false),
                (RetrievalMode::Balanced, true, true),
                (RetrievalMode::Quality, false, false),
            ] {
                unsafe { std::env::set_var("OXIDE_DEBUG_DUMP_KEPT", dump.path()) };
                let pack = service.context(q, 4000, rm, blast, git).unwrap();
                unsafe { std::env::remove_var("OXIDE_DEBUG_DUMP_KEPT") };
                let pack = strip_commits(serde_json::to_value(&pack).unwrap());
                out += &format!(
                    "== {name} context {q:?} {rm:?} blast={blast} git={git}\n{}\n== kept\n{}\n",
                    serde_json::to_string(&pack).unwrap(),
                    std::fs::read_to_string(dump.path()).unwrap()
                );
            }
        }
        let review = strip_commits(serde_json::to_value(service.review("").unwrap()).unwrap());
        out += &format!(
            "== {name} review\n{}\n",
            serde_json::to_string(&review).unwrap()
        );
        out += &format!(
            "== {name} cli search --json\n{}== {name} cli search\n{}== {name} cli context\n{}",
            cli(root, &["search", queries[0], "--json"]),
            cli(root, &["search", queries[0]]),
            cli(root, &["context", queries[0]]),
        );
    }
    let golden_path =
        Path::new(env!("CARGO_MANIFEST_DIR")).join("fixtures/candidate_output/golden.txt");
    let golden = std::fs::read_to_string(&golden_path).unwrap_or_default();
    if out != golden {
        let actual = Path::new(env!("CARGO_TARGET_TMPDIR")).join("candidate_output.actual.txt");
        std::fs::write(&actual, &out).unwrap();
        panic!(
            "candidate output differs from {}; actual written to {}",
            golden_path.display(),
            actual.display()
        );
    }
}
