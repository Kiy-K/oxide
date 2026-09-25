# Issue #14: candidate-local SQLite structural probes

**Verdict: reject the challenger for production.** The existing-index probe is exact on the tested default paths and substantially lowers one-shot search work. It also lowers peak RSS; probing on every warm MCP call would be much slower than the generation-keyed snapshot cache. Keeping both graph and probe implementations would duplicate the `neighbors()` contract for a one-shot-only gain. The diagnostic-index variant additionally needs schema and transactional write-path work; an edit demonstrably leaves its test-reference table stale.

This is a benchmark-only result at `641385666cd5ce65c8d2e627d47fbdc58ec855d2`. `src/`, `Cargo.*`, production SQL, schema, ranking and user indexes are untouched. [PG-2](prospective-gate.md) was restored before any timed run. The code is [the profiler](../../../../examples/retrieval_profile.rs) plus [one probe draft](../../../../examples/support/probe_graph.rs); `sql_probe.rs` was not merged. The benchmark driver pins `OXIDE_EMBED_SESSIONS=1` and records the profiler hash.

## Provenance and method

[The manifest](raw/manifest.json) records the OXIDE commit, unchanged `src/`, binary hashes, Rust 1.98.0, rusqlite 0.32.1, bundled SQLite 3.46.0, CPU/OS, corpus commits, index metadata and frozen retrieval options. The indexed copies have 928 `requests`, 7,281 `pytest`, 14,241 `pylint`, 55 Python-fixture and 41 TypeScript-fixture symbols. They use `hashed-bow-256`, not the native default. The five archived corpora were copied to `/tmp/oxide-issue14-work/idx` and only the copies' `meta.root` was changed so CLI/MCP validation would accept their new path. The original archive and user indexes were not written.

Each stage sample is a fresh process, with three repetitions inside it: repetition 0 is **process-cold**, repetitions 1–2 are warm. A and B batches were interleaved for five processes each. The page cache was not dropped, so none of these is disk-cold. No builds, oracle runs, or other benchmark sessions overlapped a timed sweep. Two early sweeps were discarded: the first selected top-three direct hits instead of production's strong lexical seeds; the second ran a production parity check after repetition 0 inside each probe process, contaminating warm timings and peak RSS. Parity now has separate untimed `*_check` stages. The final [stage samples](raw/stages.jsonl), [diagnostic-index samples](raw/diag/stages.jsonl), and [counting-disabled samples](raw/counting-disabled/stages.jsonl) are the completed runs. `run_id` differs across those three sequential invocations; the final profiler source and binary did not change between them. Counting-disabled still incurs one relaxed flag load per allocation, so it is a lighter profiler, not the uninstrumented `oxide` CLI.

## Correctness

The [scan oracle](raw/oracle_pylint.json) and [diagnostic-index oracle](raw/oracle_diag_pylint.json) checked **every symbol in all five corpora** (22,546 per variant) against `RelationGraph`: ordered `(relation, id)` neighbors, untruncated `related_tests`, per-import resolution, scoped `callers_of`, and hydrated `calls`/`bases`. Both variants had zero mismatches. The tested corpus-order key `(file, start_line, signed id/rowid)` reproduced `all_symbols` order on all five corpora; this is an empirical assertion, not a SQLite tie-order guarantee. The probe's `related_tests` path still inspects names at corpus scale because of `name.contains(seed.name)`.

The diagnostic expanded search and default context were byte-identical to production for the stage query plus eight parity queries on every corpus: [45 search and 45 context comparisons](raw/probe_request_parity.jsonl), run separately from timing. One pre-existing CLI/MCP difference matters: a one-shot context engine hydrates direct seeds before it loads relations, leaving their `calls`/`bases` empty; a supplied MCP snapshot hydrates those fields. The context adapter preserves the one-shot output shape after using full relations for graph work. It has not been wired into production or MCP. Opt-in `--git` and `--blast-radius` challenger context paths were not implemented, which also limits any adoption claim.

Production's [parity summary](raw/parity_summary.json) has zero differences for baseline versus itself and the final harness binary across **440 retrieval and 55 literal outputs**. The final production binary and archived baseline have different build hashes but unchanged `src/` and byte-identical outputs. `mise run verify` passed (fmt, clippy with and without default features, both test suites, fixture benchmark and installer checks). The first sandboxed run failed six mock-server tests solely because loopback bind returned `Operation not permitted`; the same full gate passed with loopback permission. `tests/query_plans.rs` passed with and without `ANALYZE`.

