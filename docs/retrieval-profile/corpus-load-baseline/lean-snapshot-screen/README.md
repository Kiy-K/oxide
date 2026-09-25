# Compact references after issue #14: lean-snapshot screen

> **Follow-up:** the lean snapshot was built and gated end to end. See [`../lean-snapshot/`](../lean-snapshot/README.md) for the implementation, parity and PG-3 results.

**Result: the side-table direction proposed by #14 is not supported.** #14's report
([structural-probe](../structural-probe/README.md), "One next action") proposed a
compact reference representation with a transactionally maintained test-reference
lookup. This cheap screen asked the question behind that proposal first: how much of
the one-shot corpus load is `imports`/`references` payload that `RelationGraph` never
reads? It then compared a narrow side table against simply not decoding that payload.

The comparison is against `full_raw`, a like-for-like control. It is the full
production projection and decode on the same connection setup, and loaded at
96–98 % of production `all_symbols`.

- **A narrow test-reference side table** (schema change + migration + write-path
  maintenance) loaded at 97–98 % on `pytest` and 82 % on `pylint`.
- **Skipping `imports_json`, and decoding `references_json` only for test
  symbols** — no schema, no migration, no write path — loaded at 77 % on `pytest`
  and 83–85 % on `pylint`. It made 61–63 % fewer allocations, and `neighbors()` was
  identical for every symbol as a seed.

The side table therefore buys nothing on `pytest`, and at most 1–3 points on
`pylint`. That is far short of what a schema change costs.

The better-supported candidate is a **lean snapshot** — a loader change, not a
storage change. It is **not a drop-in**. Three kinds of consumer read snapshot
symbols as complete (§3), and the measured win is an upper bound of about 8–15 % of
a warm expanded one-shot request, before rehydration costs. Nothing in `src/`,
`Cargo.*`, the schema, or production SQL was changed. [PG-3](prospective-gate.md)
is written for an end-to-end prototype, which has not been built.

## 1. Method

`examples/lean_snapshot_screen.rs` (new, diagnostic only) times five loads of the
same `symbols` rows in the same `ORDER BY file, start_line`:

| variant | reads | decodes | schema |
| --- | --- | --- | --- |
| `full` | production `SqliteStore::all_symbols()` | production decoder | unchanged |
| `full_raw` | the full production projection | the example's decoder, including `all_symbols`' per-file parse-once-then-clone `imports` | unchanged |
| `lean_noimp` | no `imports_json` | `references` for every row | unchanged |
| `lean_test` | no `imports_json` | `references` only where `is_test_symbol` | unchanged |
| `lean_side` | neither JSON column | test references merged from `screen_test_refs(symbol_id, ref)` `WITHOUT ROWID` | side table on the copy |

`full_raw` and the lean variants share one decoder and one connection setup. That
setup is `SQLITE_OPEN_READ_ONLY` plus `PRAGMA query_only = ON; BEGIN DEFERRED;`, the
same as `SqliteStore::open_read_only`. `full_raw` isolates the effect of the
re-implementation, and `--oracle` asserts that it serializes byte-identically to
`all_symbols`.

**Inputs.** The #14 indexed copies of `requests`, `pytest` and `pylint` (source
paths and SHA-256 in the [manifest](raw/manifest.json)) were copied to a tmpfs
scratch directory. The side table was built on those copies before timing, so every
variant read the same post-build file.

**Timing.**
- One process per sample, pinned to one P-core with `taskset -c 6`.
- 3 in-process repetitions per process: rep 0 is process-cold, and warm is the
  median of reps 1–2.
- Batches A/B interleaved 5 + 5 per corpus × variant.
- The page cache was warm throughout. The sweep took 18 s.

Raw data: [stages](raw/stages.jsonl), [side-table builds](raw/side_build.jsonl).

**Discarded sweeps**, kept in [`raw/discarded/`](raw/discarded/) and none
interpreted:
1. **Lean variants on a bare autocommit connection.** This sweep had no `full_raw`
   control, so it compared unlike connections.
2. **Unpinned.** The `pylint` `full` A/B null was 25.4 %, above PG-2's 25 % void
   line, with a background browser holding the load average near 4.
