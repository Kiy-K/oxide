# ADR-0014: Precomputed blast radius in search and context results

## Status

**Accepted**, 2026-11-11. The predeclared gate (G1–G4, `docs/decisions.md` row 53) passed,
and the owner accepted the validation items below. The measured costs are optimization debt,
listed in their own section, not open questions. This ADR changes the 2026-11-10
`oxide_search` `mode=impact` implementation recorded in
[ADR-0013](0013-graph-navigation-in-oxide-search.md); ADR-0013's own status is not changed.

## Context

The 2026-11-11 design put the blast radius into `search` and `context`, computed by a walk
per query (`docs/superpowers/specs/2026-11-11-blast-radius-in-search-design.md`). Both
variants failed the predeclared latency gate (G3), recorded in `docs/decisions.md`:

| Variant | Warm search, fixture / requests | Warm context, fixture / requests |
| --- | --- | --- |
| v1: walk through `navigation.walk` (networkx) | 2.12 → 15.75 ms (+643%) / 2.43 → 20.97 ms (+764%) | +129% / +65% |
| v2: reuse `navigation.reach` (edges only) | 2.12 → 15.01 ms (+608%) / 2.43 → 18.50 ms (+662%) | +101% / +53% |

Each entry point needs a depth-2 walk of database queries, which costs milliseconds per
query, and no reuse of the walk code removes that cost. The information is worth the
latency only if an agent reads it, not walks it for every query.

## Decision

1. **Store one blast radius per entity, on its `Entity` row.** Six columns
   (`blast_dependents`, `blast_tests`, `blast_file_count`, `blast_files`,
   `blast_unresolved`, `blast_stopped_by`) hold the bounded upstream walk of
   `Repository.impact` (depth 2, limit 20): reached entities, those that are tests, distinct
   source paths (the first 8 listed, in `blast_files` as JSON), same-name unresolved call
   sites, and the bound that stopped the walk. The generation seals them, so they cannot be
   stale.
2. **Compute it with the same walk.** `core/blast.py` runs `navigation.walk` over an
   in-memory view of the generation's relations. That view mirrors `View.adjacency` and
   `View.mentions`, including the edge allowance, so the stored value equals what
   `Repository.impact` returns for the same entity. `navigation.walk` depends on a protocol
   with one method, `adjacency`, which both the read view and the in-memory view provide.
   The unresolved-site lookup is indexed by name tail, not a scan.
3. **Read, don't walk, in the query that already runs.** `View.entries(keys)` returns each
   entity with its blast radius from the same Cypher query as `View.entities`.
   `search` and `build_context` use it for their entries, and the first `BLAST_ROOTS = 3`
   entries carry `blast`. Ranking, routing and packing do not change, so the payload is
   unchanged.
4. **MCP.** `oxide_search` drops `mode="impact"` and its `key`, `direction` and `depth`
   arguments. Search and context carry `blast`; context text gets one `# blast` line per
   root. The CLI keeps `oxide impact` as the detailed exploration tool and prints the
   summaries under `oxide search` and `oxide context`.
5. **Versions.** `SCHEMA_VERSION` becomes `oxide-ladybug-py-v5`, and `storage_components`
   gains `blast`, whose value includes `NAVIGATION_VERSION`, so any change to the walk changes
   the derivation id. Existing indexes raise `incompatible_index` ("re-index"). They are not
   migrated (SPEC § Compatibility), so users run `oxide index` once.

## Validation (2026-11-11)

**Navigation is not payload recall.** On the ContextBench dev split (15 tasks), the scored
1024-unit payload is byte-identical with and without blast (payload hashes equal on all 15
tasks), so payload recall is unchanged: 0.711 both ways. The G1 gain (0.711 → 0.800) counts
gold files that the blast radius names. An agent sees those files only in the `blast` field
or the `# blast` text line, never in the scored payload. Three gold files, in three tasks
(`psf__requests-2317`, `-5414`, `-2931`), appear only through blast. The blast radius also
names 1.07 non-gold files per task. The same 0.800 holds on the context surface
(`bundle.blast`). Whether agents act on those lines is not measured.

**Large repository.** `ansible` (1,720 Python files, 15,735 entities; no ContextBench tasks,
so neither dev nor held-out) on this machine, 200% CPU and 3 GiB, 5 paired rounds:

