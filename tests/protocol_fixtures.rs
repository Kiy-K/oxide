//! Cross-language contract fixtures (#36): `fixtures/protocol/` holds the real
//! `oxide ... --json` stdout of the binary, committed so `@oxide/protocol`
//! (`packages/protocol`) can validate it without building Rust, and
//! `fixtures/mcp/surface.json` holds the Rust MCP server's tool surface,
//! which `@oxide/mcp` serves verbatim. This test is
//! what keeps them honest: it reruns every command and fails on any byte
//! difference, so a Rust change to a machine-readable shape cannot land
//! without regenerating the fixtures — which then fail TS CI if the change
//! breaks the TS schemas.
//!
//! Regenerate deliberately with `mise run protocol:fixtures`
//! (`OXIDE_PROTOCOL_FIXTURES=update`); the ordinary run only compares.
//!
//! Determinism: the hashed embedder, a scrubbed environment (`env_clear`, so
//! no `OXIDE_*` setting or user config reaches the binary) and a fresh copy
//! of `fixtures/py_repo`. The only normalization is the temporary repository
//! path, replaced by `<repo>` wherever it appears (`status.root`, and error
//! messages that name the index path).

use std::io::{BufRead, BufReader, Write};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};

const UPDATE_ENV: &str = "OXIDE_PROTOCOL_FIXTURES";
const REPO_PLACEHOLDER: &str = "<repo>";

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

struct Run {
    code: Option<i32>,
    stdout: String,
}

fn oxide(cwd: &Path, home: &Path, args: &[&str]) -> Run {
    let out = Command::new(env!("CARGO_BIN_EXE_oxide"))
        .args(args)
        .current_dir(cwd)
        .env_clear()
        .env("HOME", home)
        .env("XDG_CONFIG_HOME", home)
        .env("NO_COLOR", "1")
        .env("OXIDE_EMBED_NATIVE", "hashed")
        .output()
        .unwrap();
    Run {
        code: out.status.code(),
        stdout: String::from_utf8(out.stdout).unwrap(),
    }
}

/// The Rust MCP server's agent-facing surface: its `instructions` and its
/// `tools/list` (names, descriptions, input schemas). `@oxide/mcp` serves
/// this file verbatim, so the TS adapter cannot drift from the canonical
/// server's schemas. `serverInfo.version` is left out: it is the crate
/// version, not part of the surface.
fn mcp_surface(cwd: &Path, home: &Path) -> Run {
    let mut child = Command::new(env!("CARGO_BIN_EXE_oxide"))
        .arg("mcp")
        .current_dir(cwd)
        .env_clear()
        .env("HOME", home)
        .env("XDG_CONFIG_HOME", home)
        .env("OXIDE_EMBED_NATIVE", "hashed")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .spawn()
        .unwrap();
    let mut stdin = child.stdin.take().unwrap();
    for message in [
        serde_json::json!({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-06-18", "capabilities": {},
            "clientInfo": {"name": "protocol-fixtures", "version": "0"}}}),
        serde_json::json!({"jsonrpc": "2.0", "method": "notifications/initialized"}),
        serde_json::json!({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}),
    ] {
        writeln!(stdin, "{message}").unwrap();
    }
    let mut replies = BufReader::new(child.stdout.take().unwrap()).lines();
    let mut reply = |id: u64| loop {
        let line = replies.next().expect("oxide mcp closed stdout").unwrap();
        let value: serde_json::Value = serde_json::from_str(&line).unwrap();
        if value["id"] == id {
            break value["result"].clone();
        }
    };
    let initialize = reply(1);
    let tools = reply(2);
    drop(stdin);
    child.wait().unwrap();
    let surface = serde_json::json!({
        "serverName": initialize["serverInfo"]["name"],
        "instructions": initialize["instructions"],
        "tools": tools["tools"],
    });
    Run {
        code: Some(0),
        stdout: format!("{}\n", serde_json::to_string_pretty(&surface).unwrap()),
    }
}

fn normalize(stdout: &str, repo: &Path) -> String {
    let mut text = stdout.to_string();
    let canonical = repo.canonicalize().unwrap();
    for path in [canonical.as_path(), repo] {
        text = text.replace(&path.display().to_string(), REPO_PLACEHOLDER);
    }
    text
}

struct Fixtures {
    dir: PathBuf,
    update: bool,
    mismatches: Vec<String>,
}

