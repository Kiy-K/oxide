# Phase 2.1 Context Economics

## Measurement status

| quantity | status | value |
|---|---|---:|
| persistent compact MCP footprint | measured/estimated | 868 chars, 217 chars/4 tokens |
| context schema | measured/estimated | 285 chars, 71t |
| search schema | measured/estimated | 302 chars, 76t |
| server instructions | measured/estimated | 278 chars, 70t |
| real tokenizer count | unavailable | no `tiktoken`/`transformers`; OpenCode provider tokenizer is not exposed |
| model/client-specific MCP accounting | unavailable | OpenCode event telemetry reports aggregate step tokens, not schema-vs-response attribution |
| OXIDE response sizes | measured | recorded in Phase 2 report; raw per-run logs include event payloads |
| native exploration tokens | unavailable | client telemetry does not attribute read/grep output separately |

## Method

OpenCode `--format json` events are preserved per run. Tool calls, timestamps,
step token telemetry, and raw MCP response text are retained. `context` and
`search` request/response payload lengths can be measured from the event logs;
agent prompt/tool-result tokenization is provider-owned and not exposed by the
client. Therefore this phase does not claim token savings beyond the stable
chars/4 persistent estimate.

## Economics hypothesis

Condition C pays ~217 persistent estimated tokens on every turn where the MCP
server is loaded. It can be favorable only when one `context` call replaces
multiple native discovery calls or a redundant search/context sequence. The
A/B/C run summaries report call counts and available aggregate token telemetry;
no unavailable native-token number is imputed.

Phase 2 representative MCP responses were 324–656 estimated tokens full
response, with 225–499 metadata tokens. Metadata is audited by observed agent
use in `results.md`; no DTO fields are removed in Phase 2.1.
