# OXIDE v0.1 MCP Phase 2 Report

## 1. Architecture and footprint

The MCP transport is a short newline-delimited JSON-RPC adapter in
`src/mcp.rs`, registered as `oxide mcp`. It owns framing, MCP initialization,
tool discovery, boundary validation, and conversion of service failures to MCP
tool results. It does not own repository discovery, index validation, ranking,
structural expansion, context allocation, or embedding behavior.

Both tools call `RepositoryService` directly:

```text
context (namespace `oxide`) -> RepositoryService::discover -> RepositoryService::context
search  (namespace `oxide`) -> RepositoryService::discover -> RepositoryService::search
```

The human CLI remains unchanged apart from the `oxide mcp` server entry point.
No MCP dependency, watcher, daemon, auto-indexing, session state, or recovery
logic was added.

## 2. Exposed tools

Exactly two tools are returned by `tools/list`:

- `context(task: string, path?: string, token_budget?: integer)`
  - Builds a bounded task-oriented working set.
  - `token_budget` defaults to `4096`.
- `search(query: string, path?: string, limit?: integer)`
  - Finds repository code for a focused follow-up question.
  - `limit` defaults to `10` and is capped by the existing service at `100`.

The adapter always uses the service's existing hybrid search defaults. It does
not expose retrieval modes, fusion weights, expansion flags, model settings,
indexing, status, stats, review, eval, or diagnostics.

Successful tool content is the existing serialized `ContextResult` or
`Vec<Evidence>`. Service failures are MCP tool results with `isError: true`,
machine-readable `structuredContent.error`, and backward-compatible JSON text
carrying the unchanged `code`, mapped `action`, and human-readable `message`.
Invalid JSON-RPC or tool arguments use standard JSON-RPC error codes.

The service error envelope is intentionally duplicated only for failures:
clients that understand structured MCP content can branch without parsing
text; older clients still receive the same JSON text payload.

Measured from the actual compact JSON returned by the debug binary. Token
estimates use the repository's established `characters / 4` approximation.

| component | chars | estimated tokens |
|---|---:|---:|
| `context` serialized schema | 285 | 71 |
| `search` serialized schema | 302 | 76 |
| both tool schemas | 590 | 148 |
| server instructions | 278 | 70 |
| compact persistent total | 868 | **217** |

The hypothetical full surface estimate was derived from the existing seven CLI
operations and their current argument/DTO definitions (`index`, `status`,
`search`, `review`, `stats`, `context`, `eval`) without exposing those tools.
The equivalent compact JSON schema estimate is 1,501 chars / **375 tokens**,
plus the same instruction allowance: approximately **445 tokens** total. The
compact surface therefore saves approximately **228 persistent tokens** per
turn under this estimate. This is an estimate, not a tokenizer measurement.

## 4. Representative response measurement

Responses were captured through the real stdio protocol against a temporary
three-file Python repository and measured with `characters / 4`.

| case | items | full response tokens | payload tokens | source/snippet tokens | metadata tokens |
|---|---:|---:|---:|---:|---:|
| small discovery (`context`, budget 128) | 5 | 642 | 552 | 58 | 494 |
| multi-file task (`context`, budget 512) | 5 | 656 | 566 | 68 | 499 |
| targeted search (limit 3) | 3 | 324 | 270 | 45 | 225 |
| weak query (limit 3) | 3 | 302 | 250 | 43 | 207 |

The payload metadata is large because every evidence item carries its
navigation identity, location, kind/language, score, reasons, and bounded
snippet accounting. The existing field audit found no clearly redundant field:
these fields support navigation, trust weighting, or budget reasoning. The
JSON-RPC envelope adds about 90 tokens in these captures. No retrieval or
allocation field was removed to improve transport size.

A zero-result response was also verified with `limit: 0`; normal hybrid search
may return nearest semantic candidates for a weak query rather than an empty
list, which is existing service behavior and was not changed in Phase 2.

## 5. Client compatibility

| client | configuration | result |
|---|---|---|
| OpenCode | local MCP entry pointing to `oxide mcp` | connected; `tools/list` exposed `context` and `search`; real run called rendered `oxide_context` once and returned source evidence |
| Claude Code | temporary `--mcp-config` stdio entry | initialized and reported rendered `mcp__oxide__context` and `mcp__oxide__search`; execution stopped before a model call because the CLI was not logged in |
| Codex CLI | isolated `CODEX_HOME`, `codex mcp add oxide -- ... mcp` | accepted and listed the stdio server as enabled; status reports auth unsupported, so no model run was claimed |

No per-agent installer or temporary test configuration was persisted by these
experiments. The user's existing OpenCode configuration separately had the
unrelated `codebase-memory-mcp` entry removed.

## 6. Agent behavior evaluation

Objective OpenCode JSON event logs were captured on the same indexed fixture:

- **A — no OXIDE:** the agent used repository `grep` and `read` (two calls)
  and located `src/auth.py:4` correctly.
- **B — MCP tools available, no server instructions:** a temporary protocol
  proxy removed only the initialization instructions. The agent called
  `search`, then redundantly called `context`, then read the source; it still
  located `src/auth.py:4` correctly.
- **C — MCP plus compact server instructions:** the agent called `context`
  once, used the returned `src/auth.py` evidence, and located the same
  implementation correctly without editing.
- **C negative control — exact known-file task:** given `src/auth.py:5`, the
  agent made three ordinary read/directory calls and zero OXIDE calls. It
  explicitly cited the server instruction to skip OXIDE for that known target.
- **C focused follow-up:** after `context` for the unfamiliar task, the agent
  made one `search` call for `where is validate_refresh_token called?`; it
  used both results to identify the definition and caller. This independently
  supports retaining `search`.

All behavioral samples were successful but tiny and fixture-bound. They show
that the instructions improve retrieval-call discipline for this task shape
(one context call in C versus search plus context in B), that the negative
control avoided OXIDE, and that search remains useful after context. They do
not establish a correctness or latency advantage: A, B, and C all reached the
same source, and wall times varied with the agent runtime. No third tool is
justified by this evidence.

## 7. Protocol and lifecycle coverage

`tests/mcp_e2e.rs` exercises the real binary and stdio protocol for:

- initialization and compact instructions
- exact two-tool discovery
- valid context and search calls
- malformed parameters
- repository-not-found, missing-index, incompatible-index, and unavailable-embedder errors
- preservation of service `code` and `action`
- deterministic repeated reads
- empty result payloads
- concurrent read processes

The server is short-lived, reads one JSON request per line, writes one response
per line, ignores supported notifications, and never indexes or mutates a read
request's repository/index.

## 8. Risks and freeze decision

Remaining risks are transport/client ecosystem details rather than core
retrieval behavior:

- MCP clients may display the JSON error content differently; the machine
  fields remain present in the tool result.
- OpenCode prefixes the discovered local tool names with its server namespace
  (`oxide_context` and `oxide_search` in event logs).
- Claude and Codex require client authentication/configuration outside OXIDE;
  only their transport/config compatibility was proven here.
- Response metadata remains the dominant per-call cost, but the fields are
  currently useful and shared with the CLI contract.
- Freshness remains honest: missing, stale, incompatible, corrupt, or
  unavailable provider state is surfaced from `RepositoryService`; the MCP
  adapter does not recover automatically.

The v0.1 MCP surface is ready to freeze as `context` plus `search`.
Retrieval ranking, context allocation, embedding selection, Selective Code
Indexing, and the canonical benchmark remain unchanged.
