# Phase 3.1 §16/17 — MCP error behavior and lifecycle, from the live server

Per advisor guidance: malformed-argument behavior is largely unreachable
from a real agent (the client validates against the JSON Schema before
ever sending the call, or a client may hide `-32602` from the model
entirely), so this section is split — protocol-level facts come from the
scripted client (`raw/mcp_probe.py`, deterministic, not an agent), and
`RepositoryService` failure-path presentation comes from real agent runs
where observed.

## Protocol-level facts (scripted client, `raw/mcp_probe_result.json`)

- **Malformed arguments are JSON-RPC protocol errors, not tool results**,
  confirmed against the live `rmcp` server:
  - Missing required `task` field on `context`: `{"error": {"code":
    -32602, "message": "task must be a string"}}`.
  - Wrong type (`task: 5` instead of a string): same `-32602` /
    `"task must be a string"`.
  - Unknown tool name: `{"error": {"code": -32602, "message": "tool not
    found"}}`.
  This matches the documented contract in `src/mcp.rs`'s module doc:
  shape errors are JSON-RPC `-32602`, not `isError: true` tool results —
  confirmed live, not just by reading the source.
- **`RepositoryService` failures are `isError: true` tool results carrying
  structured `{code, action, message}`**, confirmed live by pointing
  `context` at a subdirectory with no index:
  ```json
  {"result": {"content": [{"type": "text", "text": "{\"error\":{\"action\":\"index\",\"code\":\"index_missing\",\"message\":\"index missing at .../.oxide/index.db; run `oxide index ...`\"}}"}],
    "structuredContent": {"error": {"action": "index", "code": "index_missing", "message": "..."}},
    "isError": true}}
  ```
  Both the human-readable `content[0].text` (a JSON string) and the
  machine-readable `structuredContent.error` carry the same `{action,
  code, message}` — an agent can parse either.
- **Version negotiation echoes the client's requested version** (post-
  rmcp-migration behavior documented in `docs/mcp-phase-2-report.md`):
  requesting `protocolVersion: "2025-06-18"` gets back
  `"protocolVersion": "2025-06-18"` in the `initialize` response, not a
  hardcoded `2024-11-05`.
- **Repeated `search` calls in the same session are deterministic**: two
  identical `search` calls (different JSON-RPC `id`, same arguments)
  returned byte-identical `result` content (compared with the `id` field
  excluded, since that legitimately differs per JSON-RPC semantics).
- `tools/list` size: **590 chars / ~148 tokens** combined for both tools;
  server `instructions`: **280 chars / ~70 tokens** (see
  `context-economics.md`).

## Agent-observed failure/fallback behavior

See `failures.md` for per-run classification. Summary: `results.jsonl`'s
`transport` field lets us see, per real run, whether the model that had
MCP tools available but got a service error (e.g. an unindexed repo in a
condition where indexing wasn't pre-run) fell back to native tools or got
stuck — this phase's design deliberately pre-builds the index for every
condition except A specifically to keep the main matrix from measuring
error-fallback behavior by accident (protocol.md §4); dedicated
error-fallback runs, if included, are called out explicitly in
`failures.md` rather than mixed into the main activation numbers.

## Lifecycle

`opencode mcp list` under an isolated config confirms `initialize` →
`tools/list` → repeated `tools/call` all work end-to-end with the real
client (not just the scripted probe) — this is how tool visibility was
confirmed before the matrix ran (protocol.md §3). No client-facing
lifecycle quirks were observed beyond the ambient-`codegraph`-merge
behavior already documented in protocol.md §2 (a config-merging quirk of
OpenCode, not of OXIDE's `rmcp` server).
