# Research decisions and benchmark evidence

Start with [current documentation](../README.md), not historical reports. These studies record decisions at their pinned revisions; they do not change SPEC, public API contracts or ADR status. Current gate commands and baseline selection live in [workflow](../agents/workflow.md).

## Retention and reproduction

- Frozen `baseline-v*` and `typescript-v*` directories remain intact, including apparently duplicate results. Their READMEs are versioned protocols/amendments, not disposable narrative. Begin with [Python v1](baseline-v1/README.md), [TypeScript v1](typescript-v1/README.md) and [TypeScript corpus amendment](typescript-v2/README.md); follow the workflow for the current comparison target.
- Research READMEs summarize decisions, decisive measurements, corrections, negative results and limits. Keep their preregistrations, task/corpus manifests, scripts, inputs and raw results together: studies import other studies' scripts. Do not flatten directories or regenerate frozen controls to fix a failure.
- Follow each report's pinned source revision, corpus/split, dependency versions and reproduction commands. Historical harnesses may need retired code or paid/credentialed providers; their results are not measurements of the current checkout.
- Some provider traces, corpora and raw timing logs were external even before consolidation. Committed summaries/results alone do not make those experiments independently reproducible. Reports disclose the missing inputs; do not promote such evidence beyond its limits.
- Full pre-consolidation reports, including detailed commands and interpretation, remain at [this immutable evidence tree](https://github.com/Kiy-K/oxide/tree/eb43ac1070d8724d31a0be49bc7357c7132fb90f/docs/phase-6). For any shortened report, use the same relative path there. Evidence standards: [review policy](../review/evidence-and-benchmarks.md).

## Decision map

| Question | Reports |
|---|---|
| Graph ranking, dispatch and candidate-pool ceilings | [v1](graph-v1/README.md), [v2](graph-v2/README.md), [v3](graph-v3/README.md) |
| Graph navigation completeness and targeted resolution | [initial audit](graph-navigation/README.md), [correction and follow-up](graph-navigation-v2/README.md), [NetworkX projection](networkx-v1/README.md) |
| Agent-guided context and where to stop | [AGC](agc/README.md), [v2](agc-v2/README.md), [v3](agc-v3/README.md), [v4](agc-v4/README.md) |
| Optional judges and deterministic decision boundary | [seam](decision-seam/README.md), [study v1](decision-study-v1/README.md), [v2](decision-study-v2/README.md), [v3](decision-study-v3/README.md), [reliability](jev-reliability/README.md) |
| Historical JEV ordering milestones | [M1](jev-order-m1/README.md), [M2](jev-order-m2/README.md), [M3 release readiness](jev-order-m3/README.md), [release blockers](release-blockers/README.md) |
| Storage, graph caches and FTS footprint | [LEANN/storage](leann-graph-storage/README.md), [FTS corrections and optimizations](fts-footprint/README.md) |
| Output serialization | [TOON rejection](toon-serialization/README.md) |
| Retrieval diagnostics | [source bodies](source-body-retrieval/README.md), [entry retrieval](entry-retrieval-diagnostic/README.md), [file localization](file-localization/README.md), [path hints](path-hint-utility/README.md), [reachability](router-reachability/README.md), [routed selection](routed-module-selection/README.md) |
| Structure and output experiments | [ast-grep feasibility](astgrep-feasibility/README.md), [outlines](outline-comparison/README.md), [output integrity](output-integrity/README.md), [structural MCP trial](structural-mcp-trial/README.md) |
| Capture/indexing performance | [throughput](indexing-throughput/README.md), [incremental indexing](incremental-indexing/README.md), [change classification](change-classification/README.md) |

## Language milestone evidence

[JavaScript](javascript-v1/README.md), [Rust](rust-v1/README.md), [Go](go-v1/README.md), [Java](java-v1/README.md), [C](c-v1/README.md), [C++](cpp-v1/README.md). TypeScript evidence is the versioned protocol series above. Current support boundaries are documented under [parser limits](../parser-limits.md), not inferred from an old milestone's claims.
