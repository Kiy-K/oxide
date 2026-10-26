# Indexing throughput: full-rebuild bottleneck and incremental feasibility

Date: 2026-10-25. Branch `rewrite/v2` at `80f40c2` (plus the uncommitted working tree). No
`src/` change; frozen baselines untouched. Every claim is labelled **measured** or
**inferred**.

## Setup

- Four corpora, one per language (all **measured**, from the index reports):

  | corpus | language | files | entities | relations |
  |---|---|---:|---:|---:|
  | huggingface/transformers@43ee58588be4 | Python | 2,983 | 65,785 | 463,649 |
  | microsoft/vscode@9a281018181d | TypeScript | 4,413 | 89,927 | 553,816 |
  | gravitational/teleport@c2ace99b1c8b | Go | 1,272 | — | 174,385 |
  | nushell/nushell@399319476aa4 | Rust | 1,152 | — | 111,443 |

- Every run happens under `systemd-run --user --scope -p MemoryMax=3G -p MemorySwapMax=0
  -p CPUQuota=200%`, one job at a time, with a fresh private `OXIDE_DATA_DIR`.
  CPython 3.13 (GIL), LadybugDB 0.21.0, btrfs.
- Numbers are medians of 3 runs. The spread between runs was under 6% everywhere.
- Scripts are in `scripts/`; raw JSONL is in `results/`.
  - `profile.py` wraps each stage with a wall clock. There is no profiler, so
    Python-heavy stages are not distorted.
  - `cold.sh` and `warm.sh` drive it.
  - `par_parse.py`, `fts_probe.py` and `drift.py` are the experiments.
  - `blobs.py` summarizes the §8 A/B.

## 1. Where a cold full rebuild spends its time (measured)

Seconds, median of 3:

| stage | transformers | vscode | teleport | nushell |
|---|---:|---:|---:|---:|
| **total** | **26.88** | **26.53** | **6.60** | **4.70** |
| capture | 0.18 | 0.31 | 0.05 | 0.04 |
| derive | 15.47 | 11.46 | 2.73 | 2.05 |
| · parse (tree-sitter + fact extraction) | 8.88 | 8.30 | 2.08 | 1.26 |
| · resolve (global, per language) | 6.11 | 2.16 | 0.50 | 0.65 |
| · encode facts | 0.28 | 0.60 | 0.04 | 0.07 |
| validate | ≤0.10 | ≤0.10 | ≤0.10 | ≤0.10 |
| publish | 11.07 | 14.48 | 3.72 | 2.54 |
| · load (`_load`) | 9.45 | 12.14 | 2.80 | 1.98 |
| ·· FTS terms (`document`) | 0.90 | 1.10 | 0.23 | 0.15 |
| ·· CSV write | 1.06 | 1.28 | 0.36 | 0.25 |
| ·· COPY Entity / Rel / Opaque | 0.67 / 1.16 / 1.72 | 0.90 / 1.00 / 2.52 | 0.18 / 0.43 / 0.42 | 0.12 / 0.45 / 0.15 |
| ·· COMMIT | 0.38 | 0.64 | 0.13 | 0.10 |
| ·· `CREATE_FTS_INDEX` | **3.14** | **4.14** | 0.87 | 0.65 |
| · seal (blobs + fsync) | 1.28 | 2.06 | 0.70 | 0.52 |
| GC pauses (inside the above) | 2.07 | 2.10 | 0.28 | 0.25 |
| peak RSS (MB) | 1,141 | 1,173 | 360 | 288 |

Reading of the table:

- **Derive** is the larger half on Python (58%).
- **Publish** is the larger half on TypeScript (55%).
- The single largest statement is `CREATE_FTS_INDEX` (12–16% of total).
- Parse is the largest Python-side cost (31–33%).
- GC pauses cost about 8% on the large corpora.

## 2. Edit-one-file re-index today (measured)

Each run appends one function to the median source file, then runs `index()` again.
Today's facts.json reuse means only one file is re-parsed.

| | transformers | vscode | teleport | nushell |
|---|---:|---:|---:|---:|
| total (cold → warm) | 26.88 → **19.35** | 26.53 → **19.51** | 6.60 → **4.87** | 4.70 → **3.77** |
| derive | 7.72 | 3.85 | 0.80 | 0.90 |
| · resolve | 5.90 | 2.12 | 0.50 | 0.61 |
| · decode cached facts | 1.37 | 1.55 | 0.26 | 0.18 |
| publish | 10.93 | 14.93 | 3.82 | 2.70 |
| · `CREATE_FTS_INDEX` | 3.09 | 4.26 | 0.96 | 0.67 |
| publish share of warm total | 56% | 77% | 78% | 72% |
| generation / graph.lbug (MB) | 237 / 143 | 281 / 194 | 69 / 48 | 50 / 36 |
| copy graph.lbug + sync (s) | 0.03 | 0.01 | 0.04 | 0.01 |

