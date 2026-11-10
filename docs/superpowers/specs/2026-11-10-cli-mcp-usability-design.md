# CLI and MCP usability: fail clearly, expose impact

Status: approved by the user (2026-11-10: "A now then B"). Tier 2 (public API and CLI/MCP surface).
Revised 2026-11-10 at the user's request: impact is `oxide_search` `mode="impact"`, not a second tool (B2).

## Intent

Coding agents and developers using `oxide` on a repository hit three problems, measured on
`fixtures/py_repo` (2026-11-10 probes):

1. An empty query exits 0 with zero entries and no message.
2. Unmatched `--symbol`/`--file` hints are dropped from the CLI, and silent in the MCP context text.
3. The graph queries the API already offers (`Repository.impact`: who calls or imports a
   symbol, nearest first) reach neither the CLI nor MCP, so an agent cannot ask "what depends on X".

Also, `not_indexed` advice differs between CLI (no next step) and MCP (names `oxide index`).

## Goals

- A. Failures that an agent or human can act on, with one advice table shared by CLI and MCP.
- A. Hint issues visible in every client that accepts hints.
- B. `oxide impact` (CLI) and `oxide_search` `mode="impact"` (MCP) over the existing `Repository.impact`.

## Non-goals

- No change to retrieval, routing, packing, ranking, or the bytes of any valid `search`/`context`
  call. Frozen baselines and ContextBench stay untouched.
- The `oxide_search` description changes only by the new `mode="impact"` (B2); the search and
  context wording is unchanged. A change to the description is a protocol amendment
  (`fixtures/eval/agent-swe/README.md`).
- No degradation rewriting (core strings are not parsed by presentation code), no line/byte
  unit unification, no `entity`/`relations` commands. Each is a separate decision.
- No new dependency.

## Anti-goals

- Presentation code (`cli.py`, `mcp_server.py`) must not parse source, route, select or pack
  (AGENTS.md). Hint and impact logic stays in `api.py`/`core`.
- An empty query must not break selection-only searches: `search("", selection=…)` is used in
  `tests/python/test_retrieval.py` and is valid.
- No success claim from these changes. No held-out run.

## Constraints

- Public API and CLI/MCP changes are tier 2: this spec, a zero-context review, then implementation.
- Protocol: `scripts/agent_eval/session.py` reads `tools=N` from the connection log, so a second
  tool changes what the eval measures. The arm definition needs a dated protocol amendment before
  any further eval result is read. This change does not edit that README.
- Keep every source file at or below 500 LOC.

## Approach (chosen)

Approach A, then B, as the user chose from three options (A only; A + B; rebuild MCP around agent
workflows). The rebuild was rejected: no measured need, and it breaks the single-tool decision.

### A1. Empty query

`_hints` (used by `search` and `build_context`) raises `InvalidArgument` when the query is blank
and no seed hint (`paths`, `symbols`, `selection`, `changed`) is given. Message:
`query is empty: give words or identifiers, or a path or symbol hint`.
Effect: CLI exit 2, MCP `invalid_argument`. With any seed hint the empty query stays valid.

### A2. Hint issues in clients

- CLI `search`: after the entries and before the lexical status line, print one
  `hint: <kind> <hint>: <issue>` line per `HintIssue`.
- CLI `context`: the same lines to stderr, next to the existing `degraded` output.
- MCP `oxide_search` mode=context: `# hint <kind> <hint>: <issue>` header lines, after `# degraded`.
  Mode=search already returns hints in its structured content and text; unchanged.

### A3. One advice table

Move `_ADVICE` from `mcp_server.py` to `models.py` as `ADVICE`, with a `{repository}`
placeholder, and a function `advice(error, repository) -> str | None`. MCP uses it as today
(now with the real repository path). CLI prints `hint: <advice>` after `error[code]: …` when one
exists. `invalid_argument` keeps its own message in both clients.

`api.py` message for an unknown impact key becomes
`no entity 'KEY' in this snapshot; find keys with oxide search`.

### B1. CLI `oxide impact`

`oxide impact PATH KEY [--direction upstream|downstream] [--depth 0..8, default 3]
[--limit 0..1000, default 200] [--data-dir D] [--json]`. Opens with `create=False`.
Human output: a header with the root key, direction, reached count, `stopped_by`, and unresolved
count; then one line per hop: `depth proven key via <via>` (proven `-` when None; `(cyclic)` when
set).

### B2. MCP `oxide_search` mode `impact`

Revised 2026-11-10: the separate `oxide_impact` tool was removed before any commit, so the server
keeps one tool. `mode="impact"` lists what depends on an entity. Arguments: `key` (required,
≤ 1024 chars, a key from a `mode="search"` entry), `repository`, `direction` (`upstream` default),
`depth` (0–8, default 3), `limit` (0–100, default 25; shared with search). Validation per mode:
`query`, `budget`, `paths` and `symbols` are `invalid_argument` in impact mode; `key`, `direction`
and `depth` are `invalid_argument` outside it; `query` is required for search and context.
Output: the `Impact` structured content, prefixed by `hops_returned`. `stopped_by` (not
`more_available`) says which bound cut the walk: a depth stop means the traversal is incomplete
within the bound, not that more results exist. Compact JSON text leads with the same fields, as in
search. The output schema is the union of the three result types. The description says when to use
it (after a search gives a key) and what `unresolved` means.

Consequence: the server advertises one tool, as before. `tests/python/test_mcp.py` checks the
one-tool list, and `scripts/smoke_dist.py` checks it over stdio.

## Verification plan

- New tests: empty-query rules in `test_retrieval.py`; CLI hint lines, empty-query exit,
  `impact` round trip (text and `--json`), unknown key (`test_cli.py`); MCP `mode="impact"` returns
  a known caller (`symbol:tests/test_auth.py#test_decode_claims_parses_payload` upstream of
  `symbol:oxidepy/auth.py#decode_claims`, depth 1), rejects out-of-range bounds and an unknown key,
  and each mode rejects the other modes' arguments (`test_mcp.py`).
- `mise run verify`; before/after the CLI and MCP probes from the research step.

## Left to the builder

Exact help strings and the human layout of `impact` output, as long as the fields above appear.