## PG-2 S0 and request costs

Warm medians below are batch A / B in milliseconds; the same-window null is the absolute A/B baseline-median difference divided by A. The largest S0 warm null in these rows is 5.8%, well below the 25% void threshold. S0's `probe` cost is below 26% of the matched baseline in every row, so the stop-early screen **continued**.

| Corpus | Structural stage | Baseline A / B | Probe A / B | Probe / baseline |
| --- | --- | ---: | ---: | ---: |
| pytest | search | 32.29 / 33.06 | 8.34 / 8.48 | 26% |
| pytest | context | 42.93 / 40.42 | 8.80 / 8.76 | 21% |
| pylint | search | 49.27 / 49.40 | 10.75 / 10.48 | 22% |
| pylint | context | 56.42 / 58.62 | 10.58 / 11.02 | 19% |

The S0 probe's first repetition issued 3–4 SQL statements and visited 7,313–14,464 rows for the selected seeds, reading 1.11–1.37 MB of text. It made about 73k allocations / 3.6 MB on `pytest` and 45k / 1.8 MB on `pylint`, versus the structural baseline's 290–318k / 17.8–19.9 MB and 282–307k / 21.4–23.5 MB. Those are *cumulative* allocated bytes, not retained RSS; overlapping stages are not summed.

| Corpus | Warm in-process stage | Baseline A / B | Probe A / B | Baseline null |
| --- | --- | ---: | ---: | ---: |
| pytest | expanded search | 36.46 / 35.46 | 18.86 / 18.26 | 2.7% |
| pylint | expanded search | 59.17 / 57.64 | 32.11 / 31.34 | 2.6% |
| pytest | default context | 43.12 / 42.05 | 34.09 / 33.12 | 2.5% |
| pylint | default context | 67.15 / 65.03 | 63.66 / 62.11 | 3.2% |

The counting-disabled run shows the same direction: warm expanded search 32.94–33.96 → 18.16–18.19 ms on `pytest`, 55.32–56.01 → 31.07–31.09 ms on `pylint`; context 40.41–42.48 → 32.31–33.38 ms and 62.44–63.51 → 60.10–61.64 ms. The probe search adapter performs an extra lexical pass and materializes up to 400 direct hits; context repeats seed search to supply a bounded snapshot. Their measured gains are conservative for a hypothetical integrated implementation, but these adapters are not a production design.

The unaffected `search_noexpand` control was 7.62 / 7.05 ms (`pytest`) and 13.30 / 14.11 ms (`pylint`) warm. On the final **uninstrumented production CLI**, ten one-shot samples had medians of 61.74 / 16.65 / 69.31 ms for `pytest` expanded search / no-expand / context, and 81.65 / 20.99 / 95.62 ms on `pylint`; these are controls, because the probe is not wired into CLI. [MCP samples](raw/mcp.jsonl) likewise control the existing cache: first-call search/query medians were 60.52 / 62.47 ms (`pytest`) and 81.95 / 86.45 ms (`pylint`); steady medians were 10.50 / 9.45 and 16.13 / 15.81 ms. The probe context stage itself takes about 33–34 ms (`pytest`) and 62–64 ms (`pylint`) warm, versus `context_cached` about 8 and 13 ms. Probing each warm MCP call therefore fails G3's cache control; no MCP probe route was installed to claim a direct wire-level comparison.

## Memory, plans, and diagnostic indexes

One-shot cold-process probe search peaks near 18 MB versus 33–34 MB for the corpus-load baseline. Across three requests in one process, `VmHWM` for warm expanded search remains about 20 MB versus 34 MB on `pytest` and 20 MB versus 35 MB on `pylint`; warm context is about 20 MB versus 35 MB and 22 MB versus 36 MB. G4 passes. The process high-water mark includes SQLite/page-cache and allocator retention; it is not a measurement of live Rust objects. The earlier, higher probe RSS was caused by the now-removed in-process parity check loading a full snapshot after repetition 0.

