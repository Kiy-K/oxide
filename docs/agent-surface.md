# Agent surface vs. human/admin surface

OXIDE has one shared core (`RepositoryService`) and two transports: the human
scriptable CLI (`oxide <cmd>`) and the coding-agent MCP server (`oxide mcp`).
This document classifies the MCP surface by auditing each existing CLI
operation, not by assuming command parity.

```text
                RepositoryService (src/service.rs)
                             │
              ┌──────────────┴───────────────┐
              │                               │
          Human CLI                     Agent MCP
      (all 7 subcommands)          (context, search; namespace oxide)
              │                               │
   index / status / search /          same Evidence / ContextResult
   review / stats / context /         DTOs, reused by the
   eval  (humans, scripts, CI)        thin adapter
```

Both transports call the same `RepositoryService` methods and share the same
DTOs (`Evidence`, `ContextResult`, `StatusResult`, `IndexResult`) where those
DTOs apply. The split is about which operations are offered to which caller,
not duplicated retrieval logic.

## Classification

| operation | class | why |
|-----------|-------|-----|
| `context` | **AGENT CORE** | Default entry point for unfamiliar-task discovery: one call returns a budgeted, ranked, deduplicated working set. |
| `search` | **AGENT CORE** | Narrower follow-up after a context pack when one specific question remains. |
| `index` | **HUMAN/ADMIN** | Read tools surface `index_missing` or `index_stale` with `action: "index"`; the agent or human decides when to run the CLI index command. |
| `status` | **HUMAN/ADMIN** | Read-tool errors already expose the actionable state needed by an agent; humans use status for inspection and scripting. |
| `stats` | **HUMAN/ADMIN** | Count introspection has no normal implementation decision attached. |
| `review` | **HUMAN/ADMIN** | Diff-to-context is a post-hoc review/CI workflow, not forward task discovery. |
| `eval` | **DIAGNOSTIC/INTERNAL** | The committed benchmark is for maintainers and CI. |

The CLI retains every human/admin command. Only the agent-facing MCP tool list
is scoped to `context` and `search`, based on the Phase 1.2 evaluation,
the Phase 2 transport report, and the Phase 2.1 real-agent evaluation in
`docs/evals/phase-2.1/`.

## MCP adapter contract

The MCP server calls `RepositoryService` directly. It does not reuse Clap
parsing or human renderers, and it does not expose indexing, administration,
retrieval-internal controls, or diagnostics. Read calls never index, repair, or
mutate the repository; service errors retain their `code`, `action`, and
message fields through the MCP result.
