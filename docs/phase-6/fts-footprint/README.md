# FTS footprint: staging checkpoints and exact top-k optimization

Historical measurements on CPython 3.13 / LadybugDB 0.21.0 / btrfs, baseline `7666a3f`; `CPUQuota=200%`, memory 3 GB. Corpora: nushell `399319476aa4`, teleport `c2ace99b1c8b`, transformers `33cb21150c03`, vscode `2dff5deef71f`. No frozen retrieval baseline was changed.

## Decisions and corrections

- **Corrected attribution:** FTS was 28–33% of live data, not the 55–58% file share in the [earlier study](../leann-graph-storage/README.md). Dead pages occupied 38–40% of the file.
- **ACCEPT staging `auto_checkpoint=False` (`93e4dca`):** disk −29…−33%, generation time −20…−22%; overall timing neutral. VS Code warm peak RSS +109 MB (~9.5%). Across 24 cold + 24 warm runs, digests of rows/facts/source and 300 BM25 query lists matched.
- **ACCEPT exact wide top-k:** query `limit+1` with a minimum width of 10, fall back to the full result on a cutoff tie. Across 400 `repo.search` queries × three rounds × four corpora, total time −11…−16%, p95 −19…−30%; all 12 result digests matched. Plain top-k changed 20/15 query results and was rejected.
- **REJECT two query threads and key-ordered COPY:** BM25 one-ULP drift broke exact parity.
- **ACCEPT overlapping blob fsync and 65,536-entry word-term LRU:** five quiet VS Code rounds gave cold −4%, warm −5%, equal digests, RSS within ±2%. Earlier timing under outside load was confounded and is not the accepted measurement.

Limits: retaining WAL can increase memory; buffer-overflow preservation was inferred, not measured. These are corpus/runtime-specific results, not a general database guarantee. Reproduction needs historical code revisions; current storage policy is documented in the public API and storage review rules.

## Evidence and reproduction

The retained `scripts/` define stage, query and A/B harnesses and their flags. Raw stage/query/A-B JSONL remains external at `~/Projects/oxide-eval-data/oxide-fts-eval`; it was not committed. Full numerical replay therefore requires that external evidence as well as pinned corpora and historical engine revisions. Keep this limitation with any reused performance claim; no benchmark was rerun during consolidation.