| Measure | HEAD | With blast | Change |
| --- | --- | --- | --- |
| Cold index, median | 3.42 s | 4.72 s | +38% (blast walk 1.2 s of it) |
| Peak RSS after index | 382–386 MiB | 346–353 MiB | not higher |
| Storage after index | 49.45 MB | 49.96 MB | +1.0% |
| Search, cold / warm | 8.2 / 7.0 ms | 9.4 / 8.1 ms | +14.5% / +15.5% (later sessions, warm: +5.5% and +8.5%) |
| Context, cold / warm | 90 / 85 ms | 90 / 84 ms | flat |
| Reopen | 148 ms | 148 ms | flat |

- Blast compute peak 31 MB on the Python heap, 2.6 MB retained after publish.
- Stored value equals the live `Repository.impact` walk for every entity whose walk
  succeeds: 15,502 entities, 0 mismatches.
- An index-time regression was found and fixed without changing output. The unresolved-site
  lookup scanned every unresolved edge per entity (33 s of the 35 s blast compute on ansible;
  cold index 3.6 s → 38.6 s). It is now indexed by name tail and equal to the scan on all
  15,735 calls.
- Read-path encodings measured and rejected: one JSON cell for all six fields (no gain,
  +0.45 MB storage); a second query for the three reporting keys (search +21% to +29%);
  `CASE` projection of the blast cells only for reporting keys (no gain).
- Gate G3 (predeclared, fixture and requests) passes on the medians recorded in
  `docs/decisions.md`. Ansible is outside the gate; its numbers are above.

## Optimization debt

Accepted with these measured costs. Each has a known direction; none is a correctness issue.

| Cost | Measured | Direction not yet taken |
| --- | --- | --- |
| Cold index | +38% on ansible (1.2 s of walk per 15.7k entities); +12% on requests (one 5-round run; +6% in a later run); +1% on fixture | Recompute the walk only for entities whose upstream changed (delta publish); bound the cost of hub nodes (the single `repo` walk takes 161 ms) |
| Warm search on large repositories | +6% to +16% on ansible (cold +14.5%) | A read path that does not widen the entity query. The three alternatives measured were all worse or no better |
| Storage | +1.0% on ansible | Fewer or narrower blast fields per row |
| Memory | Not higher on ansible; blast heap peak 31 MB, 2.6 MB retained | Stream the in-memory relations if the peak grows with repository size |

The repeated-`impact` read-pool exhaustion is a separate defect, present on `main` before this
change, and is tracked in [issue #55](https://github.com/Kiy-K/oxide/issues/55). It is not a
cost of this ADR: search and context do not walk at query time, but `oxide impact` and
`Repository.impact` are affected on large repositories.

## Acceptance evidence

The gate is the 2026-11-11 gate, unchanged: G1 navigation usefulness, G2 payload and dev
scores identical to HEAD, G3 latency (search and context, cold and warm, each ≤ +15%), and
G4 token overhead. Index-time cost is reported, not gated. The gate passed (row 53); the
validation above is the evidence for the trade-offs; the owner accepted them on 2026-11-11.

The derivation id changed from `sha256:ff500e5a…` to `sha256:daa271b3…`. The only changed
components are `storage-schema` (v4 → v5) and the new `blast` component. Compatibility of the
frozen contextbench outputs is unchanged: the per-task compatibility fields match the frozen
results. Only the pin was updated, as recorded in `docs/decisions.md` row 54.

## Consequences

- **Costs:**
  - One walk per entity during each publish: 1.2–1.3 s on a 15.7k-entity repository.
  - Six more columns on every `Entity` row (+1.0% storage on ansible); re-index required
    after upgrading.
  - Search reads six more columns per candidate row: +6% to +16% warm on ansible.
- **Risks:**
  - The index-time walk is O(entities) times the bounded walk. Its measured p99 is 0.5 ms
    per entity and the largest single walk 161 ms (`repo`), so high-degree nodes are bounded
    in practice but not in principle.
  - A stored value and `Repository.impact` could disagree if the in-memory view drifts from
    `View`; a test compares them on every entity of a fixture, and the same comparison passed
    on every ansible entity whose walk succeeds.
- **Rejected:**
  - Walk per query (v1, v2 above): fails G3.
  - A separate `Blast` node table read with its own query: a second Cypher execute per call
    cost +26% to +57% on warm fixture search (`micro2`, harness queries), so the blast
    columns moved onto the entity row and the read was merged.
  - A second query for the reporting keys only (search +21% to +29% on ansible).
  - One JSON cell instead of six columns: no measured gain, +0.45 MB storage.
  - Cache per query keyed by generation: adds invalidation state for no gain over storing the
    row at publish.
