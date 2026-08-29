# Phase 2.1 Client Compatibility

## Matrix

| client | version | isolated configuration | result | behavioral count |
|---|---|---|---|---:|
| OpenCode | 1.18.25 | temporary `OPENCODE_CONFIG`; `--pure`; codegraph/codebase-memory disabled | connected; tools render as `default.oxide_context` / `default.oxide_search` in event logs; compact instructions visible in C | evaluation runs |
| Claude Code | 2.1.251 | `--bare --mcp-config /tmp/oxide-claude-mcp.json` | initialized server `oxide`; tools render as `mcp__oxide__context` / `mcp__oxide__search`; model call blocked by `Not logged in` | 0 |
| Codex CLI | 0.150.1 | isolated `CODEX_HOME=/tmp/oxide-codex`; `codex mcp add oxide -- ...` | stdio server accepted/listed enabled; authentication unavailable for model run | 0 |

## OpenCode quirks

OpenCode prepends a local/default tool namespace to discovered MCP names:
`default.oxide_context` and `default.oxide_search`. This is client rendering,
not a server tool rename. The source server exposes exactly `context` and
`search`; no `oxide_oxide_*` identifiers are emitted by OXIDE. The B proxy strips
only `initialize.result.instructions`, leaving schemas and responses unchanged.

`--pure` does not make provider tokens available; the selected free provider's
model/account quota remains an external dependency. One attempted screen run
hit the Clinepass monthly limit and is labeled infrastructure failure, not a
behavioral result. Subsequent OpenCode runs used the available
`opencode/nemotron-3-ultra-free` model.

## Interference

Temporary configs disable CodeGraph and Codebase Memory. No other retrieval MCP
server is exposed. OpenCode's global ponytail plugin is not loaded by
`--pure`; all conditions use the same `--pure` mode. The ordinary developer
config has the ponytail plugin only; it is not used for benchmark arms.

## Structured errors

Protocol tests confirm `structuredContent.error` retains `code`, `action`, and
`message`. No authenticated model run was available to observe whether Claude
or Codex models branch on `structuredContent` versus the JSON text fallback.
