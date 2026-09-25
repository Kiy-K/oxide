# PG-2: prospective gate for the candidate-local structural-probe challenger (issue #14)

**Written before any timed run it judges.** Nothing below was chosen
after seeing a challenger number. It follows PG-1
(`challenger/rowid-scan:docs/retrieval-profile/corpus-load-baseline/prospective-gate.md`):
performance thresholds are relative to a same-window null, correctness
thresholds are absolute, and maintenance is a written judgment.

## What is being judged

Structural expansion today reads the whole corpus: `SymbolSnapshot::load`
(`all_symbols` + `all_symbol_relations`) and `RelationIndex::build`, then
`RelationGraph::neighbors`/`callers_of` answer for a handful of seeds.
The challenger answers **the same questions for the same already-selected
seeds** with SQLite probes over `.oxide/index.db`, and hydrates only the
symbols the request then consumes. It lives in
`examples/retrieval_profile.rs` (+ `examples/support/`), never in `src/`.

Two access variants, reported separately:

- **`probe` (existing indexes only).** Everything `idx_symbols_file`,
  `idx_symbols_name` and `idx_symbol_relations_symbol_id` can answer
  locally is probed; the corpus-global parts are answered by one batched
  pass over `symbols`.
- **`probe_diag` (diagnostic indexes on a benchmark copy only).**
  `symbols(qualified_name)`, `symbols(parent)` and a test-reference side
  table. Their build cost, size, and effect on write costs are part of the
  comparison. A variant that needs them cannot become a production
  candidate under this gate: it is a schema change, which needs the
  architecture discussion AGENTS.md requires. It can only inform that
  discussion.

## Access-path table (from the code at `6413856`, before measuring)

| `neighbors()` class / consumer | exact semantics | existing indexes | with diagnostic indexes |
| --- | --- | --- | --- |
| `parent` | *last* symbol in corpus order with `qualified_name == seed.parent`, repo-wide | table scan | point lookup |
| `sibling` / `child` | every symbol with `parent == p`, corpus order | table scan | point lookup |
| `uses` | non-module, parentless defs with `name == r` per reference, import-narrowed | `idx_symbols_name` | same |
| import resolution | `resolve_module_with` over *every* candidate's existence (ambiguity ⇒ `None`) | `idx_symbols_file` | same |
| `imported-definition` | non-module symbols of the resolved file, corpus order, filtered by `references` | `idx_symbols_file` | same |
| `test` (`related_tests`) | `is_test_symbol(t) && (t.references ∋ seed.name ∨ t.name ⊇ seed.name)`, corpus order | table scan (reads `references_json`) | covering scan of `idx_symbols_name` + side-table lookup — still O(N) keys: a substring predicate has no B-tree access path |
| truncation | concatenation above, `truncate(24)` | — | — |
| `context` scoped `callers_of` | `calls ∋ name`, sorted `(file, start_line)` stably over corpus order, filtered to scope files | `idx_symbols_file` → `idx_symbol_relations_symbol_id` | same |
| `--blast-radius` | repo-wide `callers_of`/`implementors_of` + `related_tests` + one hop | `symbol_relations` scan | needs `symbol_relations(target)` |
| `--git` | changed/co-changed symbols by file | `idx_symbols_file`, but `gitctx` takes `&[Symbol]` | same |

Corpus order is `ORDER BY file, start_line` with ties in `(file, rowid)`
order (AGENTS.md); the probe spells it `ORDER BY file, start_line, id`
and compares ids as signed `i64` — the u64 tie-break was PG-1's first bug.

**Corpus-scale, defined up front.** "Corpus-scale work" means a pass over
every row of the `symbols` table b-tree (payload pages, including
`references_json` overflow). A key-only scan of a narrow existing index is
reported separately as "O(N) keys" — still linear, but a different cost.
Exact `related_tests` is O(N) under every variant because of its
substring clause; the question the measurements answer is how narrow that
pass can be and whether it still pays.

## The null control

