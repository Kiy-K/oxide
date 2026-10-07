//! OXIDE v2 runtime: owns I/O and resources, calls the kernel, and serves the
//! versioned service contract (oxide_kernel/README.md § Service contract).
//!
//! The wire types live here, not in the kernel: kernel results are mapped
//! onto them, so the contract can change transport without touching domain
//! code.

pub mod capture;
pub mod derivation;
pub mod embedding;
pub mod repository;

use std::io::{self, Read, Write};

use serde::{Deserialize, Serialize};
use serde_json::{Value, json};

/// Service contract version. Bump on any incompatible request/response change.
pub const PROTOCOL_VERSION: u64 = 1;

/// Largest accepted request; anything bigger is rejected unread.
pub const MAX_REQUEST_BYTES: u64 = 1 << 20;

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

/// Reads one bounded request from `input` and writes one response line.
pub fn serve(input: impl Read, mut output: impl Write) -> io::Result<()> {
    let mut bytes = Vec::new();
    input.take(MAX_REQUEST_BYTES + 1).read_to_end(&mut bytes)?;
    let response = if bytes.len() as u64 > MAX_REQUEST_BYTES {
        error(None, ErrorCode::InvalidRequest, "request exceeds 1 MiB")
    } else {
        match std::str::from_utf8(&bytes) {
            Ok(text) => handle(text),
            Err(_) => error(None, ErrorCode::InvalidRequest, "request is not UTF-8"),
        }
    };
    writeln!(output, "{response}")?;
    output.flush()
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

    fn served(input: &[u8]) -> Value {
        let mut out = Vec::new();
        serve(input, &mut out).unwrap();
        assert_eq!(out.last(), Some(&b'\n'), "one response line");
        serde_json::from_slice(&out).unwrap()
    }

    #[test]
    fn oversized_request_is_rejected() {
        let big = vec![b' '; MAX_REQUEST_BYTES as usize + 1];
        assert_eq!(served(&big)["error"]["code"], "invalid_request");
    }

    #[test]
    fn non_utf8_request_is_rejected() {
        assert_eq!(served(&[0xff, 0xfe])["error"]["code"], "invalid_request");
    }
}

pub mod storage;