- After an edit, **publication is the bottleneck** (56–78%).
- Global resolution is the rest on Python.
- Parallel parsing does not help this path, because only one file is parsed.
- The graph.lbug copy is almost free here. That is because coreutils `cp` reflinks on
  btrfs (**inferred**). On ext4 or APFS without clones, it would be a real ~150–200 MB copy.

## 3. Parallel vs sequential parsing, matched caps (measured)

The parent process pre-parses every source file in a `ProcessPoolExecutor`
(forkserver, chunksize 16) inside the same 200% CPU / 3G scope. `derive` then reads
the results through the same `parse` names. `--check` asserted that the pool's
`Parsed` values equal sequential parsing on all four corpora.

| total (s) | transformers | vscode | teleport | nushell |
|---|---:|---:|---:|---:|
| sequential | 27.25 | 26.93 | 6.46 | 5.09 |
| 2 workers | **24.22 (−11%)** | **23.32 (−13%)** | 6.11 (−5%) | 4.50 (−12%) |
| 4 workers | 25.44 | 24.57 | 6.38 | 4.47 |

- With 2 workers, parsing took 5.46 s instead of 8.88 s on transformers, and 5.43 s
  instead of 8.30 s on vscode. That is 1.5×, not 2×. Pickling the `Parsed` values back
  to the parent and the pool start-up take the rest.
- 4 workers on a 2-CPU quota are oversubscribed and slower.
- cgroup `memory.peak` rose by 56–60 MB: vscode 1,348 → 1,404 MB, transformers
  1,227 → 1,287 MB.
- The saving is cold-only (§2). It needs a process pool and pickling inside `derive`.

## 4. Cheap knobs (measured)

Every run in this section kept the lexical output identical. The check is a digest of
`view.lexical(q, 50)` over 16 fixed queries. Digests per corpus: transformers
`d513e18b97dff47d`, vscode `10e6a49381f54ea5`, nushell `f272c06daff148ce`.

| total (s) | transformers | vscode | nushell |
|---|---:|---:|---:|
| baseline | 27.17 | 26.58 | 4.82 |
| GC disabled for the run | 25.29 (−7%) | 24.53 (−8%) | 4.49 (−8%) |
| 2 DB threads for the bulk load | 25.87 (−5%) | 24.75 (−7%) | 4.45 (−8%) |
| both | **23.70 (−13%)** | **22.92 (−14%)** | **4.33 (−10%)** |

- **GC disabled.** `gc.disable()` for the whole index. Peak RSS was the same as the
  baseline, so (**inferred**) the cyclic GC reclaims nothing that matters during indexing.
- **2 DB threads.** The staging `Database` gets `max_num_threads=2` instead of 1;
  `COPY … parallel=false` is kept.
  - `CREATE_FTS_INDEX` drops 3.15 → 2.35 s (transformers) and 4.15 → 3.00 s (vscode).
  - COPY Rel drops by about 20%.
  - RSS rises by 8–12 MB.
  - ADR-0002 requires `max_num_threads` to be set explicitly, not set to 1.
  - Read views keep 1 thread.
- Both knobs also apply to the warm path (§2), unlike parallel parsing.

## 5. Incremental indexing feasibility

### 5a. Relation identity: `site` is not stable (measured)

`drift.py` derives the base, then each of 5 evenly spaced files twice more:

- with a function appended;
- with the file deleted.

Counts, comparing rows that are equal apart from `site`:

| corpus | append: relations renumbered | delete: relations renumbered | delete: other files changed |
|---|---:|---:|---:|
| transformers | 11–90% | 99.4–99.9% | 1–5 |
| vscode | 8–92% | 99.3–99.9% | 2–3 |
| teleport | 9–91% | 99.3–99.9% | 2–21 |
| nushell | 10–87% | 99.0–99.9% | 4–12 |

- `site` is a global ordinal (`enumerate(derived.edges)`). A one-line edit renumbers
  most of the relation table.
