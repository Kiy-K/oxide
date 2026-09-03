# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Full build/test/lint commands and load-bearing invariants (symbol id composition, cast conventions, retrieval expansion ordering, embedding provider identity, JSON contract shape): @AGENTS.md

## Repo etiquette

- Commit messages: lowercase conventional-ish prefix + concise imperative summary (`fix:`, `feat:`, `refactor:`, `docs:`, `harden:`, `bench:`, `tierb:`). Work happens directly on `main`, no PR workflow.
- Order matters only at commit time: `fmt` → `clippy` → `test` → benchmark gate (`tests/benchmark_gate.rs`), per `AGENTS.md`.

## Agent-facing docs — don't duplicate, restate

- `docs/agent-usage-policy.md` is the single canonical, transport-independent source of truth for how a coding agent should use OXIDE. The CLI Skill, a future MCP server's instructions, and any AGENTS.md snippet for OXIDE consumers must restate this document, not fork it.
- `skills/oxide-code-context/SKILL.md` is OXIDE's own bundled Agent Skill for *downstream consumers* of the `oxide` binary — it is a different thing from any `.claude/skills/` used while *developing* OXIDE itself. Don't conflate the two when editing either.

## Before touching retrieval scoring

`src/retrieval.rs` (BM25 + cosine + RRF fusion + structural expansion) is benchmark-gated. Before changing ranking/scoring logic, capture or compare against `docs/canonical-baseline.md` (the committed `oxide eval --config fixtures/benchmark.json` run) and treat any diff in the results as a regression to explain, not an expected side effect — including changes that only affect tie-breaking or ordering.
