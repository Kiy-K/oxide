# Decisions

The standing architectural decisions of OXIDE v2 and the research verdicts that constrain
them. [SPEC](spec/SPEC.md) is the architecture authority and the [ADRs](adr/) record
individual decisions; this file never changes an ADR's status, and a Proposed ADR is not a
requirement. The evaluation protocol is in [`fixtures/eval/`](../fixtures/eval/README.md).

Reports, harnesses and raw results behind every verdict are frozen at
[`013e791`](https://github.com/Kiy-K/oxide/tree/013e7910d6fe8b9df9ba753bd68e2b6468bb4e51/docs)
(`docs/phase-6/<study>/`, `docs/BOOTSTRAP.md`, `docs/migration-audit/`). The v1
implementation is pinned at `ac985b28`, the replaced Rust/Bun v2 runtime at `f79808a`.

## Standing rules

1. One Python package, `oxide` (ADR-0011). A new package or dependency needs a reuse, packaging or API reason.
2. Legacy v1 (`ac985b28`) is evidence, not a template. Reusing it requires a stated SPEC requirement, a boundary-fit reason and tests. No legacy SQLite schemas, row IDs, migrations or cache locations; derived state is rebuildable from captured source.
3. Repository intelligence lives in `oxide.core`, I/O in `oxide.capture` and `oxide.storage`. LadybugDB objects, native IDs, rows and Cypher stay inside `oxide.storage`; the API exposes only domain values. Integrations (the CLI, `oxide mcp`, editors) are clients of `oxide.api` and never parse, route or select.
4. When SPEC and code disagree, SPEC wins. If SPEC is ambiguous, record the ambiguity and a proposal; don't invent permanent ID, schema, transport or model policy to unblock scaffolding.
5. Vectorless: no embeddings, vector indexes or model runners. Lexical entry points, TreeRouter, the heuristic value and the packer need no model or network.
6. Models judge; deterministic code decides thresholds, traversal, stopping, inclusion, budget, fallback and packing. A `DecisionProvider` seam behind `oxide.core.packing.value` carries abstention and a deterministic fallback; JEV and every learned judge stay optional, and CI never runs a live judge.
7. Structure comes before similarity: retrieval finds entry points, routing finds context, and the TreeIndex keeps cross-edges.
8. Native libraries (tree-sitter, LadybugDB, NetworkX, hashing) do recursive and latency-sensitive work; Python holds bounded policy.
9. Never execute repository code or build hooks while indexing. Respect repository scope and source privacy.
10. Frozen baselines keep their provenance. Never regenerate gold or erase a negative result to make a challenger pass; promoting a quality change to a default needs gate evidence.

## Research verdicts

Each row is closed; its report is under `docs/phase-6/<study>/` at the pin above.

| Area | Verdict |
|---|---|
| Graph ranking (`graph-v1`..`v3`) | Resolution raised routed coverage 0.673 → 0.730. REJECT PPR + communities, dispatch routing and the order tiebreak; the loss after routing comes from budget allocation and the relevance signal. |
| Graph navigation (`graph-navigation`, `-v2`, `networkx-v1`) | Basis for ADR-0012 and ADR-0013 (both Proposed). |
| Agent-guided context (`agc`, `agc-v2`..`v4`) | v2 REJECT (adoption); v4 STOP at stage 1, so ADR-0013 stays Proposed without evidence. |
| Optional judges (`decision-study-v1`..`v3`, `jev-reliability`, `jev-order-m1`..`m3`) | v3 PASS (+0.159 at 4,096 units); reliability PASS only under immediate fallback. M3 and ADR-0009 NOT READY: JEV stays off. |
| Storage (`leann-graph-storage`, [`fts-footprint`](fts-footprint.md)) | REJECT compaction; DEFER the traversal memo. ACCEPT `auto_checkpoint=False`, exact wide top-k, blob fsync overlap with a 65,536-entry LRU. REJECT two query threads. |
| Output (`toon-serialization`, `outline-comparison`, `output-integrity`) | REJECT TOON and outlines. Output-integrity fixes shipped. |
| Retrieval (`path-hint-utility` and diagnostics) | PROMOTE path hints (`oxide-select-greedy-v4`): dev hinted `line_recall@4096` 0.685 → 0.785 (Python), 0.360 → 0.626 (TypeScript). REJECT source bodies; file localization NOT VIABLE; router reachability NEGATIVE. |
| Structure (`astgrep-feasibility`, `structural-mcp-trial`) | ast-grep feasible (94 MB, pinned). REJECT a structural MCP tool: grep F1 0.994 vs 0.978. |
| Indexing (`incremental-indexing`, `change-classification`) | Incremental indexing shipped; change classification NOT WORTH IT. |
| FTS distribution (`release-blockers`) | Upstream replaced the FTS 0.21.0 files, so OXIDE mirrors the original builds ([`fts-mirror/`](fts-mirror/RELEASE.md)). |
| Selection (`cover-selector`, 2026-11-03) | REJECT opt-in budgeted-coverage selector `oxide-select-cover-v0` vs `greedy-v4`: dev verified span_recall@1024 0.508→0.358, line 0.548→0.383; heldout span@1024 0.109→0.018; context warm +20%, RSS flat. Stays experimental, not the default. |
| Selection (`excerpt`, 2026-11-03) | REJECT opt-in body excerpts `oxide-select-excerpt-v0` (header + widest whole-line window that fits by exact rendered units, most query-term hits) vs `greedy-v4`: dev verified span_recall@1024 0.508→0.552, line@256 0.180→0.304, but file_recall@4096 0.900→0.833; heldout span@1024 0.109→0.079, file@1024 0.529→0.408 (excerpts spend budget other files' views used); context warm 47→109 ms, RSS flat. Gold: 217 spans = 185 verified + 32 unverified (dev 3, heldout 29); stage tables count verified only. |
| Indexing (`resolver-scope-index`, 2026-11-03) | PROMOTE: the Python resolver indexes each scope's child declarations by name and precomputes its star-import flag instead of scanning per lookup. Output identical (entities/relations equal, ContextBench gate exact). xarray@1757dff, `scripts/bench.py` 5 iterations under 200% CPU/3G: index_cold median 1.476→1.383 s (−6%); one-file re-index 0.933→0.823 s (−12%, resolution 0.33→0.22 s). |
| Packing (`adjacent-merge`, 2026-11-03) | PROMOTE `oxide-payload-v4`: same-file views separated only by whitespace share one item and heading (−4% of units on dev at every budget). Python dev verified span_recall@256 0.047→0.080, line@256 0.180→0.193; heldout within tolerance (span@4096 −0.009, file@4096 +0.020). TS heldout span@256 0.018→0.036, dev file_recall −0.010. Frozen as py baseline-v20, ts baseline-v17. Labelling only outermost nested symbols as well saved ~1% more units with no recall change: not kept. |
| Indexing (`delta-publish`, 2026-11-03) | REJECT publishing a re-index as a delta on a copy of the previous `graph.lbug` (xarray, one-def insert in `xarray/core/utils.py`; full publish 0.58 s of 0.83 s). The delta is small without edge ordinals (157 entities, 50 Rel, 93 Opaque rows), but `site` is a global ordinal, so 28,159 rows shift in 39 runs. Apply 0.58 s plus checkpoint 0.13 s = 0.95 s total, against a 0.52 s bulk load. Inexact: in-place FTS maintenance (ladybug 0.21) returned 1054 of 1065 hits for one query. Even with stable ordinals, FTS rebuild (0.20 s) and checkpoint (0.13–0.15 s) leave no gain. Other levers: Python resolution of unchanged files ≤0.17 s upper bound, needs exact dependency tracking through the closed-world dispatch; FTS/CSV pipelining ≤0.06 s. Both not pursued. |
| Indexing (`split-lexical`, 2026-11-03) | PROMOTE store schema `oxide-ladybug-py-v4`: the FTS documents and index live in a per-generation `lexical.lbug`, built on a side thread while the graph loads and hard-linked from the previous generation when the document digest is unchanged (the index is a function of the documents alone). Exact: ContextBench py (baseline-v21) and TS (baseline-v18) `results.json` identical to v20/v17 apart from the derivation id, metrics byte-identical. Speed (200% CPU cap): one-def re-index ansible 2.53→2.02 s (−20%), xarray 0.81→0.67 s (−17%); body-only edit ansible 2.52→1.91 s (−24%), xarray 0.81→0.66 s (−19%); TS cold index over 82 corpora 418→352 s (−16%). Cost: concurrent pools raise the VS Code cold-index peak RSS 1150→1347 MB (sampled, 2 runs); streaming the graph rows to CSV instead of holding lists brings it to 1268 MB (+10%), time unchanged, CSV bytes identical. CREATE_FTS_INDEX needs ~10 MiB of pool per MB of documents (VS Code 23.2 MB: 256 MiB fails, 270 MiB fits), so the lexical pool is `BUFFER_POOL + 10 × bytes`; at 2× all 14 VS Code corpora failed. In-place FTS patching was 5× faster than a rebuild but left one-ULP drift in multi-term BM25 sums: REJECT. |
| Indexing (`automatic-freshness`, 2026-11-04) | PROMOTE local in-process watching with incremental capture and full-content recovery. Predeclared gate: in a two-file fixture, unchanged capture reads 2→0 source files and one-file edits read 2→1; automatic updates equal full rebuilds. No retrieval policy change. `scripts/bench.py fixtures/py_repo --iterations 5 --warm 20`, Linux/Python 3.13.17, 200% CPU/3G, same 11 files / 65 entities / 196 relations: median cold index 340.26→354.04 ms, explicit unchanged index 8.04→7.88 ms, reopen 24.26→39.79 ms, warm search 2.04→3.15 ms, warm context 18.57→18.72 ms, peak RSS 188.13→184.06 MiB. Reopen now reconciles source; these are small-fixture overhead measurements, not an end-to-end speedup claim. |
