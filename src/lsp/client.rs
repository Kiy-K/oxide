//! Typed LSP requests over [`super::transport::Transport`], plus the
//! Symbol↔LSP position/URI translation this crate's own `Symbol` type needs.
//!
//! Request *params* are built as raw `serde_json::json!` values — the exact
//! shape already validated by hand against a real `ty` server (see
//! docs/lsp-enrichment-eval/README.md) — rather than via `lsp_types`'
//! builder structs, which would need every optional sub-field
//! (`work_done_progress_params`, `partial_result_params`, ...) threaded
//! through for no benefit on the write side. Response *bodies* are
//! deserialized into `lsp_types` structs, which is where getting field names
//! and case conversion exactly right actually matters.

use super::transport::{Transport, TransportError};
use anyhow::{Context, Result};
use lsp_types::{
    CallHierarchyIncomingCall, CallHierarchyItem, Diagnostic, DocumentDiagnosticReport,
    DocumentDiagnosticReportResult, GotoDefinitionResponse, Location, Position,
    PositionEncodingKind, ServerCapabilities, Uri,
};
use std::collections::HashSet;
use std::path::Path;
use std::str::FromStr;
use std::time::Duration;

/// One spawned server session: the process, its negotiated capabilities, and
/// which documents have been `didOpen`'d so far. Read-only from OXIDE's
/// point of view — there is no `didChange` support because this client never
/// edits anything.
pub struct LspClient {
    transport: Transport,
    capabilities: ServerCapabilities,
    root: std::path::PathBuf,
    opened: HashSet<String>,
    timeout: Duration,
}

/// Percent-encode every byte outside RFC 3986's unreserved set plus `/`
/// (path separator, kept literal). Found by review: a path built with plain
/// `format!` fails `Uri::from_str`'s strict RFC 3986 parser outright for any
/// repo path containing a space, `#`, `%`, or non-ASCII byte — this isn't a
/// cosmetic gap, it's the difference between LSP enrichment working at all
/// on such a repo and silently returning no evidence for every seed in it.
fn percent_encode_path(path: &str) -> String {
    let mut out = String::with_capacity(path.len());
    for b in path.bytes() {
        match b {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'_' | b'.' | b'~' | b'/' => {
                out.push(b as char)
            }
            _ => out.push_str(&format!("%{b:02X}")),
        }
    }
    out
}

/// Inverse of [`percent_encode_path`], for turning an LSP-returned `file://`
/// URI back into a plain repo-relative path comparable against `Symbol.file`.
fn percent_decode(s: &str) -> String {
    let bytes = s.as_bytes();
    let mut out = Vec::with_capacity(bytes.len());
    let mut i = 0;
    while i < bytes.len() {
        if bytes[i] == b'%' && i + 2 < bytes.len() {
            if let Ok(byte) = u8::from_str_radix(&s[i + 1..i + 3], 16) {
                out.push(byte);
                i += 3;
                continue;
            }
        }
        out.push(bytes[i]);
        i += 1;
    }
    String::from_utf8_lossy(&out).into_owned()
}

fn file_uri(root: &Path, rel_path: &str) -> Result<Uri> {
    let abs = root.join(rel_path);
    let s = format!("file://{}", percent_encode_path(&abs.to_string_lossy()));
    Uri::from_str(&s).map_err(|e| anyhow::anyhow!("invalid file uri {s}: {e:?}"))
}

impl LspClient {
    /// Spawn `program` in `root` and complete the `initialize`/`initialized`
    /// handshake within `init_timeout` (generous — a server's first response
    /// can include cold-start work no later request repeats). Errors here
    /// (binary missing, `initialize` failed or timed out) mean "this server
    /// is unavailable" — the caller falls back to no LSP evidence, per the
    /// task's "safe to fall back from" contract. `request_timeout` is the
    /// per-request deadline every later call (`definition`, `references`,
    /// ...) uses.
    pub fn spawn(
        program: &str,
        root: &Path,
        init_timeout: Duration,
        request_timeout: Duration,
    ) -> Result<Self> {
        let mut transport = Transport::spawn(program, &["server"], root)
            .with_context(|| format!("spawning `{program} server`"))?;
        let root_uri = format!("file://{}", root.display());
        let init_params = serde_json::json!({
            "processId": std::process::id(),
            "rootUri": root_uri,
            "capabilities": {
                "textDocument": {
                    "definition": {"linkSupport": true},
                    "references": {},
                    "callHierarchy": {},
                    "typeHierarchy": {},
                    "publishDiagnostics": {},
                    "synchronization": {"didSave": false},
                },
                "general": {
                    // Confirmed against a real server (ty 0.0.80): offering
                    // utf-8 first gets it negotiated back, which means every
                    // LSP column below is a byte offset — no UTF-16
                    // code-unit conversion needed. See docs/lsp-enrichment-eval/README.md.
                    "positionEncodings": ["utf-8"]
                },
                "workspace": {"symbol": {}},
            },
            "clientInfo": {"name": "oxide", "version": env!("CARGO_PKG_VERSION")},
        });
        let result = transport
            .call("initialize", init_params, init_timeout)
            .map_err(transport_err)
            .context("lsp initialize")?;
        let capabilities: ServerCapabilities = serde_json::from_value(
            result
                .get("capabilities")
                .cloned()
                .unwrap_or(serde_json::Value::Null),
        )
        .context("parsing ServerCapabilities")?;
        // Fail closed, not silently wrong: this client computes every LSP
        // position as a byte offset (see `symbol_position`'s doc comment),
        // which is only correct once the server has actually confirmed
        // `utf-8`. Per the LSP 3.17 spec, an absent `positionEncoding` means
        // the server is using the protocol default (UTF-16), not our offer —
        // treating that as "close enough" would silently misplace every
        // position on any line containing non-ASCII text instead of falling
        // back to no LSP evidence.
        match &capabilities.position_encoding {
            Some(enc) if *enc == PositionEncodingKind::UTF8 => {}
            other => anyhow::bail!(
                "server negotiated position encoding {other:?}, not utf-8 (no UTF-16 support in this client)"
            ),
        }
        transport
            .notify("initialized", serde_json::json!({}))
            .map_err(transport_err)
            .context("lsp initialized notification")?;
        Ok(Self {
            transport,
            capabilities,
            root: root.to_path_buf(),
            opened: HashSet::new(),
            timeout: request_timeout,
        })
    }

