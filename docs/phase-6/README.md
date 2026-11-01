# Research decisions and benchmark evidence

Start with [current documentation](../README.md), not historical reports. These studies record decisions at their pinned revisions; they do not change SPEC, public API contracts or ADR status. Current gate commands and baseline selection live in [workflow](../agents/workflow.md).

## Retention

- Kept: what a gate, test, script or source comment reads (`baseline-v1`, `-v2`, `-v4`, `-v18`; `typescript-v1`, `-v2`, `-v15`; language milestones; `fts-footprint`, `indexing-throughput`, `output-integrity`, `release-blockers`), and studies behind a shipped behavior or an ADR. Never regenerate them to make a failure pass.
- Superseded `baseline-v*` / `typescript-v*` keep only their README (the protocol amendment chain); their results are reproducible from the pinned commit in that README.
- Closed studies with no current consumer were removed; their verdicts are in the table below. Everything, including removed directories and raw results, is at [this immutable evidence tree](https://github.com/Kiy-K/oxide/tree/eb43ac1070d8724d31a0be49bc7357c7132fb90f/docs/phase-6) under the same relative path.
- Historical harnesses run at their pinned revision, not against the current checkout.

## Decision map

| Question | Reports |
|---|---|
| Graph ranking, dispatch and candidate-pool ceilings | [v1](graph-v1/README.md), [v2](graph-v2/README.md), [v3](graph-v3/README.md) |
| Graph navigation completeness and targeted resolution | [initial audit](graph-navigation/README.md), [correction and follow-up](graph-navigation-v2/README.md), [NetworkX projection](networkx-v1/README.md) |
| Agent-guided context and where to stop | [AGC](agc/README.md), v2 REJECT (adoption; removed), [v3](agc-v3/README.md), [v4](agc-v4/README.md) |
| Optional judges and deterministic decision boundary | [seam](decision-seam/README.md), [study v1](decision-study-v1/README.md), [v2](decision-study-v2/README.md), [v3](decision-study-v3/README.md), [reliability](jev-reliability/README.md) |
| Historical JEV ordering milestones | [M1](jev-order-m1/README.md), [M2](jev-order-m2/README.md), [M3 release readiness](jev-order-m3/README.md), [release blockers](release-blockers/README.md) |
| Storage, graph caches and FTS footprint | LEANN/storage: REJECT compaction, DEFER traversal memo (removed); [FTS corrections and optimizations](fts-footprint/README.md) |
| Output serialization | [TOON rejection](toon-serialization/README.md) |
| Retrieval diagnostics | [path hints](path-hint-utility/README.md) (PROMOTE). Removed, all closed: source bodies REJECT, entry-retrieval taxonomy (diagnostic), file localization NOT VIABLE, router reachability NEGATIVE, routed selection INCONCLUSIVE |
| Structure and output experiments | [ast-grep feasibility](astgrep-feasibility/README.md), outlines REJECT (removed), [output integrity](output-integrity/README.md), [structural MCP trial](structural-mcp-trial/README.md) |
| Capture/indexing performance | [throughput](indexing-throughput/README.md), [incremental indexing](incremental-indexing/README.md), change classification NOT WORTH IT (removed) |

## Language milestone evidence

[JavaScript](javascript-v1/README.md), [Rust](rust-v1/README.md), [Go](go-v1/README.md), [Java](java-v1/README.md), [C](c-v1/README.md), [C++](cpp-v1/README.md). TypeScript evidence is the versioned protocol series above. Current support boundaries are documented under [parser limits](../parser-limits.md), not inferred from an old milestone's claims.
