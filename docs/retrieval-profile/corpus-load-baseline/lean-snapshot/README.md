# Lean corpus snapshot: implementation and PG-3 result

> **Status (2026-09-25): production candidate, pending your review and not committed.** The final formatted build (`7e87ba9a…`) passes every PG-3 gate, with two amendments pre-registered before their challenger measurements. PG-3.1 (`--no-expand` equivalence) is equivalent on all 6 rows. PG-3.2 (warm MCP over 80 independent servers per binary, with a same-schedule A/A) is equivalent and *faster* on all 4 rows, R 0.94–0.98 with 90 % CIs within ±0.1 %, and server RSS is 48.5 → 36–37 MB. The original G3 clauses' failures are preserved; see PG-3's [outcome sections](../lean-snapshot-screen/prospective-gate.md#pg-32-outcome). The final same-window end-to-end rows are in [final-e2e](raw/final-e2e/).

**Decision: production-ready (pending your review).** `SymbolSnapshot` now loads lean by default:
- every symbol has an empty `imports`;
- `references` is kept only on test symbols;
- each symbol is explicitly marked `Completeness::Partial`.

Every symbol that becomes a `neighbors()` seed or leaves as output is completed from the store first.

On the pinned corpora, one-shot expanded requests are 16–27 % faster end to end. The first `oxide mcp` call is 18–24 % faster and peak memory is 23–32 % lower. Output is byte-identical to frozen `main` across 1,056 outputs.

**This verdict overrides the letter of PG-3.** Its `--no-expand` control clause (≤ 1× null) is not met: lean exceeded it on 3 of 6 rows, by −2.0 % to +0.9 %, with mixed signs.

An A/A control ran the *same* baseline binary as both "base" and "challenger" through the same harness. It exceeded the clause on 4 of 6 rows (up to +1.8 %; [control-aa](raw/control-aa/)). The clause cannot distinguish identical binaries at this resolution. The lean control result sits inside that A/A noise, with identical allocations and RSS on a path that runs no new code (§4). PG-3's thresholds are left as written, and the override is stated here rather than hidden.

This follows the [lean-snapshot screen](../lean-snapshot-screen/README.md) and is judged against its [PG-3 gate](../lean-snapshot-screen/prospective-gate.md). Baseline: `main` at `641385666cd5ce65c8d2e627d47fbdc58ec855d2`.

## 1. What changed

| Piece | Where |
| --- | --- |
| `Symbol::completeness` (`Complete` / `Partial`). Never serialized when complete, so JSON is unchanged; serializing a partial symbol is an error. | `src/symbols.rs` |
| `symbols::is_test_symbol`, the one test predicate shared by `related_tests` and the loader | `src/symbols.rs`, `src/relations.rs` |
| `IndexBackend::all_symbols_lean`: one `load_corpus(lean)` and one row decoder with the full load. `LEAN_CORPUS_SQL` reads `NULL` in `imports_json`'s place. | `src/storage.rs` |
| `SymbolSnapshot::load` / `load_without_relations` are lean when `retrieval::lexical_persisted`. They are complete otherwise, so the in-memory BM25 fallback's single load is unchanged. | `src/retrieval.rs`, `src/structural_relations.rs` |
| `retrieval::complete_symbols`: one bounded `symbols_by_ids` read that fills `imports`/`references` and keeps `calls`/`bases` | `src/retrieval.rs` |
| Completion points (see below) | `retrieval.rs`, `context.rs`, `evidence/coordinator.rs`, `review.rs` |
| Backstops: `neighbors()` and `LexicalIndex::build` assert completeness | `src/relations.rs`, `src/lexical.rs` |

Completion points:
- search's strong seeds and returned hits;
- context candidates;
- the coordinator's git-changed seeds;
- review's seeds and `related`.

There are no schema, index, write-path, ranking or default-option changes, and no configuration knob. The lean load is simply the default.

The MCP process cache holds the lean snapshot too. This is a single shape and was not a separate option (PG-3's shape (b)). A cached request completes only its ≤ 3 seeds and ≤ `limit` hits. It does not complete all 400 hydrated candidates, which the counting store now bounds.

**Size.** Non-test production code is +356 / −95 lines across 13 files, a large share of it doc comments. The rest is tests, one mechanical `completeness: Default::default()` per `Symbol` literal, harness flags, and docs.

**Revert.** Revert the `src/` diff. No persisted format changed, so every existing index works with either binary.

## 2. Correctness, before any timing

The binary pair below is the one timed in §3:
- **Base:** `e69d27d9…`, built from `6413856`.
- **Lean:** `2fa67683…`.

- **Real-corpus output parity** ([summary](raw/parity_summary.json)): **1,056 outputs, 0 diffs**, and base against itself also 0 diffs. The corpora were `requests`, `pytest`, `pylint`, `py_repo` and `ts_repo`, plus `pytest`/`pylint`/`py_repo` copies with `meta.lexical_index_version` removed, which forces the in-memory BM25 fallback. Per corpus, the matrix covered:
  - 8 queries × 11 search/context surfaces (lexical, semantic, hybrid, no-expand, quality, `--blast-radius`, context fast/balanced/quality/`--git`/`--blast-radius`);
  - 11 literal searches;
  - `review`;
  - the MCP `search` and `query` response bodies for a cold call and a cached-snapshot call.

  Every copy carries a real worktree-vs-HEAD diff, while the files and index stay unchanged (a second commit undone with `reset --soft`). So `--git` and `review` had changed seeds: for example, 103 on `pytest`, where `review` must succeed.

  `cargo fmt` later reflowed whitespace only. The rebuilt, shipped source (`6a267e01…`, the build `mise run verify` produced) was re-run against the same base: again 0 / 1,056 ([final-build parity](raw/final-build-parity/parity_summary.json)).

  An earlier lean build (`105702c5…`, same source) had also matched the archived #14 baseline (`0ffc1dd3…`) on the same matrix ([superseded](raw/superseded/)).
- **Fixture eval.** `oxide eval --config fixtures/benchmark.json` gives the same rows on all four binaries (hybrid R@5 0.909, vector R@5 0.818). Row order varies between runs of any binary, so the rows were compared sorted.
- **Committed tests** in `src/retrieval.rs`:
  - `lean_snapshot_output_matches_the_complete_corpus_oracle`: in-process frozen oracle, serialized JSON, both fixtures. It covers search in Balanced and Quality, the MCP cached shape, context with default/`--git`/`--blast-radius`/both, `review`, and the BM25 fallback, and asserts that queries actually expand and reach `related_tests`.
  - `completed_lean_corpus_equals_the_full_load`
  - `neighbors_refuses_a_partial_seed`
  - `a_partial_symbol_never_serializes`: checks `skip_serializing_if` under `flatten` empirically.
  - `search_hydrates_only_candidates_unless_expansion_needs_the_corpus`: one corpus load, and at most one completion read of at most `limit` ids.

  `tests/query_plans.rs` pins that the lean statement plans exactly like the full one, with and without `ANALYZE`.
- **Indexing (G5).** `src/index.rs` is untouched. Fresh indexes of `py_repo`/`ts_repo` built by each binary have identical sorted row dumps (1,684 / 1,030 rows), excluding the random `index_id` and the root path. A no-change reindex gives an equal result.
- **Codex review.**
  - The first pass found no BLOCKER or MAJOR issue in the source.
  - The second pass covered the harness and found five MAJOR measurement-instrument issues, all fixed before the final sweep:
    - a broken `Paths.__init__`;
    - `report` pooling both binaries;
    - a half-specified challenger silently timing the baseline;
    - `review` parity accepting two identical failures;
    - git surfaces timing a clean tree.

## 3. End-to-end results

**Method.**
- The #10/#14 harness, `scripts/corpus_load_baseline.py`, gained `--challenger-oxide/--challenger-profile`. Baseline and challenger are interleaved in every batch, alternating which runs first.
- `--git-surfaces` refuses a tree without a diff.
- One process per sample, pinned with `taskset -c 6`, `OXIDE_EMBED_SESSIONS=1`, hashed embedder. A/B 5 + 5; the `--no-expand` control 10 + 10.
- The null is |base B − base A| / base A. `requests` is excluded, as in PG-2.
- The page cache is warm. Load average was about 3 from a desktop browser, so absolute times are higher than #10's, but base and challenger saw the same machine.

The timed lean binary is `2fa67683…`. The shipped source differs only by a later `cargo fmt` whitespace reflow; its build `6a267e01…` was parity-checked (§2) and was not re-timed.

Raw data: [stages](raw/stages.jsonl), [cli](raw/cli.jsonl), [mcp](raw/mcp.jsonl), [control](raw/control-10x10/), [manifest](raw/manifest.json). Ms are batch A / B medians.

**Uninstrumented CLI, one-shot** (includes every completion read and hydration):

| corpus | request | base | lean | change | null |
| --- | --- | ---: | ---: | ---: | ---: |
| pytest | `search` (expanded) | 51.6 / 52.9 | 38.9 / 38.7 | −24.5 / −26.9 % | 2.6 % |
| pytest | `query` (context) | 59.5 / 60.2 | 45.5 / 46.1 | −23.5 / −23.3 % | 1.2 % |
| pytest | `query --git` | 72.9 / 72.4 | 58.8 / 60.7 | −19.3 / −16.1 % | 0.6 % |
| pytest | `review` | 85.8 / 87.7 | 71.0 / 73.1 | −17.3 / −16.6 % | 2.2 % |
| pytest | `search --no-expand` | 12.3 / 12.8 | 12.3 / 12.6 | +0.7 / −1.6 % | 4.2 % |
| pylint | `search` (expanded) | 74.4 / 78.8 | 56.5 / 60.1 | −24.0 / −23.8 % | 6.0 % |
| pylint | `query` (context) | 82.4 / 83.3 | 65.5 / 66.5 | −20.5 / −20.2 % | 1.2 % |
| pylint | `query --git` | 96.6 / 98.8 | 81.6 / 82.0 | −15.5 / −17.0 % | 2.3 % |
| pylint | `review` | 93.5 / 94.6 | 75.5 / 76.9 | −19.2 / −18.7 % | 1.2 % |
| pylint | `search --no-expand` | 17.7 / 18.5 | 17.9 / 18.1 | +0.6 / −1.9 % | 4.1 % |

**Peak RSS, CLI.** Expanded search, context, `--git` and `review` drop from 35.6–37.7 MB to 25.0–27.6 MB, a 26–30 % reduction. `--no-expand` is identical.

**`oxide mcp`:**

| corpus | tool | first call (cache miss) | steady calls 1..16 | server RSS |
| --- | --- | --- | --- | --- |
| pytest | search | 51.4 / 50.5 → 39.9 / 38.6 (−22.5 / −23.5 %) | 9.17 / 8.72 → 9.06 / 7.78 | 49.5 → 37.0 MB (−25 %) |
| pytest | query | 50.8 / 50.9 → 39.1 / 39.1 (−23.0 / −23.1 %) | 8.21 / 8.08 → 8.12 / 7.28 | |
| pylint | search | 72.2 / 72.7 → 58.9 / 58.9 (−18.4 / −19.0 %) | 14.13 / 13.94 → 13.78 / 13.47 | 49.4 → 37.9 MB (−23 %) |
| pylint | query | 71.8 / 73.2 → 57.5 / 58.7 (−19.9 / −19.7 %) | 13.76 / 13.79 → 13.11 / 13.66 | |

No steady MCP row is slower. Smaller clones out of the lean cache offset the per-request completion read.

**Instrumented stages** (`retrieval_profile`, warm, in-process):
- `snapshot_load`: −21 / −22 % on pytest, −20 / −19 % on pylint.
- `search_expand`: −21 / −23 % and −17 / −19 %.
- `context` (cold engine): −19 / −18 % and −16 / −18 %.
- `context_cached`: −4.5 / −6.5 % and −2.6 / −2.4 %. The pylint row sits inside its 3.4 % null.
- Rep-0 allocations for `snapshot_load` fall by 62–66 % (310k → 117k on pytest), and by 57–66 % for expanded requests.
- Stage VmHWM falls by 25–32 %.

The isolated load saving (~8–15 ms) accounts for most of the end-to-end drop. The CLI rows above are the figures that include every added read.

## 4. PG-3 gates

| Gate | Result |
| --- | --- |
| G1 correctness | **Pass.** §2: 0 / 1,056 parity diffs incl. `--git`, `--blast-radius`, `review`, MCP and the BM25 fallback; oracle test; eval unchanged; query plans pinned; `mise run verify`. |
| G2 one-shot | **Pass.** `search_expand` and cold-engine `context` improve 16–23 % on both corpora and both batches (≥ 5 %, > 2× null). The CLI expanded search improves 24–27 %. |
| G3 controls | **Warm MCP: pass**; no row is slower. **`--no-expand`: not met as literally written**, and an A/A control of identical binaries fails it too (4 of 6 rows). Overridden; see below. |
| G4 memory | **Pass.** VmHWM −25–32 % (stages), CLI RSS −26–30 %, MCP RSS −23–25 %. |
| G5 storage | **Pass.** No schema or index change; identical index rows; `src/index.rs` untouched. |
| G6 maintenance | **Pass, as a written judgment.** See below. |

**G3, the `--no-expand` control.** The clause asks for ≤ 1× null. At 10 + 10 ([control](raw/control-10x10/)), 3 of 6 control rows exceed it:
- pytest warm stage: −2.0 / +0.8 % against a 0.6 % null;
- pytest cold stage: −0.5 / −0.5 % against 0.3 %;
- pytest CLI: +0.9 / −0.4 % against 0.7 %.

The pylint rows are within their null. The moves are under 2 %, and they go in both directions within one row. Allocation counts (16,609 = 16,609) and peak RSS are identical. The path executes no new work: `--no-expand` never loads a snapshot, and `complete_symbols` returns without a read when nothing is partial, which the counting-store test pins.

**A/A control.** The same baseline binary was run as both variants, 10 + 10, in the same window ([control-aa](raw/control-aa/)). It exceeds the same clause on 4 of 6 rows:
- pylint warm: +1.8 / +0.1 % against a 0.3 % null;
- pytest warm: +0.3 / +1.6 % against 0.4 %;
- pylint cold: +0.4 / +0.9 % against 0.2 %;
- pytest cold: +1.0 / −0.6 % against 0.0 %.

The CLI rows stay within their nulls. With identical code, sub-2 % moves beyond a single-median null are the harness's resolution, not an effect. So the lean control rows (3 of 6, −2.0 % to +0.9 %) are indistinguishable from A/A.

The clause as written is still not met. The production-ready verdict is an explicit override of PG-3's letter, on this evidence and on the user's criterion: no unacceptable regression.

**G6.** One-shot expanded requests get 16–27 % faster across search, context, `--git` and `review`. The first MCP call is 18–24 % faster, and peak memory is 23–32 % lower, with no steady-MCP or non-expanded regression.

That benefit outweighs the cost:
- one new `Symbol` field;
- about 360 lines of non-test code, much of it comments;
- a completion invariant at five call sites.

The invariant is enforced loudly by a serialization error, two asserts, a read-budget test and an oracle parity test. `RelationGraph` stays a single implementation, there is no new configuration, and the change reverts cleanly.

## 5. Reproduce

```bash
# binaries: base from a detached worktree at 6413856, lean from this tree
B=/tmp/oxide-lean-bench/bin
ARGS="--work /tmp/oxide-lean-parity --oxide $B/oxide-base --profile $B/profile-base \
      --challenger-oxide $B/oxide-lean --challenger-profile $B/profile-lean"
O=docs/retrieval-profile/corpus-load-baseline/lean-snapshot/raw
python3 scripts/corpus_load_baseline.py parity --work /tmp/oxide-lean-parity --out $O \
  --oxide $B/oxide-lean --baseline-bin $B/oxide-base \
  --corpora requests,pytest,pylint,py_repo,ts_repo,pytest-nolex,pylint-nolex,py_repo-nolex
taskset -c 6 python3 scripts/corpus_load_baseline.py stages $ARGS --out $O --corpora pytest,pylint \
  --stages snapshot_load,search_noexpand,search_expand,context,context_cached --reps 5 --in-process 3
taskset -c 6 python3 scripts/corpus_load_baseline.py cli $ARGS --out $O --corpora pytest,pylint --reps 5 --git-surfaces --skip-native
taskset -c 6 python3 scripts/corpus_load_baseline.py mcp $ARGS --out $O --corpora pytest,pylint --reps 5
```

The parity copies are the #14 indexed corpora under `/tmp/oxide-lean-parity/idx/`. Each copy has:
- `meta.root` pointed at the copy;
- a synthetic staged diff: three files truncated, committed, restored and committed again, then `git reset --soft HEAD~1`;
- for the `-nolex` copies, `meta.lexical_index_version` deleted.

The copies are disposable, and neither the archive nor any user index was written. A first sweep taken before the harness review fixes is kept in [`raw/superseded/`](raw/superseded/). It showed the same effects and is not interpreted.