    pub fn capabilities(&self) -> &ServerCapabilities {
        &self.capabilities
    }

    /// Open `rel_path` if not already open this session, sending its current
    /// on-disk text — this client only ever reads, so there is no
    /// `didChange` to keep in sync.
    pub fn ensure_open(&mut self, rel_path: &str) -> Result<Uri> {
        let uri = file_uri(&self.root, rel_path)?;
        let key = uri.as_str().to_string();
        if !self.opened.contains(&key) {
            let text = std::fs::read_to_string(self.root.join(rel_path))
                .with_context(|| format!("reading {rel_path} to open in lsp"))?;
            self.transport
                .notify(
                    "textDocument/didOpen",
                    serde_json::json!({
                        "textDocument": {
                            "uri": key,
                            "languageId": "python",
                            "version": 1,
                            "text": text,
                        }
                    }),
                )
                .map_err(transport_err)?;
            self.opened.insert(key);
        }
        Ok(uri)
    }

    fn text_document_position(uri: &Uri, pos: Position) -> serde_json::Value {
        serde_json::json!({
            "textDocument": {"uri": uri.as_str()},
            "position": {"line": pos.line, "character": pos.character},
        })
    }

    /// Shared by `definition`/`implementations`: both LSP methods answer
    /// with the exact same `Location | Location[] | LocationLink[] | null`
    /// union (`lsp_types::GotoImplementationResponse` is a type alias for
    /// `GotoDefinitionResponse`).
    fn goto_like(&mut self, method: &str, uri: &Uri, pos: Position) -> Result<Vec<Location>> {
        let params = Self::text_document_position(uri, pos);
        let result = self
            .transport
            .call(method, params, self.timeout)
            .map_err(transport_err)?;
        if result.is_null() {
            return Ok(Vec::new());
        }
        let resp: GotoDefinitionResponse =
            serde_json::from_value(result).context("parsing goto-like response")?;
        Ok(match resp {
            GotoDefinitionResponse::Scalar(l) => vec![l],
            GotoDefinitionResponse::Array(v) => v,
            GotoDefinitionResponse::Link(links) => links
                .into_iter()
                .map(|l| Location::new(l.target_uri, l.target_range))
                .collect(),
        })
    }

    pub fn definition(&mut self, uri: &Uri, pos: Position) -> Result<Vec<Location>> {
        self.goto_like("textDocument/definition", uri, pos)
    }

    /// Repo-wide by construction, same caveat as [`Self::references`].
    pub fn implementations(&mut self, uri: &Uri, pos: Position) -> Result<Vec<Location>> {
        self.goto_like("textDocument/implementation", uri, pos)
    }

    /// Repo-wide by construction — same contract as
    /// `RelationGraph::callers_of`. Callers MUST scope-then-cap before use;
    /// this method itself applies no bound beyond the wire response.
    pub fn references(&mut self, uri: &Uri, pos: Position) -> Result<Vec<Location>> {
        let mut params = Self::text_document_position(uri, pos);
        params["context"] = serde_json::json!({"includeDeclaration": false});
        let result = self
            .transport
            .call("textDocument/references", params, self.timeout)
            .map_err(transport_err)?;
        if result.is_null() {
            return Ok(Vec::new());
        }
        serde_json::from_value(result).context("parsing references response")
    }

