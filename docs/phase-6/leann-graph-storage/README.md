# LEANN-inspired storage and graph traversal: no new cache

Historical study at `7916951`, 2026-10-28. **REJECT compact relation metadata; DEFER traversal caching.** Exact impact results prohibit unmeasured pruning. No LEANN dependency or approximate graph was adopted.

Reported results:

- Compact `Rel` metadata saved at most 0.33%: not worth complexity. FTS does not make terms dispensable; global FTS rebuild cost 1.1–6.6 s. The initial 55–58% FTS file-share attribution was corrected by the [FTS footprint study](../fts-footprint/README.md): dead pages were a substantial confound.
- Four pinned corpora, 120 roots × two directions each = 960 walks, all output parity. Hub caching gave 1.2–2.3× speedup but was dominated by per-walk memoization M (1.83–2.07×). M remained deferred without an end-to-end client workload.
- Whole-graph projection G gave 97–301× steady-state traversal speedup, but cost 1–4 s load time and 35–144 MB per process. No high-volume client justified retaining that projection: deferred, not a shipped optimization.

Corpus revisions: nushell `399319476aa4`, teleport `c2ace99b1c8b`, transformers `33cb21150c03`, vscode `2dff5deef71f`. Limits: single storage samples, M measured only in the second run, baseline spread up to 12%, hub-cache memory inferred. Synthetic repeated walks are not evidence of production request latency.

## Evidence and reproduction

[scripts/storage.py](scripts/storage.py) measures storage; [scripts/traverse.py](scripts/traverse.py) compares traversal variants. Retain the JSONL results and scripts together. Use the pinned corpora in private scratch worktrees/stores; the scripts define their command-line inputs. Historical execution requires the corresponding engine revision, not an assumption of compatibility with today's API. The corrected storage attribution is linked above; do not reuse the original FTS percentage.
