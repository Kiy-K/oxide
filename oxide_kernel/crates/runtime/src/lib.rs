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

use std::io::{self, BufRead, Read, Write};

use serde::{Deserialize, Serialize};
use serde_json::{Value, json};

/// Service contract version. Bump on any incompatible request/response change.
pub const PROTOCOL_VERSION: u64 = 1;

/// Largest accepted request line, excluding its newline; anything bigger is
/// discarded unparsed and answered with `invalid_request`.
pub const MAX_REQUEST_BYTES: u64 = 1 << 20;

/// Every operation `handle` answers, as reported by `capabilities`.
pub const OPERATIONS: &[&str] = &["status", "capabilities"];

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ErrorCode {
    /// Not JSON, not UTF-8, too large, or not the envelope of this version.
    InvalidRequest,
    /// The request names a protocol version this runtime does not speak.
    UnsupportedVersion,
    /// Valid envelope, but no such operation in this version.
    UnknownOperation,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Request {
    #[allow(dead_code)] // checked before the envelope is decoded
    version: u64,
    id: String,
    op: String,
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
pub fn serve(mut input: impl BufRead, mut output: impl Write) -> io::Result<()> {
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
                Ok(text) => handle(text),
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

/// Answers one request. Every input yields a contract response; consumers
/// branch on `ok` and `error.code`, never on `message`.
pub fn handle(text: &str) -> Value {
    let Ok(value) = serde_json::from_str::<Value>(text) else {
        return error(None, ErrorCode::InvalidRequest, "request is not JSON");
    };
    let id = value.get("id").and_then(Value::as_str).map(str::to_owned);
    // Version first: a newer envelope must get `unsupported_version`, not a
    // shape error from this version's decoder.
    match value.get("version").and_then(Value::as_u64) {
        Some(PROTOCOL_VERSION) => {}
        Some(_) => {
            return error(
                id,
                ErrorCode::UnsupportedVersion,
                "unsupported protocol version",
            );
        }
        None => return error(id, ErrorCode::InvalidRequest, "missing integer `version`"),
    }
    let Ok(request) = serde_json::from_value::<Request>(value) else {
        return error(
            id,
            ErrorCode::InvalidRequest,
            "request does not match the version 1 envelope",
        );
    };
    match request.op.as_str() {
        "status" => {
            let status = oxide_kernel::status();
            ok(
                request.id,
                StatusResult {
                    kernel_version: status.kernel_version,
                },
            )
        }
        "capabilities" => ok(
            request.id,
            CapabilitiesResult {
                protocol_version: PROTOCOL_VERSION,
                operations: OPERATIONS,
                max_request_bytes: MAX_REQUEST_BYTES,
                execution: "serial",
            },
        ),
        _ => error(
            Some(request.id),
            ErrorCode::UnknownOperation,
            "unknown operation",
        ),
    }
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
        serve(reader, &mut out).unwrap();
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
        let caps = handle(r#"{"version":1,"id":"c","op":"capabilities"}"#);
        assert_eq!(caps["result"]["operations"], json!(OPERATIONS));
        assert_eq!(caps["result"]["protocol_version"], PROTOCOL_VERSION);
        for op in OPERATIONS {
            let answer = handle(&format!(r#"{{"version":1,"id":"x","op":"{op}"}}"#));
            assert_eq!(answer["ok"], true, "{op}");
        }
        let unknown = handle(r#"{"version":1,"id":"x","op":"index"}"#);
        assert_eq!(unknown["error"]["code"], "unknown_operation");
    }
}

pub mod storage;
