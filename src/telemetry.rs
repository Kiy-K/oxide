//! Crash reporting: off unless the user opts in. `TELEMETRY.md` at the
//! repository root is the user-facing contract; this file is the whole
//! implementation of it, so the two should be read together.
//!
//! The only telemetry OXIDE has is Sentry crash (panic) reporting. Nothing
//! here runs, and no Sentry client exists, unless `OXIDE_TELEMETRY` is set
//! to an affirmative value — so an ordinary command cannot make a
//! telemetry network request, because there is no client to make one.

use std::sync::Arc;

/// Set to `1`, `true`, `yes`, or `on` to enable crash reporting.
pub const OPT_IN_VAR: &str = "OXIDE_TELEMETRY";

/// Where reports go when enabled. Overridable so a user can point OXIDE at
/// their own Sentry instance, or at a local listener to audit exactly what
/// leaves the machine (`tests/telemetry.rs` does the latter).
pub const DSN_VAR: &str = "OXIDE_TELEMETRY_DSN";

const DEFAULT_DSN: &str =
    "https://220500416743d04ad4597d88eec7cb8e@o4511784788557824.ingest.us.sentry.io/4512021829779456";

/// Whether the value of [`OPT_IN_VAR`] is an opt-in. Unset, empty, and
/// anything unrecognized all mean off — never on by default, and never on
/// by typo.
pub fn opted_in(value: Option<&str>) -> bool {
    matches!(
        value.map(|v| v.trim().to_ascii_lowercase()).as_deref(),
        Some("1" | "true" | "yes" | "on")
    )
}

/// Initialize crash reporting if, and only if, the environment opts in.
/// The returned guard must live for the whole process: dropping it flushes
/// any queued report on exit. `None` means no Sentry client was created.
pub fn init() -> Option<sentry::ClientInitGuard> {
    if !opted_in(std::env::var(OPT_IN_VAR).ok().as_deref()) {
        return None;
    }
    let dsn = std::env::var(DSN_VAR)
        .ok()
        .filter(|s| !s.is_empty())
        .unwrap_or_else(|| DEFAULT_DSN.to_string());
    Some(sentry::init((dsn, options())))
}

/// The client configuration used when enabled. Public so a test can assert
/// the privacy settings directly rather than inferring them from traffic.
pub fn options() -> sentry::ClientOptions {
    // ClientOptions is #[non_exhaustive], so it is built via Default plus
    // field assignment rather than a struct-update literal.
    let mut options = sentry::ClientOptions::default();
    options.release = sentry::release_name!();
    // Never attach the reporting machine's IP address or username.
    options.send_default_pii = false;
    // The contexts integration fills this with the hostname when unset;
    // an explicit empty value stops that, and `before_send` clears the
    // field outright.
    options.server_name = Some("".into());
    options.before_send = Some(Arc::new(scrub));
    options
}

