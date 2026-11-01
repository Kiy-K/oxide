# Decisions

The standing rules, delivered state, settled research verdicts and evaluation protocol of
OXIDE v2, in one place. It replaces `docs/BOOTSTRAP.md` and the Phase 6 decision map.
[SPEC](spec/SPEC.md) is the architecture authority and the [ADRs](adr/) record individual
decisions. This file never changes an ADR's status, and a Proposed ADR is not a requirement.

The reports, harnesses and raw results behind every verdict below are frozen at
[`013e791`](https://github.com/Kiy-K/oxide/tree/013e7910d6fe8b9df9ba753bd68e2b6468bb4e51/docs)
(`docs/BOOTSTRAP.md`, `docs/phase-6/<study>/`, `docs/migration-audit/`). Historical
harnesses run at that pin, not against the current checkout. The v1 implementation is
pinned at `ac985b28`, and the replaced Rust/Bun v2 runtime at `f79808a`.

## Purpose

OXIDE transforms repository structure into task-specific context. It is agent-agnostic and
is not a coding agent. The flow is: repository → TreeIndex → query → TreeRouter (lexical
entry points) → candidates → value → deterministic policy → expansion → ContextPacker →
ContextBundle.

## Standing rules

1. Work on `rewrite/v2` or a branch from it. Check branch and working-tree state first, and protect existing work.
2. Legacy `main` is evidence, not a template. Reusing it requires a stated SPEC requirement, a boundary-fit reason and tests.
3. Don't keep legacy SQLite schemas, row IDs, migrations or cache locations. Derived state must be rebuildable from captured source.
4. When SPEC and code disagree, SPEC wins. If SPEC is ambiguous, record the ambiguity, the alternatives and a proposal, then continue with independent work. Don't invent permanent ID, schema, transport or model policy to unblock scaffolding.
5. JEV and every learned judge stay optional, and there are no embeddings or vectors. Lexical entry points, TreeRouter, the heuristic value and the packer need no model or network.
6. Models judge; deterministic code decides thresholds, traversal, stopping, inclusion, budget, fallback and packing. Never overwrite retrieval scores with logits. JEV judges value, not code correctness.
7. Repository intelligence lives in `oxide.core`, and I/O in `oxide.capture` and `oxide.storage`. Integrations (the CLI, `oxide mcp`, editors) are clients of `oxide.api` and never parse, route or select.
8. LadybugDB objects, native IDs, rows and Cypher stay inside `oxide.storage`. The API exposes only domain values.
9. Keep one package. A new package or dependency needs a reuse, packaging or API reason.
10. Test at boundaries: declared invariants, degraded states, publication and limits.
11. Keep frozen baselines with their provenance. Never regenerate gold or erase a negative result to make a challenger pass.
12. A quality claim states what information it adds, which decision it improves and which metric should move. Promoting it to a default needs evidence.
13. Documentation claims aren't proof. Verify behavior at the pinned version (ADR-0011 § Storage verification).
14. Never execute repository code or build hooks while indexing. Respect repository scope and source privacy.
15. Structure comes before similarity: retrieval finds entry points and routing finds context. The TreeIndex keeps cross-edges.
16. A `DecisionProvider` seam sits behind `oxide.core.packing.value`, with subject correlation, abstention and a deterministic fallback. CI never runs a live judge.
17. Native libraries (tree-sitter, LadybugDB, NetworkX, hashing) do recursive and latency-sensitive work. Python holds bounded policy.

## Delivered

- **Python engine (ADR-0011, 2026-10-15).** The `oxide` package runs index → search → context, with the CLI and verify (Ruff, mypy `--strict`, pytest). It reproduces `docs/phase-3/baseline-v1` exactly. The Rust/Bun runtime was removed at `f79808a`, and every port or non-port is recorded in ADR-0011 § What was removed.
- **Surfaces.** `oxide mcp` (FastMCP) exposes a single `oxide_search` tool with mode `search|context`. Structural search is a Python API only.
- **Indexing.** Indexing is incremental: sealed facts match a full rebuild, an edit reindex on 3,539 files dropped from 14.5 s to 9.8 s, and a cold build costs +9%. Large repositories use a load pool sized BUFFER_POOL + 2× CSV bytes.
- **Graph.** Python resolution `oxide-python-v4`+ powers `Repository.impact`, and heuristic method edges are resolved through the C3 MRO.
- **Languages.** Python, TypeScript (tsconfig `paths`/`baseUrl`), JavaScript, Rust, Go, Java, C and C++, each with a conformance suite. The support boundaries are in [parser limits](parser-limits.md).
- **Platforms.** All four hosted platform jobs pass. `ladybug` is pinned to 0.21.0 to match the FTS build, and the FTS build comes from OXIDE's own release mirror ([platforms](platforms.md)).

## Research verdicts

Each row is closed. Its report is under `docs/phase-6/<study>/` at the pin above.

| Area | Verdict |
|---|---|
| Graph ranking (`graph-v1`..`v3`) | Resolution raised routed coverage from 0.673 to 0.730. REJECT PPR + communities, dispatch routing and the order tiebreak. The loss after routing comes from budget allocation and the relevance signal. |
| Graph navigation (`graph-navigation`, `-v2`, `networkx-v1`) | Basis for ADR-0012 and ADR-0013 (both Proposed). |
| Agent-guided context (`agc`, `agc-v2`..`v4`) | v2 REJECT (adoption). The rest is input to ADR-0013. |
| Optional judges (`decision-seam`, `decision-study-v1`..`v3`, `jev-reliability`, `jev-order-m1`..`m3`) | Study v1 REJECT, v2 INCONCLUSIVE, v3 PASS (+0.159 at 4,096 units). Reliability PASS only under immediate fallback. M3 and ADR-0009 are NOT READY: JEV stays off unless its validity gates are reopened. |
| Storage (`leann-graph-storage`, `fts-footprint`) | REJECT compaction; DEFER the traversal memo. ACCEPT `auto_checkpoint=False`, exact wide top-k, and blob fsync overlap with a 65,536-entry LRU. REJECT two query threads. [`fts-footprint`](phase-6/fts-footprint/README.md) stays local because source comments cite it. |
| Output (`toon-serialization`, `outline-comparison`, `output-integrity`) | REJECT TOON and outlines. Output-integrity fixes are shipped. |
| Retrieval (`path-hint-utility` and diagnostics) | PROMOTE path hints (`oxide-select-greedy-v4`): dev hinted `line_recall@4096` rose from 0.685 to 0.785 for Python and from 0.360 to 0.626 for TypeScript. REJECT source bodies. File localization NOT VIABLE, router reachability NEGATIVE, routed selection INCONCLUSIVE; entry retrieval is diagnostic only. |
| Structure (`astgrep-feasibility`, `structural-mcp-trial`) | ast-grep is feasible (94 MB install, pinned exactly). REJECT a structural MCP tool: grep scored F1 0.994 against 0.978. |
| Indexing (`indexing-throughput`, `incremental-indexing`, `change-classification`) | Throughput and incremental fixes are shipped. Change classification is NOT WORTH IT. |
| Release blockers (`release-blockers`) | Upstream replaced the FTS 0.21.0 files, so OXIDE mirrors the original builds (2026-10-24). The release body and notices are in [`fts-mirror/`](fts-mirror/RELEASE.md). |
| Languages (`javascript-v1`, `rust-v1`, `go-v1`, `java-v1`, `c-v1`, `cpp-v1`, `typescript-v1`..`v14`) | Milestones met. Current behavior is in each language doc and in parser limits. |

## Evaluation protocol

Experiments are only for claims a test can't settle (AGENTS.md § How to work). Ordinary
implementation is accepted on tests plus `mise run verify`. Changes to retrieval, routing or
packing must also pass the ContextBench gates.

**Frozen assets (read by the gates; never regenerate them to make a failure pass):**

| Path | Read by |
|---|---|
| `docs/phase-3/baseline-v1/` | `tests/python/test_baseline.py` (exact Rust Phase 3 reproduction) |
| `docs/phase-6/baseline-v1/tasks.json`, `baseline-v2/gold-audit.json` | `tests/python/contextbench.py` (`TASKS`, `AUDIT`) |
| `docs/phase-6/baseline-v18/` | the Python gate (`contextbench.BASELINE`), in `verify` |
| `docs/phase-6/typescript-v1/{tasks,gold-audit}.json`, `typescript-v15/` | the TypeScript gate, `mise run contextbench-ts` |

**ContextBench protocol (Python `contextbench-py`, TypeScript `contextbench-ts`):**

- **Tasks.**
  - Fixture queries (`fixtures/benchmark.json`, `structural_benchmark.json`).
  - `EuniAI/ContextBench` `data/full.parquet` tasks (sha256 `2f56535b…`), filtered by a file-count cost rule. Python has 37 tasks across requests, flask, seaborn, xarray and pytest. TypeScript has 81 across mui, vue, vscode, code-server and darkreader.
  - Gold is the human-annotated line spans. The audit marks each span verified or unverified, never dropped, and only verified gold is gated.
- **Splits are by repository.** Held-out is the two eligible repositories with the most tasks: xarray and pytest for Python, mui and vue for TypeScript. Develop on dev, and read held-out results only to evaluate a finished change.
- **Metrics.**
  - Entry: `entry_recall@1/5/20`, `entry_mrr`, `entry_file_recall`.
  - Routing: `routed_coverage`.
  - Packing, at 256/1,024/4,096 units: `span_recall`, `line_recall`, `file_recall`, `utilization`, `gold_unit_share`.
  - Each loss is tagged with the stage that caused it.
  - Values are macro means per split.
- **Gate rules.**
  - Per split, every gated metric stays at or above its frozen value minus 0.02.
  - Bundles stay within budget, are deterministic and never fail.
  - Corpus identities match the manifest.
  - Output compatibility (entry order, routed set, payload digests) is checked separately. A version bump is reported as a versioned difference, not as a quality regression.
- **Freezing.** A failing gate is investigated, not re-frozen. An intended, reviewed change is frozen as a new `baseline-vN` (or `typescript-vN`) directory with a README stating the reason and the diff against the previous version. Run `uv run --locked python tests/python/contextbench.py run --out docs/phase-6/baseline-vN` under the resource limits, then switch `BASELINE`. Remove the superseded directory in the same change; it stays recoverable from git history.
- **Recording.** Record a verdict as a row in the table above, plus a short report only if a gate, test or comment will cite it.

Commands, corpora and resource limits are in [workflow](agents/workflow.md).

## Remaining work

- **Distribution (final milestone).** PyPI (the name is open), standalone executables, and the FTS extension fetched by `oxide prepare` (never bundled). The name/version policy and the wheel matrix are open; FTS is pinned for four platforms.
- **Optional judges.** The `DecisionProvider` seam is in place (Replay fake, `oxide-capsule-v1`). JEV returns only if its validity gates are reopened (ADR-0009).
- **Proposed ADR candidates.** Candidates 0004–0008, named in SPEC, were never written.

## Handoff

The PR description is the handoff: what changed, how it was verified, any newly frozen
baseline, and open questions. Authorization rules are in `AGENTS.md`.
