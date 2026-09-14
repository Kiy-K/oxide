//! Plain stdio JSON-RPC framing and an id-keyed request/response dispatcher.
//!
//! This crate has no async runtime feature (`tokio` here is `rt`-only, see
//! Cargo.toml), so the transport is a blocking child process plus one
//! dedicated reader thread — the same shape `retrieval.rs`'s
//! `std::thread::scope` embedding call already uses for the one place OXIDE
//! does concurrent I/O, just long-lived instead of scoped to one call.
//!
//! The reader thread matters for a reason found empirically against a real
//! server (`ty`, see docs/lsp-enrichment-eval/README.md): a language server
//! sends unsolicited notifications (`textDocument/publishDiagnostics`) on
//! the same stdout stream as ordinary responses, interleaved with them. A
//! transport that just reads "the next message" after sending a request
//! will happily return someone else's notification as if it were the
//! response. Every message from the wire is dispatched by JSON-RPC `id`;
//! anything without a matching id is buffered as a notification instead of
//! being handed back as a response.

use serde_json::Value;
use std::io::{BufRead, BufReader, Write};
use std::process::{Child, ChildStdin, Command, Stdio};
use std::sync::mpsc::{self, Receiver, RecvTimeoutError};
use std::time::{Duration, Instant};

/// Bound on buffered unsolicited notifications — a chatty server (diagnostics
/// on every `didOpen`) must never grow this without limit. Oldest dropped
/// first; OXIDE only ever consumes diagnostics via the pull model
/// (`textDocument/diagnostic`), so a dropped push notification costs nothing
/// today, and the buffer exists for forward compatibility, not a used path.
const NOTIFICATION_BUFFER_CAP: usize = 64;

#[derive(Debug)]
pub enum TransportError {
    /// The server process's stdout closed or the reader thread otherwise
    /// stopped — the process is presumed dead.
    Closed,
    /// No response with the matching id arrived within the deadline.
    Timeout,
    /// The server responded with a JSON-RPC error object.
    Rpc {
        code: i64,
        message: String,
    },
    Io(std::io::Error),
}

impl std::fmt::Display for TransportError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            TransportError::Closed => write!(f, "lsp server connection closed"),
            TransportError::Timeout => write!(f, "lsp request timed out"),
            TransportError::Rpc { code, message } => {
                write!(f, "lsp server error {code}: {message}")
            }
            TransportError::Io(e) => write!(f, "lsp transport io error: {e}"),
        }
    }
}
impl std::error::Error for TransportError {}

enum ReaderEvent {
    Message(Value),
    Closed,
}

/// A spawned server process plus its framing/dispatch machinery. One
/// transport = one server session; callers that want a fresh session after
/// a crash construct a new one rather than trying to resurrect this one.
pub struct Transport {
    child: Child,
    stdin: ChildStdin,
    rx: Receiver<ReaderEvent>,
    next_id: i64,
    /// Notifications seen while waiting for a request's response, oldest
    /// first, capped at [`NOTIFICATION_BUFFER_CAP`].
    notifications: Vec<Value>,
}

fn read_framed_message<R: BufRead>(r: &mut R) -> std::io::Result<Option<Value>> {
    let mut content_length: Option<usize> = None;
    loop {
        let mut line = String::new();
        let n = r.read_line(&mut line)?;
        if n == 0 {
            return Ok(None); // EOF before a full header block
        }
        let trimmed = line.trim_end_matches(['\r', '\n']);
        if trimmed.is_empty() {
            break; // blank line ends the header block
        }
        if let Some((name, value)) = trimmed.split_once(':') {
            if name.eq_ignore_ascii_case("content-length") {
                content_length = value.trim().parse().ok();
            }
        }
    }
    let Some(len) = content_length else {
        return Ok(None);
    };
    let mut body = vec![0u8; len];
    r.read_exact(&mut body)?;
    let value = serde_json::from_slice(&body)
        .map_err(|e| std::io::Error::new(std::io::ErrorKind::InvalidData, e))?;
    Ok(Some(value))
}

fn write_framed_message(w: &mut impl Write, value: &Value) -> std::io::Result<()> {
    let body = serde_json::to_vec(value)?;
    write!(w, "Content-Length: {}\r\n\r\n", body.len())?;
    w.write_all(&body)?;
    w.flush()
}