[Query plans](raw/query_plans.json) were captured with bundled SQLite 3.46.0 on existing-index and diagnostic copies, before and after `ANALYZE` on further copies. Existing `qualified_name`/`parent` and global `related_tests` access scans `symbols`; `name` and `file` predicates use existing indexes. The diagnostic copies use `diag_symbols_qualified` and `diag_symbols_parent` point lookups plus the `diag_test_refs` primary key, while their name-substring pass remains an O(N) covering-name-index scan. Scoped callers use `idx_symbols_file` then `idx_symbol_relations_symbol_id`; hydration uses the rowid; the exact vector path remains `SCAN embeddings`. No production query plan changed.

Diagnostic indexes were built **only on `/tmp` database copies**. One build added 1,622,016 bytes to `pytest` (36,552,704 → 38,174,720) in 49.2 ms and 1,097,728 bytes to `pylint` (50,257,920 → 51,355,648) in 26.8 ms; WAL was zero after close. Their warm S0 probe was 3.47–4.86 ms on `pytest` and 6.49–7.05 ms on `pylint`. The [controlled edit check](raw/diag_edit_check.json) on a separate `py_repo` copy added a test referencing `RetryPolicy`: `oxide index` indexed it, but `diag_test_refs` had no row for the new symbol until `probe_diag_build` was rerun. Its single no-change/edit/refresh times are diagnostic only, not a paired large-corpus write benchmark. This design cannot satisfy the storage/write-cost gate without transactional side-table maintenance, migration and versioning, so no further cold-index/edit timing was used to argue for it.

## Decision and next action

The existing-index path passed S0, the tested default-output oracle, G2's structural-stage threshold, G3's expanded-search threshold and G4 memory. A cached MCP service must retain the current snapshot path to avoid a large warm-call regression. Maintaining that plus a separate one-shot probe implementation **does not pass G6**: it duplicates graph semantics, seed selection and direct-hit provenance, needs special treatment for the existing CLI/MCP seed hydration difference, and has no demonstrated opt-in Git/blast-radius equivalence, for a default-context gain of only about 3 ms on `pylint`. The diagnostic-index path is not eligible under PG-2 and its side table is stale after an edit. This rejects adoption while preserving the measured search and memory wins.

**One next action:** investigate a compact reference representation with transactionally maintained test-reference lookup as a separate architecture and migration proposal. The diagnostic copy's narrower reads and stage benefit support that direction more directly than another wide-row layout tweak. Do not wire this probe into production as part of issue #14.

Codex review applied `docs/review/`'s structural scope and evidence rules to the diagnostic diff. The review found the in-process parity contamination described above; it was fixed and all affected timed rows were rerun. The remaining limitations are explicit gates: no production call site was added, the probe's scoped callers cannot feed an unbounded context path, opt-in context variants are unproven, and the diagnostic side table has no writer maintenance. `git diff --check` is clean.

Reproduce the paired stage runs with the final built binaries and valid indexed copies:

```bash
cargo build --release -j 2 --bin oxide --example retrieval_profile
W=/tmp/oxide-issue14-work # copied indexed corpora; each index meta.root points to W/idx/<corpus>
O=docs/retrieval-profile/corpus-load-baseline/structural-probe/raw
python scripts/corpus_load_baseline.py manifest --work "$W" --out "$O" --skip-native
python scripts/corpus_load_baseline.py stages --work "$W" --out "$O" --stages struct_base_search,struct_probe_search,struct_base_context,struct_probe_context,search_noexpand,search_expand,search_expand_probe,context,context_probe,context_cached --reps 5 --in-process 3
PROFILE_COUNT_ALLOCS=0 python scripts/corpus_load_baseline.py stages --work "$W" --out "$O/counting-disabled" --corpora pytest,pylint --stages search_noexpand,search_expand,search_expand_probe,context,context_probe,context_cached --reps 5 --in-process 3
mise run verify
```

Run `probe_oracle` once per corpus with `--stage probe_oracle --json`; `probe_oracle_diag` and `probe_diag_build` require disposable database copies. Run `search_expand_probe_check` and `context_probe_check` for the stage query and eight parity queries per corpus, outside timed sweeps. The baseline #10 `cli`, `mcp`, and `parity` commands generated the other raw files. The initial moved-archive parity attempt failed root validation and is not a sample; the `/tmp` copies resolved it. No sample was excluded from a completed final sweep.