impl Fixtures {
    fn check(&mut self, name: &str, repo: &Path, run: Run, expected_code: i32) {
        assert_eq!(run.code, Some(expected_code), "{name}: {}", run.stdout);
        let actual = normalize(&run.stdout, repo);
        serde_json::from_str::<serde_json::Value>(&actual)
            .unwrap_or_else(|e| panic!("{name}: stdout is not JSON ({e}): {actual}"));
        let path = self.dir.join(format!("{name}.json"));
        if self.update {
            std::fs::write(&path, &actual).unwrap();
            return;
        }
        if std::fs::read_to_string(&path).ok().as_deref() != Some(actual.as_str()) {
            let out = std::env::temp_dir().join(format!("oxide-{}.json", name.replace('/', "-")));
            std::fs::write(&out, &actual).unwrap();
            self.mismatches
                .push(format!("{} (actual: {})", path.display(), out.display()));
        }
    }
}

#[test]
fn protocol_fixtures_match_the_binary() {
    let manifest = Path::new(env!("CARGO_MANIFEST_DIR"));
    let mut fixtures = Fixtures {
        dir: manifest.join("fixtures"),
        update: std::env::var(UPDATE_ENV).as_deref() == Ok("update"),
        mismatches: Vec::new(),
    };
    let home = tempfile::tempdir().unwrap();
    let home = home.path();
    let tmp = tempfile::tempdir().unwrap();
    let repo = tmp.path().join("repo");
    copy_dir(&manifest.join("fixtures/py_repo"), &repo);
    let r = repo.as_path();

    // Discovery finds neither `.git` nor `.oxide` above a fresh temp dir.
    let outside = tmp.path().join("outside");
    std::fs::create_dir(&outside).unwrap();
    let run = oxide(&outside, home, &["search", "retry", "--json"]);
    fixtures.check("protocol/error-repository-not-found", &outside, run, 1);

    let run = oxide(r, home, &["status", ".", "--json"]);
    fixtures.check("protocol/status-no-index", r, run, 0);
    let run = oxide(r, home, &["search", "retry", "--path", ".", "--json"]);
    fixtures.check("protocol/error-index-missing", r, run, 1);
    // Literal search scans repository text and needs no index.
    let args = [
        "search",
        "RetryPolicy",
        "--path",
        ".",
        "--mode",
        "literal",
        "--limit",
        "2",
        "--json",
    ];
    let run = oxide(r, home, &args);
    fixtures.check("protocol/literal-search", r, run, 0);
    let run = mcp_surface(r, home);
    fixtures.check("mcp/surface", r, run, 0);

    let run = oxide(r, home, &["index", ".", "--json"]);
    fixtures.check("protocol/index-fresh", r, run, 0);
    let run = oxide(r, home, &["index", ".", "--json"]);
    fixtures.check("protocol/index-unchanged", r, run, 0);

    let run = oxide(r, home, &["status", "--json"]);
    fixtures.check("protocol/status-current", r, run, 0);
    let q = "retry with exponential backoff";
    let run = oxide(r, home, &["search", q, "--limit", "3", "--json"]);
    fixtures.check("protocol/search-hybrid", r, run, 0);
    let run = oxide(
        r,
        home,
        &["search", q, "--limit", "2", "--blast-radius", "--json"],
    );
    fixtures.check("protocol/search-blast-radius", r, run, 0);
    let run = oxide(
        r,
        home,
        &["search", "zzqqxx", "--mode", "lexical", "--json"],
    );
    fixtures.check("protocol/search-empty", r, run, 0);
    let task = "where is retry logic";
    let run = oxide(
        r,
        home,
        &["query", task, "--budget-tokens", "600", "--json"],
    );
    fixtures.check("protocol/context", r, run, 0);
    let run = oxide(r, home, &["query", task, "--budget-tokens", "0", "--json"]);
    fixtures.check("protocol/context-empty", r, run, 0);

    // Edited after indexing: files no longer match, so `base_fresh` and
    // `is_current` flip while the embedding counts stay as indexed.
    let edited = r.join("oxidepy/cache.py");
    let mut text = std::fs::read_to_string(&edited).unwrap();
    text.push_str("# edited after indexing\n");
    std::fs::write(&edited, text).unwrap();
    let run = oxide(r, home, &["status", "--json"]);
    fixtures.check("protocol/status-stale", r, run, 0);

    assert!(
        fixtures.mismatches.is_empty(),
        "protocol fixtures differ from the binary's output; if the change is \
         intended, run `mise run protocol:fixtures` and make @oxide/protocol \
         accept it:\n  {}",
        fixtures.mismatches.join("\n  ")
    );
}