/// Last step before an event leaves the process: strip every field that
/// could identify the machine or the user, whatever an integration added.
/// Stack frames keep function names and line numbers but lose their paths:
/// a from-source build records the builder's home directory in every
/// frame, and `cli.rs:120` locates a line in OXIDE just as well.
fn scrub(mut event: sentry::protocol::Event<'static>) -> Option<sentry::protocol::Event<'static>> {
    event.server_name = None;
    event.user = None;
    event.request = None;
    event.breadcrumbs = Default::default();
    event.extra.clear();
    if let Some(trace) = event.stacktrace.as_mut() {
        scrub_frames(trace);
    }
    for exception in event.exception.values.iter_mut() {
        if let Some(trace) = exception.stacktrace.as_mut() {
            scrub_frames(trace);
        }
        if let Some(trace) = exception.raw_stacktrace.as_mut() {
            scrub_frames(trace);
        }
    }
    for thread in event.threads.values.iter_mut() {
        if let Some(trace) = thread.stacktrace.as_mut() {
            scrub_frames(trace);
        }
        if let Some(trace) = thread.raw_stacktrace.as_mut() {
            scrub_frames(trace);
        }
    }
    Some(event)
}

fn scrub_frames(trace: &mut sentry::protocol::Stacktrace) {
    for frame in trace.frames.iter_mut() {
        frame.abs_path = None;
        frame.filename = frame.filename.take().map(|f| {
            std::path::Path::new(&f)
                .file_name()
                .map(|n| n.to_string_lossy().into_owned())
                .unwrap_or(f)
        });
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn only_affirmative_values_opt_in() {
        assert!(!opted_in(None));
        assert!(!opted_in(Some("")));
        assert!(!opted_in(Some("0")));
        assert!(!opted_in(Some("false")));
        assert!(!opted_in(Some("maybe")));
        for yes in ["1", "true", "TRUE", "yes", "on", " 1 "] {
            assert!(opted_in(Some(yes)), "{yes:?} should opt in");
        }
    }

    /// Positive control for `tests/telemetry.rs`: the same local-listener
    /// audit hook that proves silence by default also sees a report once
    /// enabled — a real panic, since that is the only thing OXIDE reports —
    /// and that report carries no hostname, username, or build path.
    #[test]
    fn an_enabled_client_reports_a_panic_to_the_configured_dsn_without_identity() {
        use std::io::Read;
        let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
        let port = listener.local_addr().unwrap().port();
        let guard = sentry::init((format!("http://key@127.0.0.1:{port}/1"), options()));
        // The panic hook the integration installs reports from any thread;
        // panicking on a helper thread keeps this one alive to read the wire.
        let _ = std::thread::spawn(|| panic!("oxide telemetry self-test")).join();
        let (mut stream, _) = listener.accept().expect("no report arrived");
        stream
            .set_read_timeout(Some(std::time::Duration::from_secs(5)))
            .unwrap();
        // Read exactly the request: headers, then Content-Length bytes of
        // body. The client keeps the socket open waiting for a response,
        // so reading to EOF would only time out.
        let mut request = Vec::new();
        let mut buf = [0u8; 4096];
        let mut body_end: Option<usize> = None;
        loop {
            if let Some(end) = body_end {
                if request.len() >= end {
                    break;
                }
            }
            let n = stream.read(&mut buf).expect("read failed");
            if n == 0 {
                break;
            }
            request.extend_from_slice(&buf[..n]);
            if body_end.is_none() {
                let head = String::from_utf8_lossy(&request).into_owned();
                if let Some(split) = head.find("\r\n\r\n") {
                    let length = head
                        .lines()
                        .find_map(|l| {
                            l.strip_prefix("content-length:")
                                .or(l.strip_prefix("Content-Length:"))
                        })
                        .and_then(|v| v.trim().parse::<usize>().ok())
                        .expect("Content-Length header");
                    body_end = Some(split + 4 + length);
                }
            }
        }
        drop(stream);
        drop(guard);
        let request = String::from_utf8_lossy(&request);
        assert!(request.contains("oxide telemetry self-test"), "{request}");
        assert!(
            !request.contains("server_name"),
            "hostname leaked:\n{request}"
        );
        assert!(
            !request.contains("abs_path"),
            "frame paths leaked:\n{request}"
        );
        assert!(
            !request.contains(env!("CARGO_MANIFEST_DIR")),
            "build path leaked:\n{request}"
        );
        assert!(
            request.contains("telemetry.rs"),
            "frames should keep their file names for triage:\n{request}"
        );
        if let Ok(user) = std::env::var("USER") {
            if !user.is_empty() {
                assert!(!request.contains(&user), "username leaked:\n{request}");
            }
        }
    }

    #[test]
    fn enabled_options_never_send_default_pii_or_a_hostname() {
        let options = options();
        assert!(!options.send_default_pii);
        let mut event = sentry::protocol::Event {
            server_name: Some("laptop.local".into()),
            ..Default::default()
        };
        event.extra.insert("k".into(), "v".into());
        let scrubbed = (options.before_send.as_ref().unwrap())(event).unwrap();
        assert_eq!(scrubbed.server_name, None);
        assert!(scrubbed.extra.is_empty());
    }
}
