# AGENTS.md

OXIDE v2 (on `main`): a structural context engine for agents. One Python package, `oxide`, in `src/oxide/` (ADR-0011); the Rust/Bun implementation it replaced is recoverable at `f79808a`.
`CLAUDE.md` is a symlink to this file, so Claude and Codex share one root policy.

The architecture authority is `docs/spec/SPEC.md`, with standing rules and research verdicts in `docs/decisions.md`, the evaluation protocol in `fixtures/eval/README.md` and decisions in `docs/adr/` (Proposed ADRs are proposals, not Accepted decisions; never change an ADR's status unasked). When they disagree with anything else in the repo, they win.

This file is always loaded; keep it short. New commands go in `docs/agents/workflow.md`; the public API is documented in `docs/public-api.md`.

## Layout and ownership

- `src/oxide/api.py`, `models.py`: the public API and its typed results/errors, the stable product boundary.
- `src/oxide/core/`: repository intelligence and deterministic policy (Python slice, derivation, retrieval, TreeRouter, selection/packing). No filesystem or database access except through the read view it is given.
- `src/oxide/capture.py`, `src/oxide/storage/`: worktree capture; LadybugDB ownership, generations, publication and queries. Nothing else touches LadybugDB.
- `src/oxide/cli.py`, `src/oxide/mcp_server.py` (FastMCP) and future editor adapters: Python clients of `oxide.api` only. They must not parse source, route, select or pack.
- Vectorless: no embeddings, vector indexes or model runners. Judges, if ever added, are optional external services.
- Toolchain: Mise is the entrypoint (`mise run verify`, which CI runs); uv manages Python and dependencies (`pyproject.toml`, committed `uv.lock`); pytest, Ruff, mypy `--strict`. Rust, Bun/TypeScript, pnpm, Deno and `packages/*` are retired; don't reintroduce them without an ADR.

## How to work

- For fully-autonomous tasks, don't poll background tasks; they will send you their output once they're done.
- Implementation first: read the code, change it, add or adjust a test, `mise run verify`; before a push also `mise run pythons` (CI's 3.12/3.14 rows). Feature, bug-fix, language and refactor work needs no study, preregistration, plan doc or report.
- Run `mise run tier` before review/commit. Tier 0 needs `mise run verify`; tier 1 also needs an independent zero-context review using the printed rubric(s); tier 2 additionally needs a Goldfish-reviewed intent spec before implementing a feature or behavior change. The tier and review flow are in `docs/agents/workflow.md`. Agents may commit after required checks; human review remains at merge.
- Library first: an existing helper, the standard library or an installed dependency (tree-sitter queries, LadybugDB queries, NetworkX) before your own code. Native libraries do recursive and latency-sensitive work; Python holds bounded policy. A new dependency needs a reuse, packaging or API reason (SPEC § System architecture).
- No speculative abstractions, flags, options or features: build only what the current phase needs. Don't duplicate logic or policy, but don't add an abstraction just to remove a few repeated lines.
- Minimal documentation: update only the doc that owns a changed fact (SPEC, `docs/public-api.md`, `docs/agents/workflow.md`). No new design notes, plans or reports unless asked or a gate cites them. Comments say why, never what.
- Graph selection is algorithm-driven: a named, standard graph algorithm (NetworkX or a LadybugDB query) over stored relations with a stated objective, not hand-tuned weights or ad-hoc traversal.
- Benchmark-backed decisions: a quality or performance claim needs before/after numbers (ContextBench gates per `docs/agents/workflow.md` for retrieval/routing/packing/selection, `scripts/bench.py` for speed). Fix the pass/fail rule before reading held-out results; record the verdict as one row in `docs/decisions.md` § Research verdicts. No numbers, no claim, no merge.
- Keep changes small and cohesive. Don't clean up unrelated code during a focused task.
- Keep the public API and versioned formats (derivation components, store schema, payload/units versions) compatible unless the task says to change them; bump the version when you do. Legacy v1 compatibility (SQLite indexes, symbol ids, CLI/MCP/JSON surface) and the retired Rust/TS protocol are not requirements (SPEC § Compatibility policy).
- Fix root causes. Don't weaken tests, regenerate goldens or re-baseline just to make a failure go away.
- Experiments are only for claims a test can't settle: the quality and performance claims above, or promoting an optional judge.
- Read the code, tests and docs before stating how something works. Label each claim as measured or inferred.
- If missing or ambiguous information could change the implementation, check the repo first. If that doesn't settle it, ask one focused question instead of guessing. Don't invent permanent ID/schema/transport/model policy to unblock scaffolding (`docs/decisions.md` rule 4).
- Before finishing, read the full diff for accidental changes in scope, public API surface (`oxide/__init__.py`), dependencies (`pyproject.toml`, `uv.lock`) or generated files.
- Tests never touch real user data: every test and subprocess gets a private data location (`tests/python/conftest.py`).
- When an implementation is done and `mise run verify` passes, commit it on the current worktree branch without asking. Don't push, tag, release, edit GitHub issues or roadmaps, or start the next phase unless asked.
- Keep every OXIDE source file at or below 500 LOC. Tests, docs and CSS are exempt, and so are vendored or generated agent tooling (`.agents/`, `.claude/`, `.opencode/`), which stay as their tools ship them. Markdown is documentation, not source. Split a source file before completion if your change takes it over the limit.
- If unsure about a problem or a bug, search relevant sources of the problem before tackling it.

## Read before touching

| Area | Read first |
|---|---|
| Anything architectural | `docs/spec/SPEC.md`, `docs/decisions.md`, `docs/adr/` |
| Python engine, storage, removed/retained features | `docs/adr/0011-python-engine-vectorless.md`, `docs/public-api.md` |
| Build, test, lint, commit format | `docs/agents/workflow.md` |
| Issues, triage labels, domain docs | `docs/agents/` (`issue-tracker.md`, `triage-labels.md`, `domain.md`) |
| Historical evidence and legacy reproduction | `docs/decisions.md` (verdicts; reports and the migration audit are pinned at `013e791`). Port semantic lessons only where current SPEC requires them; legacy layout and APIs are not v2 instructions. |
| v2 review policy | `docs/review/` — reviewer-facing invariants for the Python engine (public API, storage/generations, core/retrieval, capture/language, evidence/benchmarks). Architecture authority remains `docs/spec/SPEC.md` and `docs/decisions.md`. |

## Always-on rules

- Legacy code and docs are historical evidence, not a template (SPEC, `docs/decisions.md` rule 2). The old implementation is recoverable at `ac985b28`; don't port it into `src/oxide/` without a stated SPEC requirement.
- Retrieval, routing and packing changes are evaluated per SPEC § Evaluation against the frozen baselines (`tests/python/test_baseline.py` checks Phase 3 exactly); never regenerate a frozen artifact to make a failure pass. Legacy numbers in pinned historical reports are historical comparators, not v2 gates.
- Models judge; deterministic code decides. Keep JEV and learned judges optional; there are no embeddings. The deterministic offline path needs no model or network (only `oxide setup` fetches the pinned FTS extension, explicitly).
- A restriction is a conclusion you earn from a real failure, not a field you read. Never report an operation as blocked, unavailable, or not-permitted based on a metadata flag, a config field, or a docs claim. Attempt it and quote the actual error. If you genuinely cannot attempt it, say "not attempted", never "we can't".

<!-- CODEGRAPH_START -->
## CodeGraph

In repositories indexed by CodeGraph (a `.codegraph/` directory exists at the repo root), reach for it BEFORE grep/find or reading files when you need to understand or locate code:

- **MCP tool** (when available): `codegraph_explore` answers most code questions in one call — the relevant symbols' verbatim source plus the call paths between them, including dynamic-dispatch hops grep can't follow. Name a file or symbol in the query to read its current line-numbered source. If it's listed but deferred, load it by name via tool search.
- **Shell** (always works): `codegraph explore "<symbol names or question>"` prints the same output.

If there is no `.codegraph/` directory, skip CodeGraph entirely — indexing is the user's decision.
<!-- CODEGRAPH_END -->

<!-- entire-agent:begin -->
Read .entire/agent-guide.md for this repository's workflow, source inspection, and verification guidance.
@.entire/agent-guide.md
<!-- entire-agent:end -->

%% lat:begin %%
# Before starting work

Use Lat's knowledge graph to ground each task before changing code.

- Run `lat search` to find sections relevant to your task. Read them to understand the design intent before writing code.

# Post-task checklist (REQUIRED — do not skip)

After EVERY task, before responding to the user:

- [ ] Update `lat.md/` if you added or changed meaningful implemented functionality, architecture, tests, or behavior. Keep it a focused snapshot of the current implemented state, not a journal/changelog. Plans may be drafted in `lat.md/` alongside implementation, with the intent that by commit time they describe what was implemented. Otherwise, keep proposals, hypothetical designs, and future work outside `lat.md/` unless the user explicitly requests them there.
- [ ] Run `lat check` — all validations must pass
- [ ] Do not skip these steps. Do not consider your task done until both are complete.

---

# What is lat.md?

Lat.md is the project's structured knowledge graph for architecture, design decisions, behavior, and tests.

The graph lives in `lat.md/` as cross-linked Markdown describing **what** the project does and **why**. Use it to ground work in actual architecture, and keep it a focused snapshot of the current implemented state rather than a journal or changelog.

Project-specific lat documentation belongs in `lat.md/`. Do not modify this generated instruction file or a generated `lat-md` `SKILL.md` to record project guidance: both are owned by lat tooling and may be replaced by a later `lat init`.

# Commands

Use these commands to navigate and validate the project knowledge graph.

```bash
lat locate "Section Name"      # find a section by name (exact, fuzzy)
lat refs "file#Section"        # find what references a section
lat search "natural language"  # semantic search across all sections
lat external show <handle>      # inspect a pinned external source before cloning it
lat check                      # run full graph and documentation validation
```

Run `lat --help` when in doubt about available commands or options.

If `lat search` fails because no API key is configured, explain to the user that semantic search requires a key provided via `LAT_LLM_KEY` (direct value), `LAT_LLM_KEY_FILE` (path to key file), or `LAT_LLM_KEY_HELPER` (command that prints the key). Supported key prefixes: `sk-...` (OpenAI) or `vck_...` (Vercel). If the user doesn't want to set it up, use `lat locate` for direct lookups instead.

# Syntax primer

Lat uses stable section ids, wiki links, source links, and code references to connect documentation with implementation.

- **Section ids**: `lat.md/path/to/file#Heading#SubHeading` — full form uses project-root-relative path (e.g. `lat.md/tests/search#RAG Replay Tests`). Short form uses bare file name when unique (e.g. `search#RAG Replay Tests`, `cli#search#Indexing`).
- **Wiki links**: `[[target]]` or `[[target|alias]]` — cross-references between sections. Can also reference repository paths or source code: `[[schema.sql]]`, `[[src/components]]`, `[[src/foo.ts#myFunction]]`.
- **Link specificity**: When documenting implemented behavior, prefer the narrowest relevant source-symbol link over a whole-file link. Use whole-file or directory links when discussing the module or resource as a whole, or when no supported symbol represents the target. If prose names a symbol, link that symbol directly.
- **Repository path links**: Wiki links without a `#` fragment may target any existing file or directory inside the project. Unsupported formats validate but cannot be opened by Lat; fragments require a `lat.md/` section or supported source file.
- **Source code links**: Wiki links in `lat.md/` files can reference functions, classes, constants, and methods in supported source files. Use the full path: `[[src/config.ts#getConfigDir]]`, `[[src/server.ts#App#listen]]` (class method), `[[lib/utils.py#parse_args]]`, `[[src/lib.rs#Greeter#greet]]` (Rust impl method), `[[src/app.go#Greeter#Greet]]` (Go method), `[[src/app.h#Greeter]]` (C struct). When prose names an implementation symbol or a behavior governed by one, link the symbol instead of using a bare code span or copying its literal value. Prefer `[[src/config.ts#DEFAULT_TIMEOUT]]` (or an aliased form) over a bare identifier or copied value. `lat check` validates these exist.
- **Code refs**: `// @lat: [[section-id]]` (JS/TS/Rust/Go/C/PHP) or `# @lat: [[section-id]]` (Python/PHP) — ties source code to concepts

# Diagrams

Default to prose. Reserve Mermaid diagrams for concepts involving more than seven interacting subjects (such as components, states, or stages) with complex sequencing, branching, or dependencies that prose alone makes hard to follow.

The subject count alone does not justify a diagram; omit diagrams for simple sequences or relationships that a short paragraph or list explains clearly. A diagram should give users and later agents an overview of the interactions.

Keep each diagram focused on one process, use readable labels, and explain the important decisions in surrounding prose with precise source-symbol links. Prefer a small overview or several focused diagrams over one dense diagram. Use fenced `mermaid` blocks and run `lat check` to validate syntax and readability.

# Test specs

Key tests can be described as sections in `lat.md/` files (e.g. `tests.md`). Add frontmatter to require that every leaf section is referenced by a `// @lat:` or `# @lat:` comment in test code:

```markdown
---
lat:
  require-code-mention: true
---
# Tests

Authentication and authorization test specifications.

## User login

Verify credential validation and error handling for the login endpoint.

### Rejects expired tokens
Tokens past their expiry timestamp are rejected with 401, even if otherwise valid.

### Handles missing password
Login request without a password field returns 400 with a descriptive error.
```

Every section MUST have a description — at least one sentence explaining what the test verifies and why. Empty sections with just a heading are not acceptable. (This is a specific case of the general leading paragraph rule below.)

Each test in code should reference its spec with exactly one comment placed next to the relevant test — not at the top of the file:

```python
# @lat: [[tests#User login#Rejects expired tokens]]
def test_rejects_expired_tokens():
    ...

# @lat: [[tests#User login#Handles missing password]]
def test_handles_missing_password():
    ...
```

Do not duplicate refs. One `@lat:` comment per spec section, placed at the test that covers it. `lat check` will flag any spec section not covered by a code reference, and any code reference pointing to a nonexistent section.

# Section structure

Concise opening paragraphs give every section a stable overview for validation and discovery.

Every section in `lat.md/` **must** have a leading paragraph — at least one sentence immediately after the heading, before any child headings or other block content. The first paragraph must be ≤250 characters (excluding `[[wiki link]]` content). This paragraph serves as the section's overview and is used in search results, command output, and RAG context — keeping it concise guarantees the section's essence is always captured.

```markdown
# Good Section

Brief overview of what this section documents and why it matters.

More detail can go in subsequent paragraphs, code blocks, or lists.

## Child heading

Details about this child topic.
```

```markdown
# Bad Section

## Child heading

Details about this child topic.
```

The second example is invalid because `Bad Section` has no leading paragraph. `lat check` validates this rule and reports errors for missing or overly long leading paragraphs.
%% lat:end %%
