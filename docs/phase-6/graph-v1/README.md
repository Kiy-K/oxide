# Graph v1: resolution promoted; ranking algorithms rejected

Historical decision, 2026-10-23. These are reported measurements, not a description of the current API. Protocol: [preregistration](preregistration.md), frozen at `984dcab`.

- **Promote Python v4 resolution:** nested import roots, re-exports and module/class attributes. No resolved edges lost across flask, requests, xarray and pytest; 48/48 sampled new edges correct. Jedi agreed on 2,241/2,337 edges, missed 92 and disagreed on 4 subsequently judged oracle errors. Index cost +2–7%.
- **Ship bounded `Repository.impact`:** distance and proven distance remain separate; heuristic/ambiguous paths are never proof. Most random methods have no resolved dependents. Empty output does not establish safety; see [navigation completeness](../graph-navigation/README.md) and the [current API](../../public-api.md).
- **Reject PPR:** Python/TypeScript dev `line_recall@4096` stayed 0.659/0.370 in all B/D/P arms (required P−D ≥ 0.02). Admitting routed neighbours reduced gold-unit share by 0.045/0.025 without gaining recall.
- **Reject Louvain regions:** community-over-directory gold share +0.056/+0.064 missed the +0.10 gate; TypeScript communities were 16× larger.
- **Native `algo` extension unavailable at LadybugDB 0.21.0:** digest-verified Linux load failed for missing `libnetworkit.so`; dependent libraries returned 404. No cross-platform load success is claimed.

Evidence: `audit/` (v3/v4 relations, impact cost), `results/` (PPR, community and Jedi comparisons), and `scripts/` retain the detailed tables and probes. Python [baseline-v12](../baseline-v12/README.md) improved routed coverage but not packed line/span recall; TypeScript [v9](../typescript-v9/README.md) was identity-only. Algorithm studies used dev only; held-out was opened only by the baseline gates.

Limits: Python instance/inherited calls and dynamic imports were unresolved at this stage. VS Code impact latency was dominated by by-name unresolved scans (176 ms p50); [graph v2](../graph-v2/README.md) supersedes that cost finding. SCC size alone was not ranking evidence.
## Reproduce

    uv run python docs/phase-6/graph-v1/scripts/graph_audit.py OUT.json REPO...
    uv run python docs/phase-6/graph-v1/scripts/edge_sets.py x OUT.json REPO...   # per version
    uv run python docs/phase-6/graph-v1/scripts/edge_diff.py AFTER.json BEFORE.json
    uv run python docs/phase-6/graph-v1/scripts/impact_perf.py REPO...
    uv run python docs/phase-6/graph-v1/scripts/mentions_cost.py REPO
    uv run python docs/phase-6/graph-v1/scripts/algo_probe.py DIR
    uv run python docs/phase-6/graph-v1/scripts/jedi_extract.py EDGES.json REPO...    # per version
    JEDI_VENV/bin/python -I docs/phase-6/graph-v1/scripts/jedi_judge.py EDGES.json ROWS.json
    uv run python docs/phase-6/graph-v1/scripts/ppr_run.py {py|ts} {B|D|P} OUT
    uv run --with networkx==3.5 python docs/phase-6/graph-v1/scripts/community_probe.py {py|ts} OUT.json