    /// `prepareCallHierarchy` at `pos`, then `callHierarchy/incomingCalls` on
    /// the first resolved item (mirrors the probe: a position inside a
    /// symbol resolves to exactly one hierarchy item in practice). Repo-wide
    /// by construction, same caveat as [`Self::references`].
    pub fn incoming_calls(
        &mut self,
        uri: &Uri,
        pos: Position,
    ) -> Result<Vec<CallHierarchyIncomingCall>> {
        let prep_params = Self::text_document_position(uri, pos);
        let prep = self
            .transport
            .call(
                "textDocument/prepareCallHierarchy",
                prep_params,
                self.timeout,
            )
            .map_err(transport_err)?;
        let items: Vec<CallHierarchyItem> = if prep.is_null() {
            Vec::new()
        } else {
            serde_json::from_value(prep).context("parsing prepareCallHierarchy response")?
        };
        let Some(item) = items.into_iter().next() else {
            return Ok(Vec::new());
        };
        let item_json = serde_json::to_value(&item)?;
        let result = self
            .transport
            .call(
                "callHierarchy/incomingCalls",
                serde_json::json!({"item": item_json}),
                self.timeout,
            )
            .map_err(transport_err)?;
        if result.is_null() {
            return Ok(Vec::new());
        }
        serde_json::from_value(result).context("parsing incomingCalls response")
    }

    /// Pull diagnostics for `uri` (3.17 `textDocument/diagnostic`). Scoped to
    /// one file per call by design — this crate never issues
    /// `workspace/diagnostic`, which is repo-wide and unbounded.
    pub fn diagnostics(&mut self, uri: &Uri) -> Result<Vec<Diagnostic>> {
        let params = serde_json::json!({"textDocument": {"uri": uri.as_str()}});
        let result = self
            .transport
            .call("textDocument/diagnostic", params, self.timeout)
            .map_err(transport_err)?;
        if result.is_null() {
            return Ok(Vec::new());
        }
        let report: DocumentDiagnosticReportResult =
            serde_json::from_value(result).context("parsing diagnostic report")?;
        Ok(match report {
            DocumentDiagnosticReportResult::Report(DocumentDiagnosticReport::Full(r)) => {
                r.full_document_diagnostic_report.items
            }
            DocumentDiagnosticReportResult::Report(DocumentDiagnosticReport::Unchanged(_)) => {
                Vec::new()
            }
            DocumentDiagnosticReportResult::Partial(_) => Vec::new(),
        })
    }

    pub fn close(self) {
        self.transport.close();
    }
}

fn transport_err(e: TransportError) -> anyhow::Error {
    anyhow::anyhow!("{e}")
}

/// 0-based LSP position of `symbol`'s bare name on its declaration line.
/// `Symbol` has 1-based lines and no columns at all, so this locates the
/// first occurrence of `symbol_name` on `symbol_start_line`'s source text.
/// Byte offset, not a code-point/UTF-16 count — safe because the client
/// negotiates `positionEncoding: utf-8` at `spawn` (see its doc comment).
pub fn symbol_position(src: &str, symbol_start_line: u32, symbol_name: &str) -> Option<Position> {
    let line = src
        .lines()
        .nth(symbol_start_line.saturating_sub(1) as usize)?;
    let col = line.find(symbol_name)?;
    Some(Position {
        line: symbol_start_line.saturating_sub(1),
        character: col as u32,
    })
}

/// Repo-relative path from a `file://` URI under `root`, or `None` if the
/// URI isn't a `file://` URI under `root` (e.g. a stdlib/site-packages
/// location the corpus never indexed).
pub fn uri_to_repo_relative(root: &Path, uri: &Uri) -> Option<String> {
    let s = uri.as_str();
    let path_str = s.strip_prefix("file://")?;
    let decoded = percent_decode(path_str);
    let path = std::path::Path::new(&decoded);
    let rel = path.strip_prefix(root).ok()?;
    Some(rel.to_string_lossy().replace('\\', "/"))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn file_uri_round_trips_paths_with_spaces_and_special_characters() {
        let root = Path::new("/home/user/My Projects/repo #1");
        let uri = file_uri(root, "src/a b.py").expect("space/# path must still parse as a URI");
        let rel = uri_to_repo_relative(root, &uri);
        assert_eq!(rel.as_deref(), Some("src/a b.py"));
    }

    #[test]
    fn symbol_position_locates_the_name_on_the_declaration_line() {
        let src = "class RetryPolicy:\n    def should_retry(self, attempt, error):\n        pass\n";
        let pos = symbol_position(src, 2, "should_retry").unwrap();
        assert_eq!(pos.line, 1);
        let line = src.lines().nth(1).unwrap();
        assert_eq!(
            &line[pos.character as usize..][.."should_retry".len()],
            "should_retry"
        );
    }

    #[test]
    fn uri_to_repo_relative_strips_the_root_prefix() {
        let root = Path::new("/home/user/proj");
        let uri = Uri::from_str("file:///home/user/proj/oxidepy/retry.py").unwrap();
        assert_eq!(
            uri_to_repo_relative(root, &uri).as_deref(),
            Some("oxidepy/retry.py")
        );
    }

    #[test]
    fn uri_to_repo_relative_is_none_outside_the_root() {
        let root = Path::new("/home/user/proj");
        let uri = Uri::from_str("file:///usr/lib/python3.13/typing.py").unwrap();
        assert_eq!(uri_to_repo_relative(root, &uri), None);
    }
}