- An in-place DB delta keyed by today's identity would rewrite almost every Rel row.
- A stable identity, such as (source key, kind, evidence span, name), would change
  `Relation.site`'s public meaning (`docs/public-api.md`, `models.py`). That needs a
  versioned format change.
- `Entity`/`Relation` `SourceRef` carries the file digest. So every row of the edited
  file changes on any edit. That is expected and stays inside the file.

### 5b. Cross-file invalidation is global (measured + inferred)

- **Measured.** Appending a fresh function changed no other file's relations.
  Deleting a file changed relations in 1–21 other files. That happens because
  resolution re-runs over the whole repository: imports, calls and dispatch that
  pointed into the deleted file become unresolved or re-resolve elsewhere.
- **Inferred from the resolvers.** The scope of a change is not bounded by per-file
  dependencies.
  - Python dispatch (`assigned()`) iterates all files.
  - MRO and `subclasses` use a repository-wide inheritance graph.
  - Rust glob imports follow up to `MAX_HOPS` = 64.
  - TypeScript re-exports (`export *`) chain, and `tsconfig` paths are global.
  - A Go package's scope spans files, and `go.mod` is global.
  - A precise reverse-dependency index would have to mirror every resolver's lookup
    rules. Missing one silently gives wrong edges, which is the failure mode the graph
    navigation study already flagged (signal sensitivity 0.73).
- **Limitation.** The append probe adds a name nobody references. Renaming an export
  others use would reach further (**inferred**, not measured).

### 5c. LadybugDB FTS under in-place mutation (measured)

`fts_probe.py` builds an FTS index over real `document()` terms, then copies the file
(a copy-on-write generation). It mutates 5% of rows each way (DELETE, SET, CREATE) in
one transaction, then compares 501 queries' exact `(key, score)` lists against a
fresh build of the same final rows.

| case | score lists differ | rankings differ | notes |
|---|---:|---:|---|
| 1,312 rows (oxide src+tests), mixed ops | 0 / 501 | 0 | exact at this size |
| 13,979 rows (nushell), mixed ops | — | 6 | 167 **untouched** docs missing from results |
| nushell, DELETE only | 292 | 3 | 106 untouched missing; 6,114 scores differ |
| nushell, SET only | 327 | 3 | 109 untouched missing; 11,575 scores differ |
| nushell, CREATE only | 306 | 0 | 4,489 scores differ (stale BM25 statistics) |
| nushell, mutate then `DROP_FTS_INDEX` + `CREATE_FTS_INDEX` | **0** | **0** | exact; 1.21 s total |

- In-place FTS maintenance in LadybugDB 0.21.0 is **not exact**. DELETE and SET make
  documents that were never touched disappear from results. CREATE leaves corpus
  statistics stale.
- Lexical results are covered by the frozen baselines. So any graph delta must be
  followed by a full FTS rebuild. That puts the 3–4 s `CREATE_FTS_INDEX` floor on
  every incremental publish.

### 5d. Smallest safe incremental path (inferred from 5a–5c, not built)