3. **The unpinned `pylint` repeat.** The null was 20.8 %, and the samples were
   bimodal, around 50 against 75–85 ms. That fits scheduling onto the i7-13620H's
   3.6 GHz E-cores (CPUs 12–15) against its 4.7–4.9 GHz P-cores.

Pinning is a deviation from #14's unpinned method. It applies to every variant
equally.

**Correctness screen** ([oracle](raw/oracle.jsonl)). With every symbol of the corpus
as a fully hydrated seed, `RelationGraph::neighbors` over the `lean_test` corpus
returned the same `(relation, id)` sequence as over the production corpus: 0
mismatches for 928 / 7,281 / 14,241 seeds (864 / 7,140 / 13,667 with non-empty
neighbors). Corpus order was identical. This is an id oracle. It does not check
serialized output, and its seeds were hydrated, which §3 shows production does not
guarantee.

## 2. Results

Pinned. Warm and process-cold medians in ms, batch A / B. Allocations and bytes are
cumulative for rep 0.

| corpus | variant | warm A / B | cold A / B | allocs | alloc MB | warm vs `full_raw` |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| pytest | `full` | 38.43 / 37.87 | 57.60 / 56.47 | 283,140 | 16.5 | 104 % / 102 % |
| pytest | `full_raw` | 36.92 / 37.25 | 46.97 / 47.51 | 283,142 | 16.5 | — |
| pytest | `lean_noimp` | 32.03 / 32.59 | 38.78 / 39.04 | 162,079 | 12.5 | 87 % / 87 % |
| pytest | `lean_test` | 28.36 / 28.73 | 32.93 / 34.21 | 103,856 | 8.3 | 77 % / 77 % |
| pytest | `lean_side` | 36.15 / 36.22 | 42.58 / 41.15 | 89,307 | 8.5 | 98 % / 97 % |
| pylint | `full` | 61.58 / 62.52 | 72.45 / 72.79 | 267,999 | 19.0 | 102 % / 104 % |
| pylint | `full_raw` | 60.09 / 60.26 | 70.31 / 70.44 | 268,001 | 19.0 | — |
| pylint | `lean_noimp` | 55.60 / 54.89 | 63.47 / 63.28 | 195,112 | 16.7 | 93 % / 91 % |
| pylint | `lean_test` | 49.70 / 51.39 | 54.41 / 55.58 | 103,834 | 11.2 | 83 % / 85 % |
| pylint | `lean_side` | 49.37 / 49.59 | 53.46 / 53.04 | 75,365 | 10.9 | 82 % / 82 % |

Same-window A/B nulls were 1.5 % for `full` and 0.9 % for `full_raw` on `pytest`,
and 1.5 % and 0.3 % on `pylint`. `lean_test` saves 8.5–8.6 ms on `pytest` and
8.9–10.4 ms on `pylint`, against `full_raw`. The smallest of those savings is about
10× the larger null. `full_raw` runs 2–4 % faster than `full`. That gap is small but
above the null, so every ratio above is taken against `full_raw`, not production.
`requests` is in the raw data but excluded, as PG-2 excluded it: its stage takes
3–4 ms.

What the rows say:

- **Imports alone** are 7–13 % of the load. `all_symbols` parses each file's list
  once but clones it into every row. On `pytest` that clone is about 121k of 283k
  allocations.
- **Non-test references** are the next 6–10 %. Tests are 45 % of `pytest`'s
  symbols and 8 % of `pylint`'s, and hold 42 % and 11 % of all reference items.
