# Agent surface vs. human/admin surface

OXIDE has one shared core (`RepositoryService`) and two transports today: the
human/scriptable CLI (`oxide <cmd>`) and, informally, a coding agent driving
that same CLI via a shell tool. A future MCP server (Phase 2, not started in
this phase) is a third transport onto the same core. This document is the
classification that transport is required to follow — it is the answer to
"what should an agent-facing tool surface actually contain," derived by
auditing each existing CLI operation, not by assumption.

```text
                RepositoryService (src/service.rs)
                             │
              ┌──────────────┴───────────────┐
              │                               │
          Human CLI                     Agent surface
      (all 7 subcommands)          (context, search — see below)
              │                               │
   index / status / search /          same StatusResult /
   review / stats / context /         Evidence / ContextResult
   eval  (oxide binary, humans,       DTOs, reused verbatim by
   scripts, CI)                       a future thin MCP adapter
```

Both transports call the same `RepositoryService` methods and share the same
DTOs (`Evidence`, `ContextResult`, `StatusResult`, `IndexResult`) — the
split is about which operations are *offered* to which caller, not about
duplicating logic. A human CLI legitimately exposes more than an agent tool
surface; forcing them to have identical command counts would be minimalism
for its own sake, not for the agent's benefit.

## Classification

For each operation: does a coding agent need to invoke it during normal
implementation work, can a higher-level operation subsume it, does exposing
it create tool-selection ambiguity, and can it be invoked wastefully if an
agent calls tools reflexively?

| operation | class | why |
|-----------|-------|-----|
| `context` | **AGENT CORE** | The default entry point for unfamiliar-task discovery — one call returns a budgeted, ranked, deduplicated working set. This is the operation the "Context Engine" framing is built around. |
| `search` | **AGENT CORE** | Narrower follow-up once a context pack exists and one specific question remains (e.g. "where else is this called"). Distinct from `context`: different budget shape, no allocation/omission bookkeeping. Whether it's worth a *second* tool (vs. folding into `context`) is exactly what section I's experiment tests — see `docs/context-engineering-notes.md` / eval results for the empirical call. |
| `index` | **AGENT CORE (rare, reactive)** | Unavoidable bootstrap: `context`/`search` refuse to run on a missing/stale index by design (read commands never index, repair, or mutate — see `docs/agent-usage-policy.md`). An agent only needs to call `index` reactively, when a prior `context`/`search` call fails with `index_missing`/`index_stale` and `action: "index"` — never proactively, never per-query. This keeps it in the agent set without making it a repeat-call tool. |
| `status` | **HUMAN/ADMIN** | Every scenario where an agent might be tempted to call `status` first ("is the index current?") is already answered more cheaply by just calling `context`/`search` and reading the structured error's `code`/`action` if it fails. `status` is fully subsumed for agent purposes; it remains valuable for a human checking freshness, debugging, or scripting. |
| `stats` | **HUMAN/ADMIN** | Pure count introspection (files/symbols/embeddings) with no coding decision attached — a subset of `status`'s fields with a different (non-JSON, human) renderer. No agent workflow needs raw counts. |
| `review` | **HUMAN/ADMIN** | Diff → changed-symbols → related-context is a post-hoc review/PR workflow (human reviewer, or a future CI/review-bot integration), not a forward "find code to implement a task" workflow. Out of scope for the general coding-agent working-set use case this phase is minimizing for. |
| `eval` | **DIAGNOSTIC/INTERNAL** | Runs the committed regression benchmark (`fixtures/benchmark.json`) against the current binary. Exclusively a maintainer/CI operation — a coding agent has no scenario where running OXIDE's own retrieval regression test helps it complete a task. |

No command was removed from the CLI as a result of this audit — `status`,
`stats`, `review`, and `eval` remain fully available for humans, scripts,
and CI. Only the *agent-facing* surface (Skill instructions today; an MCP
tool list in Phase 2) is scoped down to `context` (+ `search`, pending the
Phase 1.2 evaluation).

## What this means for a future MCP adapter

A Phase 2 MCP server should expose exactly the AGENT CORE row(s) above as
tools — `context`, and `search` if the evaluation in section I supports it
— calling `RepositoryService` directly (per the "Future MCP reuse audit" in
`README.md`). `index` is reachable only as the reactive action named in a
structured error, not as a proactively-offered tool, to avoid an agent
treating "reindex the whole repo" as a routine step. `status`/`stats`/
`review`/`eval` stay CLI-only unless a concrete future agent persona (e.g. a
review-bot) demonstrates a need — not added speculatively.