1. Re-derive fully (today's facts reuse already skips parsing).
2. Diff entity and relation rows against the previous generation by a stable identity.
3. Reflink-copy the previous `graph.lbug` into staging, and apply the delta in one
   transaction.
4. Rebuild FTS (`DROP` + `CREATE`).
5. Readback and seal as today.

It would save, at most, the CSV + COPY + COMMIT share of publish. That is about
4–6 s on the large corpora; the share of the warm path that is not FTS, resolve, seal
or decode. It needs all of these:

- a stable relation identity and a versioned change to `site` (5a);
- proof that the delta-applied database is row-identical to a fresh load. Example: a
  readback digest over all tables, checked at publish;
- reflink support for the copy to be cheap (§2). Elsewhere it is a full copy;
- a correctness argument for global resolution. Re-deriving fully already provides
  this, so the delta is just a storage diff, not partial resolution.

Partial re-resolution (only affected files) is **not** shown to be safe (5b).
Per the task, incremental indexing is not implemented: its publication invariant
(delta DB ≡ fresh DB, FTS exact) is achievable only with a full FTS rebuild. Its
identity invariant needs a public format change that this study does not make.

## 6. Recommendation

**First optimization: turn GC off during `index()`, and give the staging bulk-load
`Database` 2 threads.**

- It is a few lines in `api.py` and `store.py`, and identical output was measured.
- It saves about 5–8% each (§4), and applies to both cold and warm paths (warm is **inferred**: the knobs act on GC and the load, which dominate warm runs, but warm runs were not measured with them). Together, −10–14% cold (measured, §4), matching the 2-worker parse pool without its process pool or +60 MB.
- Accepted and implemented, with HEAD A/B and verification in §7.

Next, in order of value to cost:

1. **2-worker parse pool**, −11–13% on a cold build for +60 MB. Worth it for first
   index, useless for re-index.
2. **Skip re-writing unchanged content-addressed source blobs in seal.** Hard-link
   them from the previous generation (seal is 0.6–2.1 s, warm and cold). Implemented
   and measured in §8: seal −77–89% and warm total −7–13%. Cold builds are unchanged,
   because there is no previous generation.
3. **Copy-on-write graph delta** (5d), only after a stable relation identity is
   accepted as a format change. The FTS rebuild floor stays.

## 7. Implemented: GC off during the build, 2 staging threads (measured)

The first optimization was accepted and implemented as two minimal changes:

- **GC switch** (`src/oxide/api.py`, `Repository.index`):
  - `gc.isenabled()` is saved, then `gc.disable()` runs around derive + validate +
    publish.
  - A `finally` re-enables GC only if it was enabled before.
  - `test_index_restores_the_callers_gc_state` covers GC on/off × published/failed.
- **Staging threads** (`src/oxide/storage/store.py`, `_load`): `max_num_threads=2` on
  the staging `Database` only. Read views stay at 1.
- No flag, version, schema or baseline change.

A/B runs compare HEAD (`git archive HEAD src`, put first on `PYTHONPATH`) against the
working tree. Each run used the same venv and caps, interleaved per corpus
(`scripts/warm.sh`, `results/ab-*.jsonl`). Seconds, median of 3:

| | cold HEAD → new | warm (1 file edited) HEAD → new | peak RSS cold, MB |
|---|---:|---:|---:|
| transformers | 27.02 → 24.07 (**−10.9%**) | 19.68 → 16.74 (**−15.0%**) | 1,141 → 1,144 |
| vscode | 26.41 → 23.09 (**−12.6%**) | 20.01 → 15.69 (**−21.6%**) | 1,172 → 1,162 |
| teleport | 6.47 → 5.89 (**−9.0%**) | 4.51 → 3.86 (**−14.4%**) | 360 → 372 |
| nushell | 4.66 → 4.31 (**−7.5%**) | 3.55 → 3.18 (**−10.5%**) | 288 → 300 |

- **Variance.** One vscode round was an outlier on both sides: HEAD warm 29.64 s, new
  cold 34.01 s. The medians are unaffected.
- **Second session.** A 2-round rerun on 2026-10-26 (`results/ab2-*.jsonl`) ran about
  1.7× slower in absolute terms on both arms, because of the host state. It kept the
  same direction:
  - cold −10 to −14%, warm −10 to −17%;
  - peak RSS up to +27 MB cold and +50 MB warm (≤ +4%).
- **Equal output.** `scripts/digest.py` masks the store's random repository id, which
  is the repository entity's name and is in the manifest. On every corpus, the 2 HEAD
  and 2 new builds gave a single digest for cold and a single digest for warm. The
  digest covers:
  - MANIFEST, facts.json and the retained source names;
  - every row of Entity, Unresolved, Rel and Opaque;
  - the exact `(key, score)` lists of 300 BM25 queries.
- **Gates.** `mise run verify` passes: 388 tests, which include `test_baseline.py`,
  the Python ContextBench gate, publication-atomicity and crash tests
  (`test_recovery.py`, `test_lifecycle.py`), and the new GC test.
  `mise run contextbench-ts` passes 17/17. Both ran under the caps. Derivation and
  baseline versions are unchanged, because the outputs are equal.

Concurrency and portability (**inferred** from the code):

- The GC switch is process-global.
  - The MCP server never indexes, and it serialises everything behind one lock.
  - The CLI indexes once per process.
  - For a library caller indexing from several threads at once: whoever started with
    GC on restores it, so the final state is right. Overlapping builds may run part of
    the time with GC on, which only loses speed.
  - Other threads of the host process run without cyclic collection while a build is
    running.
- `gc` and LadybugDB's thread pool behave the same on Linux, macOS and Windows. Two
  threads on a 1-CPU host are only time-sliced. Not run on the platforms CI.

## 8. Implemented: hard-link unchanged source blobs in seal (measured)

Date: 2026-10-26, against HEAD `b47be65`, which already includes §7.

**Problem (measured, §1–§2).** `Store._seal` wrote every captured file's bytes to
`source/<sha256 hex>` and fsynced each blob, on every generation. After a one-file
edit, all but one blob are byte-identical to the current generation's. Seal cost
0.7–2.7 s of a warm index.

**Change** (`src/oxide/storage/store.py`, `_link` and `_seal`):

- For each captured file, `_seal` first `os.link`s the current generation's blob of the
  same digest into staging. It then reads the linked blob back and keeps it only if
  its bytes equal the captured bytes.
- A missing blob, a file system without hard links, or any `OSError` falls back to
  writing, so behaviour is unchanged.
- A mismatching (damaged) blob is unlinked, never overwritten, because the inode is
  shared. Fresh bytes are then written. So a damaged retained source is never carried
  into the next generation. `test_missing_retained_source_is_never_reported_unchanged`
  tampers every blob, and still passes.
- Only written blobs are fsynced. A linked blob's data was fsynced when its own
  generation was sealed. The new directory entries are made durable by the existing
  `fsync(source)` and `fsync(staging)`.
- Content addressing makes this sound. A blob's name is its sha256, it is never
  written in place, and every read (`View.source`) re-verifies the digest. Deleting
  the previous generation drops one link; the new generation keeps the inode.
- No format, schema, manifest, derivation or public-API change. Generations look
  exactly as before.
- `test_unchanged_sources_are_linked_never_rewritten` checks that after a one-file
  edit, every unchanged blob keeps its inode and only the edited file's blob is new.
  It fails without the change.

**A/B (measured).** HEAD (`git archive HEAD src` first on `PYTHONPATH`) against the
working tree. `scripts/warm.sh` ran with the same venv and caps, the 4 corpora of
§1, 3 interleaved rounds. Medians, seconds (`scripts/blobs.py
results/blobs-head.jsonl results/blobs-new.jsonl`):

| warm (1 file edited) | total HEAD → new | seal HEAD → new | publish HEAD → new | peak RSS, MB |
|---|---:|---:|---:|---:|
| transformers | 29.52 → 27.58 (**−6.6%**) | 1.69 → 0.21 (−87%) | 16.63 → 14.99 | 1,191 → 1,206 |
| vscode | 27.68 → 24.17 (**−12.7%**) | 2.73 → 0.42 (−85%) | 22.49 → 19.16 | 1,221 → 1,201 |
| teleport | 6.45 → 5.85 (**−9.4%**) | 0.89 → 0.10 (−89%) | 5.11 → 4.51 | 394 → 396 |
| nushell | 5.10 → 4.73 (**−7.2%**) | 0.70 → 0.16 (−77%) | 3.67 → 3.30 | 324 → 324 |

- **Seal is no longer a warm-path cost.** What remains (0.1–0.4 s) is the edited
  blob, `MANIFEST.json`, `facts.json`, the `graph.lbug` fsync and the links.
- **The total gain is larger than the seal gain** on vscode and teleport (−3.5 s
  total vs −2.3 s seal on vscode). That is **inferred** to be fsync and writeback
  contention with the concurrent load, which leaves the host. It was not isolated.
- **Cold builds are unchanged, as expected.** There is no current generation to
  link from. Cold seal medians are within run noise: transformers 1.90 → 1.79 s,
  nushell 0.70 → 0.71 s.
- **Variance.** HEAD's warm seal on vscode ranged 0.65–2.95 s (fsync latency). The
  new arm ranged 0.33–0.44 s. Absolute totals are about 1.5× §7's, because of host
  state, on both arms alike.
- **Equal output.** Across all 6 runs of both arms, each corpus gave one cold digest
  and one warm digest (`scripts/digest.py`: manifest, facts, retained source names,
  every table row, 300 BM25 result lists).
- **Disk (inferred from the code, not measured).** While staging and the current
  generation coexist, unchanged sources are stored once instead of twice. Peak disk
  drops by the captured bytes of the unchanged files.

**Portability (inferred from the code, not run off Linux).** `os.link` works on
ext4, btrfs, APFS and NTFS. Where it fails (FAT, some network mounts), the
`OSError` fallback writes the blob as before. Windows `_fsync` opens files for write,
but linked blobs are no longer opened. Staging and generations share one directory,
so a link never crosses devices.

## Negative results

- **4 parse workers** under a 200% quota are slower than 2.
- **In-place FTS update** is not exact at 14k rows (5c), even though it was exact at
  1.3k rows. A small test would have passed wrongly.
- **Per-file invalidation.** Deletes reach 1–21 other files, and the resolvers' scope
  is repository-wide. No safe per-file dependency rule was found.
