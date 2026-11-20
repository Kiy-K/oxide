# Architecture

OXIDE v2 is a modular monolith: one Python package, `oxide`, that turns repository structure into task-specific context. No services or IPC; authority is [[docs/spec/SPEC.md]].

## Layers

Dependencies point downward only: integrations, public API, engine, core policy, storage, LadybugDB.

- **Integrations**: [[src/oxide/cli.py]] and [[src/oxide/mcp_server.py]] are thin clients of the public API. They never parse source, route, select or pack.
- **Public API**: [[src/oxide/api.py]] (`Repository`, `open_repository`) and [[src/oxide/models.py]] (frozen typed results and errors) are the stable product boundary; see [[docs/public-api.md]].
- **Engine I/O**: [[src/oxide/capture.py]] reads the worktree; [[src/oxide/tracking.py]] keeps indexes fresh.
- **Core**: `src/oxide/core/` holds pure derivation, retrieval, routing, selection and packing, touching data only through the read view it is given.
- **Storage**: `src/oxide/storage/` is the only code that touches LadybugDB. See [[storage]].

## Context pipeline

A query flows from lexical and structural entry points through TreeRouter to a budgeted, source-backed bundle.

1. Snapshot is derived into entities and relations ([[src/oxide/core/indexing.py]]).
2. [[src/oxide/core/retrieval.py]] merges lexical hits and structural seeds by entity; these are entry points, never final context.
3. [[src/oxide/core/routing.py]] (TreeRouter) walks a level-synchronous BFS over TreeIndex projection 1 (physical containment), with all other relations as typed cross-edges.
4. [[src/oxide/core/packing.py]] values candidates through a `DecisionProvider`, selects greedily within the token budget, and emits an `oxide-payload-v4` bundle.

## Freshness and ownership

An open `Repository` owns its store and keeps it fresh without user action; a second process may read instead.

[[src/oxide/tracking.py]] coalesces filesystem events into incremental capture and atomic publication, with a periodic reconcile as a safety net (300 s healthy, 30 s when the watcher failed). No event history or telemetry is kept. Non-owners open with `shared=True` and read through pinned generations ([[storage#Generations and ownership]]).

## Navigation and impact

Besides context, the API serves deterministic code navigation over one sealed generation.

[[src/oxide/core/lookup.py]] answers outline, inspect and refs from indexed facts, with line numbers computed from the captured bytes. [[src/oxide/core/navigation.py]] walks upstream dependents; [[src/oxide/core/blast.py]] precomputes each entity's blast radius at publication (ADR-0014). [[src/oxide/core/structural.py]] runs an explicit ast-grep rule search and is never part of retrieval, routing or packing.

## Optional judge

A `DecisionProvider` seam lets an external model order admitted candidates; the default path needs no model or network.

[[src/oxide/core/capsule.py]] builds the bounded `CandidateCapsule` evidence a judge sees. [[src/oxide/core/ordering.py]] and [[src/oxide/jev.py]] implement JEV ordering (ADR-0009): internal, off by default, not exported from `oxide`.

## Agent tooling

Repo-level agent configuration is not part of the engine; it wires coding agents to this repository's knowledge.

`.mcp.json`, `.codex/` and `.claude/` register the `oxide` and `lat` MCP servers and hooks; lat skills and plugins live under `.agents/`, `.claude/`, `.codex/`, `.opencode/` and `.pi/`. `AGENTS.md` is the shared root policy (`CLAUDE.md` symlinks to it).
