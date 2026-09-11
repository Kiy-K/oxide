//! The privacy contract from `TELEMETRY.md`: an ordinary command makes no
//! telemetry network request unless `OXIDE_TELEMETRY` opts in.
//!
//! The binary is pointed at a local listener through `OXIDE_TELEMETRY_DSN`
//! — the same audit hook a user can use — so "no request" is observed on
//! the wire, not inferred from configuration. The positive control (a
//! report *does* reach such a listener once enabled) lives next to the
//! implementation in `src/telemetry.rs`, where the Sentry API is in scope.

use std::net::TcpListener;
use std::path::Path;
use std::process::{Command, Output};

fn listener() -> (TcpListener, String) {
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    listener.set_nonblocking(true).unwrap();
    let port = listener.local_addr().unwrap().port();
    (listener, format!("http://publickey@127.0.0.1:{port}/1"))
}

fn run(root: &Path, dsn: &str, opt_in: Option<&str>, args: &[&str]) -> Output {
    let mut cmd = Command::new(env!("CARGO_BIN_EXE_oxide"));
    cmd.args(args)
        .env("OXIDE_EMBED_NATIVE", "hashed")
        .env("OXIDE_TELEMETRY_DSN", dsn)
        .env_remove("OXIDE_TELEMETRY")
        .current_dir(root);
    if let Some(value) = opt_in {
        cmd.env("OXIDE_TELEMETRY", value);
    }
    cmd.output().unwrap()
}

fn assert_no_connection(listener: &TcpListener, what: &str) {
    match listener.accept() {
        Err(e) if e.kind() == std::io::ErrorKind::WouldBlock => {}
        Ok((_, peer)) => panic!("{what} opened a telemetry connection from {peer}"),
        Err(e) => panic!("listener failed: {e}"),
    }
}

#[test]
fn ordinary_commands_make_no_telemetry_request_by_default() {
    let tmp = tempfile::tempdir().unwrap();
    std::fs::create_dir(tmp.path().join(".git")).unwrap();
    std::fs::write(
        tmp.path().join("app.py"),
        "def handler(request):\n    return request\n",
    )
    .unwrap();
    let (listener, dsn) = listener();

    // Successes, a structured failure, and a malformed invocation: none may
    // touch the network, whether telemetry is unset or explicitly off.
    for opt_in in [None, Some(""), Some("0"), Some("false"), Some("off")] {
        for args in [
            vec!["--version"],
            vec!["search", "handler"], // index missing -> exit 1
            vec!["index", ".", "--json"],
            vec!["status", "--json"],
            vec!["query", "where is the handler", "--json"],
            vec!["search", "handler", "--json"],
            vec!["install", "--dry-run", "--agent", "all"],
            vec!["definitely-not-a-command"], // clap error -> exit 2
        ] {
            let out = run(tmp.path(), &dsn, opt_in, &args);
            assert_no_connection(
                &listener,
                &format!("OXIDE_TELEMETRY={opt_in:?} `oxide {}`", args.join(" ")),
            );
            // Sanity: the run actually executed the command path.
            let combined = format!(
                "{}{}",
                String::from_utf8_lossy(&out.stdout),
                String::from_utf8_lossy(&out.stderr)
            );
            assert!(
                !combined.is_empty(),
                "`oxide {}` printed nothing",
                args.join(" ")
            );
        }
    }
}
