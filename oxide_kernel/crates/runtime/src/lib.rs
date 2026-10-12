//! OXIDE v2 runtime: owns I/O and resources, calls the kernel, and serves the
//! versioned service contract (oxide_kernel/README.md § Service contract).
//!
//! The wire types live here, not in the kernel: kernel results are mapped
//! onto them, so the contract can change transport without touching domain
//! code.

pub mod capture;
pub mod decisionbench;
pub mod derivation;
pub mod embedding;
pub mod jev;
pub mod python;
pub mod repository;
mod session;

use std::io::{self, BufRead, Read, Write};

use serde::de::DeserializeOwned;
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};

use repository::DataLocation;
use session::{Failure, Sessions};

/// Service contract version. Bump on any incompatible request/response change.
pub const PROTOCOL_VERSION: u64 = 1;

/// Largest accepted request line, excluding its newline; anything bigger is
/// discarded unparsed and answered with `invalid_request`.
pub const MAX_REQUEST_BYTES: u64 = 1 << 20;

/// Every operation `handle` answers, as reported by `capabilities`.
pub const OPERATIONS: &[&str] = &[
    "status",
    "capabilities",
    "open_repository",
    "close_repository",
];

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ErrorCode {
    /// Not JSON, not UTF-8, too large, or not the envelope of this version.
    InvalidRequest,
    /// The request names a protocol version this runtime does not speak.
    UnsupportedVersion,
    /// Valid envelope, but no such operation in this version.
    UnknownOperation,
    // Operation failures: the request was valid, the operation could not
    // succeed. Only operations that take parameters produce them.
    /// The repository path is not absolute, does not resolve to a directory,
    /// or contains the application data location.
    InvalidPath,
    /// Not registered, or no published generation (never indexed, or only in
    /// an older storage format): index or rebuild it.
    NotIndexed,
    /// Stored state is corrupt or unusable; rebuild required.
    RepositoryUnavailable,
    /// Published only under another derivation, storage format or adapter
    /// encoding than this runtime's.
    IncompatibleDerivation,
    /// Another runtime process owns the repository's store.
    OwnershipConflict,
    /// No open repository session has this handle in this runtime.
    UnknownHandle,
    /// The runtime failed for a reason that is not the request's.
    InternalError,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Request {
    #[allow(dead_code)] // checked before the envelope is decoded
    version: u64,
    id: String,
    op: String,
    /// Operation parameters: required by operations that take them,
    /// rejected by those that do not.
    params: Option<Value>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct PathParams {
    path: String,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct HandleParams {
    handle: String,
}

#[derive(Serialize)]
struct StatusResult {
    kernel_version: &'static str,
}

#[derive(Serialize)]
struct CapabilitiesResult {
    protocol_version: u64,
    operations: &'static [&'static str],
    max_request_bytes: u64,
    /// `serial`: one request at a time, answered in arrival order.
    execution: &'static str,
}

/// Serves newline-delimited requests until EOF: one response line per
/// request, flushed, in arrival order. A final line without a newline is
/// still a request. Blank lines are skipped. Memory per request is bounded by
/// `MAX_REQUEST_BYTES`. Only I/O failures (e.g. a closed stdout) end it early.
/// Dropping `runtime` at EOF closes every repository session it still holds.
pub fn serve(
    mut runtime: Runtime,
    mut input: impl BufRead,
    mut output: impl Write,
) -> io::Result<()> {
    let mut line = Vec::new();
    loop {
        line.clear();
        if (&mut input)
            .take(MAX_REQUEST_BYTES + 1)
            .read_until(b'\n', &mut line)?
            == 0
        {
            return Ok(());
        }
        let terminated = line.last() == Some(&b'\n');
        if terminated {
            line.pop();
        }
        let response = if line.len() as u64 > MAX_REQUEST_BYTES {
            if !terminated {
                skip_line(&mut input)?;
            }
            error(None, ErrorCode::InvalidRequest, "request exceeds 1 MiB")
        } else if line.trim_ascii().is_empty() {
            continue;
        } else {
            match std::str::from_utf8(&line) {
                Ok(text) => runtime.handle(text),
                Err(_) => error(None, ErrorCode::InvalidRequest, "request is not UTF-8"),
            }
        };
        writeln!(output, "{response}")?;
        output.flush()?;
    }
}

/// Discards input through the next newline (or EOF) without buffering it.
fn skip_line(input: &mut impl BufRead) -> io::Result<()> {
    loop {
        let chunk = input.fill_buf()?;
        if chunk.is_empty() {
            return Ok(());
        }
        match chunk.iter().position(|&b| b == b'\n') {
            Some(at) => {
                input.consume(at + 1);
                return Ok(());
            }
            None => {
                let len = chunk.len();
                input.consume(len);
            }
        }
    }
}

/// The state one runtime process holds across requests.
pub struct Runtime {
    sessions: Sessions,
}

impl Runtime {
    /// `location` is the application data root holding the repository
    /// registry; without one, `open_repository` fails.
    pub fn new(location: Option<DataLocation>) -> Self {
        Self {
            sessions: Sessions::new(location),
        }
    }

    /// Answers one request. Every input yields a contract response;
    /// consumers branch on `ok` and `error.code`, never on `message`.
    pub fn handle(&mut self, text: &str) -> Value {
        let request = match envelope(text) {
            Ok(request) => request,
            Err(response) => return response,
        };
        let Request { id, op, params, .. } = request;
        match (op.as_str(), params) {
            ("status", None) => {
                let status = oxide_kernel::status();
                ok(
                    id,
                    StatusResult {
                        kernel_version: status.kernel_version,
                    },
                )
            }
            ("capabilities", None) => ok(
                id,
                CapabilitiesResult {
                    protocol_version: PROTOCOL_VERSION,
                    operations: OPERATIONS,
                    max_request_bytes: MAX_REQUEST_BYTES,
                    execution: "serial",
                },
            ),
            ("open_repository", Some(p)) => match decode::<PathParams>(p) {
                Some(p) => answer(id, self.sessions.open(&p.path)),
                None => bad_params(id, op),
            },
            ("close_repository", Some(p)) => match decode::<HandleParams>(p) {
                Some(p) => answer(id, self.sessions.close(&p.handle).map(|()| json!({}))),
                None => bad_params(id, op),
            },
            (op, _) if OPERATIONS.contains(&op) => bad_params(id, op.to_owned()),
            _ => error(Some(id), ErrorCode::UnknownOperation, "unknown operation"),
        }
    }
}

fn decode<T: DeserializeOwned>(params: Value) -> Option<T> {
    serde_json::from_value(params).ok()
}

fn bad_params(id: String, op: String) -> Value {
    error(
        Some(id),
        ErrorCode::InvalidRequest,
        &format!("invalid params for `{op}`"),
    )
}

fn answer(id: String, result: Result<impl Serialize, Failure>) -> Value {
    match result {
        Ok(result) => ok(id, result),
        Err(Failure(code, message)) => error(Some(id), code, &message),
    }
}

/// Decodes a version 1 envelope, or the contract error answering it.
fn envelope(text: &str) -> Result<Request, Value> {
    let Ok(value) = serde_json::from_str::<Value>(text) else {
        return Err(error(
            None,
            ErrorCode::InvalidRequest,
            "request is not JSON",
        ));
    };
    let id = value.get("id").and_then(Value::as_str).map(str::to_owned);
    // Version first: a newer envelope must get `unsupported_version`, not a
    // shape error from this version's decoder.
    match value.get("version").and_then(Value::as_u64) {
        Some(PROTOCOL_VERSION) => {}
        Some(_) => {
            return Err(error(
                id,
                ErrorCode::UnsupportedVersion,
                "unsupported protocol version",
            ));
        }
        None => {
            return Err(error(
                id,
                ErrorCode::InvalidRequest,
                "missing integer `version`",
            ));
        }
    }
    serde_json::from_value::<Request>(value).map_err(|_| {
        error(
            id,
            ErrorCode::InvalidRequest,
            "request does not match the version 1 envelope",
        )
    })
}

fn ok(id: String, result: impl Serialize) -> Value {
    json!({ "version": PROTOCOL_VERSION, "id": id, "ok": true, "result": result })
}

fn error(id: Option<String>, code: ErrorCode, message: &str) -> Value {
    json!({
        "version": PROTOCOL_VERSION,
        "id": id,
        "ok": false,
        "error": { "code": code, "message": message },
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Delivers its bytes `step` at a time, like a pipe under partial reads.
    struct Trickle<'a> {
        bytes: &'a [u8],
        step: usize,
    }

    impl Read for Trickle<'_> {
        fn read(&mut self, buf: &mut [u8]) -> io::Result<usize> {
            let n = self.step.min(buf.len()).min(self.bytes.len());
            buf[..n].copy_from_slice(&self.bytes[..n]);
            self.bytes = &self.bytes[n..];
            Ok(n)
        }
    }

    fn served_with(input: &[u8], step: usize) -> Vec<Value> {
        let mut out = Vec::new();
        let reader = io::BufReader::with_capacity(7, Trickle { bytes: input, step });
        serve(Runtime::new(None), reader, &mut out).unwrap();
        assert!(
            out.is_empty() || out.last() == Some(&b'\n'),
            "whole lines only"
        );
        out.split(|&b| b == b'\n')
            .filter(|l| !l.is_empty())
            .map(|l| serde_json::from_slice(l).unwrap())
            .collect()
    }

    fn served(input: &[u8]) -> Vec<Value> {
        served_with(input, usize::MAX)
    }

    fn status(id: &str) -> String {
        format!(r#"{{"version":1,"id":"{id}","op":"status"}}"#)
    }

    #[test]
    fn oversized_request_is_rejected_and_the_stream_continues() {
        let mut input = vec![b' '; MAX_REQUEST_BYTES as usize + 1];
        input.push(b'\n');
        input.extend(status("after").as_bytes());
        let answers = served(&input);
        assert_eq!(answers.len(), 2);
        assert_eq!(answers[0]["id"], Value::Null);
        assert_eq!(answers[0]["error"]["code"], "invalid_request");
        assert_eq!(answers[1]["id"], "after");
        // Exactly the limit is accepted (it is then just not JSON).
        let limit = vec![b'x'; MAX_REQUEST_BYTES as usize];
        assert_eq!(served(&limit)[0]["error"]["message"], "request is not JSON");
    }

    #[test]
    fn oversized_final_line_without_newline_is_rejected() {
        let big = vec![b' '; MAX_REQUEST_BYTES as usize + 1];
        assert_eq!(served(&big)[0]["error"]["code"], "invalid_request");
    }

    #[test]
    fn non_utf8_request_is_rejected() {
        assert_eq!(served(&[0xff, 0xfe])[0]["error"]["code"], "invalid_request");
    }

    #[test]
    fn many_frames_partial_reads_and_bad_lines_answer_in_order() {
        let input = format!(
            "{}\n\n{{\"version\":1,\n{}\r\n{{\"version\":2,\"id\":\"v\",\"op\":\"status\"}}\n{}",
            status("a"),
            status("b"),
            status("c"),
        );
        for step in [1, 3, usize::MAX] {
            let answers = served_with(input.as_bytes(), step);
            let ids: Vec<_> = answers.iter().map(|a| a["id"].clone()).collect();
            assert_eq!(
                Value::from(ids),
                json!(["a", null, "b", "v", "c"]),
                "step {step}"
            );
            assert_eq!(answers[1]["error"]["code"], "invalid_request");
            assert_eq!(answers[3]["error"]["code"], "unsupported_version");
        }
    }

    #[test]
    fn empty_input_answers_nothing() {
        assert!(served(b"").is_empty());
        assert!(served(b"\n  \n").is_empty());
    }

    #[test]
    fn capabilities_list_exactly_the_answered_operations() {
        let mut runtime = Runtime::new(None);
        let caps = runtime.handle(r#"{"version":1,"id":"c","op":"capabilities"}"#);
        assert_eq!(caps["result"]["operations"], json!(OPERATIONS));
        assert_eq!(caps["result"]["protocol_version"], PROTOCOL_VERSION);
        // Every listed operation is dispatched: wrong params get
        // `invalid_request`, never `unknown_operation`.
        for op in OPERATIONS {
            let answer = runtime.handle(&format!(
                r#"{{"version":1,"id":"x","op":"{op}","params":7}}"#
            ));
            assert_eq!(answer["error"]["code"], "invalid_request", "{op}");
        }
        let unknown = runtime.handle(r#"{"version":1,"id":"x","op":"index"}"#);
        assert_eq!(unknown["error"]["code"], "unknown_operation");
    }

    #[test]
    fn repository_operations_validate_params_and_paths() {
        let mut runtime = Runtime::new(None);
        let mut code = |request: &str| runtime.handle(request)["error"]["code"].clone();
        for bad in [
            r#"{"version":1,"id":"a","op":"open_repository"}"#,
            r#"{"version":1,"id":"a","op":"open_repository","params":{}}"#,
            r#"{"version":1,"id":"a","op":"open_repository","params":{"path":1}}"#,
            r#"{"version":1,"id":"a","op":"open_repository","params":{"path":"/","x":1}}"#,
            r#"{"version":1,"id":"a","op":"status","params":{}}"#,
        ] {
            assert_eq!(code(bad), "invalid_request", "{bad}");
        }
        for path in ["relative/repo", "/nonexistent/oxide/repo", "/etc/hostname"] {
            let request = format!(
                r#"{{"version":1,"id":"a","op":"open_repository","params":{{"path":"{path}"}}}}"#
            );
            assert_eq!(code(&request), "invalid_path", "{path}");
        }
        assert_eq!(
            code(r#"{"version":1,"id":"a","op":"close_repository","params":{"handle":"nope"}}"#),
            "unknown_handle"
        );
    }
}

pub mod storage;