impl Transport {
    /// Spawn `program args...` with the given working directory and start the
    /// reader thread. Fails only if the process itself cannot be started
    /// (e.g. the binary is not on `$PATH`) — that is the caller's signal
    /// that this server is simply unavailable.
    pub fn spawn(program: &str, args: &[&str], cwd: &std::path::Path) -> std::io::Result<Self> {
        let mut child = Command::new(program)
            .args(args)
            .current_dir(cwd)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .spawn()?;
        let stdin = child.stdin.take().expect("piped stdin");
        let stdout = child.stdout.take().expect("piped stdout");
        let (tx, rx) = mpsc::channel();
        std::thread::spawn(move || {
            let mut reader = BufReader::new(stdout);
            loop {
                match read_framed_message(&mut reader) {
                    Ok(Some(v)) => {
                        if tx.send(ReaderEvent::Message(v)).is_err() {
                            break; // receiver gone
                        }
                    }
                    Ok(None) | Err(_) => {
                        let _ = tx.send(ReaderEvent::Closed);
                        break;
                    }
                }
            }
        });
        Ok(Self {
            child,
            stdin,
            rx,
            next_id: 1,
            notifications: Vec::new(),
        })
    }

    fn push_notification(&mut self, v: Value) {
        if self.notifications.len() >= NOTIFICATION_BUFFER_CAP {
            self.notifications.remove(0);
        }
        self.notifications.push(v);
    }

    /// Buffered notifications received while waiting on requests, drained.
    pub fn take_notifications(&mut self) -> Vec<Value> {
        std::mem::take(&mut self.notifications)
    }

    /// Send a JSON-RPC request and block for its response (or `timeout`).
    /// Any notification or unrelated message seen while waiting is buffered,
    /// never mistaken for the response — see the module doc.
    pub fn call(
        &mut self,
        method: &str,
        params: Value,
        timeout: Duration,
    ) -> Result<Value, TransportError> {
        let id = self.next_id;
        self.next_id += 1;
        let req = serde_json::json!({
            "jsonrpc": "2.0",
            "id": id,
            "method": method,
            "params": params,
        });
        write_framed_message(&mut self.stdin, &req).map_err(TransportError::Io)?;

        let deadline = Instant::now() + timeout;
        loop {
            let remaining = deadline.saturating_duration_since(Instant::now());
            if remaining.is_zero() {
                return Err(TransportError::Timeout);
            }
            match self.rx.recv_timeout(remaining) {
                Ok(ReaderEvent::Message(v)) => {
                    let is_response = v.get("id").map(|i| i.as_i64() == Some(id)).unwrap_or(false)
                        && v.get("method").is_none();
                    if !is_response {
                        // A message with both `method` and `id` is itself a
                        // server-to-client REQUEST (e.g. `workspace/
                        // configuration`, `client/registerCapability`), not
                        // a notification — found by review: silently
                        // buffering it like a notification leaves the
                        // server waiting forever for a reply it will never
                        // get, which can stall the server's own processing
                        // of the request we're actually waiting on. Answer
                        // it immediately with "method not found" so the
                        // server is never left hanging; this client
                        // implements no server-initiated requests.
                        if v.get("method").is_some() && v.get("id").is_some() {
                            let reply = serde_json::json!({
                                "jsonrpc": "2.0",
                                "id": v["id"],
                                "error": {
                                    "code": -32601,
                                    "message": "not supported by this client",
                                },
                            });
                            let _ = write_framed_message(&mut self.stdin, &reply);
                        } else {
                            self.push_notification(v);
                        }
                        continue;
                    }
                    if let Some(err) = v.get("error") {
                        let code = err.get("code").and_then(Value::as_i64).unwrap_or(0);
                        let message = err
                            .get("message")
                            .and_then(Value::as_str)
                            .unwrap_or("unknown lsp error")
                            .to_string();
                        return Err(TransportError::Rpc { code, message });
                    }
                    return Ok(v.get("result").cloned().unwrap_or(Value::Null));
                }
                Ok(ReaderEvent::Closed) => return Err(TransportError::Closed),
                Err(RecvTimeoutError::Timeout) => return Err(TransportError::Timeout),
                Err(RecvTimeoutError::Disconnected) => return Err(TransportError::Closed),
            }
        }
    }

    /// Send a one-way JSON-RPC notification (no response expected).
    pub fn notify(&mut self, method: &str, params: Value) -> Result<(), TransportError> {
        let msg = serde_json::json!({
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
        });
        write_framed_message(&mut self.stdin, &msg).map_err(TransportError::Io)
    }

    /// Best-effort shutdown/exit handshake, then kill if the process lingers.
    pub fn close(mut self) {
        let _ = self.call("shutdown", Value::Null, Duration::from_millis(500));
        let _ = self.notify("exit", Value::Null);
        let _ = self.child.wait_timeout_or_kill(Duration::from_millis(500));
    }
}