- **The side table does not pay.** Dropping `references_json` from the scan saves
  SQLite little, because the load is dominated by the `idx_symbols_file` walk and
  rowid seeks (#10 §2). Reading the side table back costs an owned `String` per row
  plus an id map. On `pytest`, where the test references are many, that eats the
  entire saving. Its build added 1.1 MB (`pytest`) and 0.27 MB (`pylint`) to the
  database. Keeping it current would need `lexical_postings`-style transactional
  maintenance, a version key and a fallback.

## 3. Why `lean_test` is not a drop-in

A lean `Symbol` has empty `imports`, and empty `references` unless it is a test.
Nothing distinguishes it from a complete one, and three kinds of consumer treat it
as complete.

**1. Seeds taken from the snapshot.** `neighbors(seed)` reads `seed.imports` and
`seed.references`. Seeds can come straight from the snapshot:
- `review.rs:53` iterates snapshot symbols as seeds.
- `gitctx::build_git_context` takes snapshot symbols, and its `changed_symbols`
  seed `evidence/coordinator.rs:176`.
- `RetrievalEngine::hydrate` (`retrieval.rs:436`) clones hits from the snapshot
  whenever one is loaded. MCP always supplies one, so on that path search's strong
  seeds are snapshot symbols.

A lean seed silently loses its `uses` and `imported-definition` neighbors, and the
id oracle above cannot see it.

**2. Serialized output.** `SearchHit`, `ContextItem` and `gitctx::ChangedSymbol`
`#[serde(flatten)]` the whole `Symbol`. Search's expansion-only hits are inserted
straight from the graph's corpus (`expansion_symbols.entry(cand_id).or_insert(cand)`,
`retrieval.rs:698`). A lean symbol would therefore emit `"imports":[]` and
`"references":[]` in `--json`, which changes the byte parity of `search`, `context`,
`review` and `--git`. `BlastItem` copies only identity and span fields, so it is
unaffected.

**3. The in-memory lexical fallback.** `retrieval.rs:360` builds `LexicalIndex` from
`engine.snapshot()` when the persisted BM25 index is absent or of another version.
`lexical.rs:62-65` indexes `references` and `imports`.

A production shape therefore needs:
- a completeness marker on `SymbolSnapshot`, like `with_relations`;
- a full load whenever the lexical fallback is active;
- rehydration through `symbols_by_ids` of every snapshot symbol that becomes a seed
  or leaves as output.

For MCP there are two options:
- **(a)** Keep the full snapshot in the process cache. That is one graph but two
  loader shapes.
- **(b)** Go lean everywhere, and pay a bounded rehydration per request. #10
  measured `hydrate` ×400 at 1.5–2.1 ms, against 10–16 ms steady MCP calls.

## 4. Size of the win, stated against its cost

The saving measured here is on the load alone: 8.5–10.4 ms warm. #10's warm
in-process `search_expand` was 57.9 / 94.8 ms (`pytest` / `pylint`) and `context`
was 70.5 / 105.7 ms. Those figures were measured at an earlier commit, so the
comparison is approximate. Against them, the saving is an upper bound of about
8–15 % of an expanded one-shot request, before rehydration is subtracted. Warm MCP
gains no latency, because its snapshot is already cached. Only that snapshot's
memory would shrink.

The cost is a `Symbol` that is only partly populated, flowing through types shared
by the graph, three serializers, `review`, `--git`, and the lexical fallback. Every
consumer must either never read the missing fields or rehydrate first, and only a
mechanical check (PG-3 G1/G6) keeps that true. Whether about 10 ms per one-shot
expanded request is worth that invariant is the decision this screen hands over. It
does not settle it.

## 5. Limitations

- **Stage-only.** No production loader was changed. No end-to-end request, CLI,
  MCP or rehydration cost was measured.
- **One merge strategy.** The side-table merge was tried as an id → row map only.
  An ordered merge join was not tried. It could narrow `lean_side`'s `pytest` gap,
  but not the schema cost that makes it ineligible.
- **Limited conditions.** Page cache warm, one machine, one pinned P-core, and a
  hashed embedder (which does not affect this stage).

## 6. Reproduce

```bash
cargo build --release -j 2 --example lean_snapshot_screen
B=target/release/examples/lean_snapshot_screen
DB=<copy>/.oxide/index.db          # a disposable copy of an indexed corpus
$B "$DB" --oracle                   # read-only; also asserts full_raw == all_symbols
$B "$DB" --build-side               # WRITES the copy: adds screen_test_refs
for v in full full_raw lean_noimp lean_test lean_side; do
  taskset -c 6 $B "$DB" --variant $v --reps 3
done
```

Interleave the variant loop A/B five times per corpus, as described in §1.