All stages run through `scripts/corpus_load_baseline.py stages` (one
process per sample, `PROFILE_REPEAT=3`, rep 0 process-cold, reps 1–2
warm), two batches A/B interleaved per repetition, 5+5, with baseline and
challenger stages in the *same* sweep. For each row,

    null(row) = |median(baseline, B) − median(baseline, A)| / median(baseline, A)

A run whose null exceeds 25 % on any row a gate depends on is void and is
repeated, not interpreted. 10+10 only when a gate is within 1× null of its
threshold. `OXIDE_EMBED_SESSIONS=1` on every child. Nothing here is
page-cache-cold; "cold" is process-cold (see the #10 baseline §0).

## S0 — stop-early screen (decides whether the rest is built)

Timed on the queries' real seeds, before the end-to-end challenger exists:
the full `neighbors()` probe for the context seeds (≤ 5) and the search
strong seeds (≤ 3), both variants, against the baseline's
`snapshot_load` + `relation_index` (context) and `all_symbols` +
`relation_index` (search).

**Stop** (record a negative result, build nothing further) if either:
- exact equivalence with the `RelationGraph` oracle cannot be reached for
  every symbol of every corpus used as a seed, or
- the `probe` variant's warm structural cost is ≥ 50 % of the
  corresponding baseline structural cost on either large corpus.

## The gates (all must pass for "production candidate")

- **G1 — Correctness (absolute).** (a) Oracle: with *every* symbol of all
  five corpora as a seed, probe `neighbors()` (relation, id) sequences,
  `related_tests`, per-import `resolve_import`, and file-scoped
  `callers_of` are identical to `RelationGraph`'s. (b) Requests: for the
  stage queries and the 8 parity queries on all five corpora, challenger
  `search` (expansion on) serializes byte-identically to production
  `RetrievalEngine::search`, and challenger `context` byte-identically to
  production `build_context_with` over the same supplied inputs. (c) The
  probe's corpus-order spelling reproduces `--stage order_digest` on every
  corpus. (d) `src/`, `Cargo.*`, schema and production SQL untouched; the
  440 + 55 parity matrix is 0-diff baseline-vs-itself and
  baseline-vs-harness-build; `tests/query_plans.rs` passes; `mise run
  verify` green.
- **G2 — Structural stage.** Warm improvement ≥ 3 × null and ≥ 10 % on
  both large corpora (`pytest`, `pylint`), both batches.
- **G3 — End to end.** The in-process expanded `search` (challenger
  re-implementation vs production, same binary, allocation counting off)
  improves by > 2 × null on both large corpora; `search --no-expand` and
  the one-shot CLI controls move by ≤ 1 × null. **Warm MCP:** probing on
  every call must not be slower than `context_cached` by more than
  1 × null; if it is, a production shape must keep the snapshot cache for
  `oxide mcp`, and that dual path is judged under G6.
- **G4 — Memory.** Peak RSS (`VmHWM`) of the challenger stage process not
  above the baseline's; allocation counts and bytes reported.
- **G5 — Storage/indexing.** `probe`: no change by construction (no
  schema, no index), verified by `index.db` size. `probe_diag`: index
  build time, `index.db` delta, and cold / no-change / single-file-edit
  cost with the indexes present, reported — and, being a schema change,
  it exits this gate regardless of the numbers.
- **G6 — Maintenance (written judgment).** Lines and surfaces; whether
  `neighbors()` semantics would then live in two implementations (graph
  for MCP/blast/git, probes for one-shot); whether each moved guarantee
  has a committed mechanical check; dependencies; how a revert looks.
  Passes only if the write-up names the measured benefit and this cost in
  the same sentence and judges the benefit larger.

## Outcomes

- **Production candidate** — S0 continues and G1–G6 all pass.
- **Reject** — S0 stops, or any gate fails on a valid run.
- **Insufficient evidence** — the run is void (null > 25 %) twice, or a
  gate sits within 1 × null of its threshold after 10+10.