impl Drop for Transport {
    /// Best-effort safety net for every path that drops a `Transport`
    /// *without* calling `close()` — found by review: `LspClient::spawn`
    /// returns early (`?`) on an `initialize` failure/timeout, which used to
    /// drop `Transport` (and its `Child`) with no kill/wait at all.
    /// `std::process::Child::drop` does neither on its own, so a server that
    /// spawned successfully but never completed the handshake leaked one
    /// `ty server` process per failed `--lsp` attempt. `close()` already
    /// waits on the child itself, so `try_wait` here is `Some` by the time
    /// Drop runs on that path and this is a harmless no-op — the kill/wait
    /// below only fires for a `Transport` that was never closed.
    fn drop(&mut self) {
        if self.child.try_wait().ok().flatten().is_none() {
            let _ = self.child.kill();
            let _ = self.child.wait();
        }
    }
}

/// `Child::wait` with a bound, since the standard library has no built-in
/// timeout for process exit — poll `try_wait` and kill if the deadline
/// passes.
trait WaitTimeoutOrKill {
    fn wait_timeout_or_kill(&mut self, timeout: Duration) -> std::io::Result<()>;
}
impl WaitTimeoutOrKill for Child {
    fn wait_timeout_or_kill(&mut self, timeout: Duration) -> std::io::Result<()> {
        let deadline = Instant::now() + timeout;
        loop {
            if self.try_wait()?.is_some() {
                return Ok(());
            }
            if Instant::now() >= deadline {
                let _ = self.kill();
                let _ = self.wait();
                return Ok(());
            }
            std::thread::sleep(Duration::from_millis(20));
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Cursor;

    #[test]
    fn read_framed_message_parses_content_length_body() {
        let body = serde_json::json!({"jsonrpc":"2.0","id":1,"result":42});
        let bytes = serde_json::to_vec(&body).unwrap();
        let wire = format!("Content-Length: {}\r\n\r\n", bytes.len());
        let mut buf = wire.into_bytes();
        buf.extend_from_slice(&bytes);
        let mut reader = BufReader::new(Cursor::new(buf));
        let msg = read_framed_message(&mut reader).unwrap().unwrap();
        assert_eq!(msg["result"], 42);
    }

    #[test]
    fn read_framed_message_returns_none_on_clean_eof() {
        let mut reader = BufReader::new(Cursor::new(Vec::<u8>::new()));
        assert!(read_framed_message(&mut reader).unwrap().is_none());
    }

    fn harmless_child() -> Child {
        // `cat` with no args reads stdin until EOF/close and writes nothing
        // meaningful to stdout — enough to exercise `Transport.stdin`/`.child`
        // without depending on any real LSP server being installed.
        Command::new("cat")
            .stdin(Stdio::piped())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .spawn()
            .expect("spawn `cat` for the transport unit test")
    }

    /// Reproduces the exact failure a naive "read the next message"
    /// transport hits against `ty`: an unsolicited notification
    /// (`textDocument/publishDiagnostics`) arrives on the wire before the
    /// real response to an in-flight request. `call` must skip it and keep
    /// waiting for the id it actually sent, not return the notification as
    /// if it were the response.
    #[test]
    fn call_skips_interleaved_notifications_and_returns_the_matching_response() {
        let (tx, rx) = mpsc::channel();
        tx.send(ReaderEvent::Message(serde_json::json!({
            "jsonrpc": "2.0",
            "method": "textDocument/publishDiagnostics",
            "params": {"uri": "file:///x.py", "diagnostics": []}
        })))
        .unwrap();
        tx.send(ReaderEvent::Message(serde_json::json!({
            "jsonrpc": "2.0",
            "id": 1,
            "result": {"ok": true}
        })))
        .unwrap();

        let mut child = harmless_child();
        let stdin = child.stdin.take().unwrap();
        let mut transport = Transport {
            child,
            stdin,
            rx,
            next_id: 1,
            notifications: Vec::new(),
        };

        let result = transport
            .call(
                "textDocument/definition",
                Value::Null,
                Duration::from_secs(2),
            )
            .expect("must return the id-matched response, not the earlier notification");
        assert_eq!(result["ok"], true);
        let buffered = transport.take_notifications();
        assert_eq!(
            buffered.len(),
            1,
            "the notification must be buffered, not dropped or returned"
        );
        assert_eq!(buffered[0]["method"], "textDocument/publishDiagnostics");

        transport.close();
    }

    #[test]
    fn call_times_out_when_no_response_arrives() {
        let (_tx, rx) = mpsc::channel::<ReaderEvent>();
        let mut child = harmless_child();
        let stdin = child.stdin.take().unwrap();
        let mut transport = Transport {
            child,
            stdin,
            rx,
            next_id: 1,
            notifications: Vec::new(),
        };
        let err = transport
            .call("x", Value::Null, Duration::from_millis(50))
            .unwrap_err();
        assert!(matches!(err, TransportError::Timeout));
        transport.close();
    }
}
