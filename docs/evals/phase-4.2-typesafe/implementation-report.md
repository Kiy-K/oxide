# Phase 4.2 implementation report — review-relevance fix, shipped to local commits

Implements the two fixes validated in `followup-precision-fix.md`
(`raw/proposed_fix.diff`), then hardens them through four rounds of real
Greptile review against the actual applied code. **Four local commits,
none pushed**, on `main`, ahead of `origin/main` by 6 (4 from this task +
2 pre-existing, unrelated docs/logo commits already on the branch before
this task started).

## Commits (local only)

1. `90f7d54` — the two validated fixes from `proposed_fix.diff`: innermost-symbol
   seed attribution (`gitctx.rs::changed_symbols_for`) and reference-gated
   `imported-definition` (`relations.rs::neighbors`), plus the 5 regression
   tests from `followup-precision-fix.md`.
2. `69f3bf5` — Greptile finding #1 fix: innermost-attribution was decided
   per **file**, not per **hunk** — a class-level hunk got wrongly discarded
   whenever a different hunk in the same delta touched a nested method.
3. `03aba52` — Greptile finding #2 fix: the dedup index was keyed on
   `qualified_name` alone, which is only unique within one file (this
   repo's own pinned symbol-id invariant is `file + qualified_name`) — two
   different files' same-named symbols could collide and one would be
   silently dropped.
4. `3c9721d` — Greptile finding #3 fix: even per-hunk, a *single* added-line
   range can itself span both a container's own body and a nested symbol —
   "any nested hit discards the container" was too blunt; now a container
   is only discarded when nested hits' overlapping ranges *fully cover* its
   own overlap with that hunk.

Each fix followed the same cycle: read Greptile's specific claim, verify it
against the actual code (not taken on faith), write a regression test that
fails against the prior commit's code, confirm the failure, fix, confirm
the test now passes, run the full targeted test suite, commit, re-run
Greptile. **Round 4 returned zero new findings** on `src/gitctx.rs` /
`src/relations.rs`: *"No new actionable defects were established... fix the
cross-file collision and mixed-hunk findings."*

## What Greptile flagged and left alone, deliberately

- **Aliased imports** (`import { Foo as Bar }`) still don't resolve through
  the new `imported-definition` gate. Verified via a new test
  (`an_aliased_import_is_a_known_gap_shared_with_uses_not_new_here`) that
  this is a **pre-existing** limitation of the whole bare-name-matching
  scheme (`uses←` has never resolved aliases either — no `Symbol` field
  records an alias mapping) — not a regression this change introduces.
  Documented, not fixed; fixing it needs alias-tracking infrastructure this
  change doesn't touch.
- **`docs/agents/issue-tracker.md`'s two findings** (issue-read command
  lacks structured output; frontier procedure lacks dependency/order data)
  and several other findings Greptile listed as still-outstanding (watcher
  metadata, unbounded-read, decode-error) belong to **other, unrelated
  commits** already on this branch before this task started (`ac8231e`,
  and earlier `5726999`/`4626a4b`/`155756c`) — out of scope for "smallest
  correct changes" to review-context precision, left untouched.

## Final measurements (shipped binary, `git log -1` = `3c9721d`)

### Dev set (39 manually labeled pairs, `raw/review_relevance_labels.json`)

| | precision | recall | FPR |
|---|---|---|---|
| `oxide review` (baseline, unfixed) | 0.368 | 0.700 | 0.414 |
| **Shipped (all 4 commits)** | **0.467** | **0.700** | **0.276** |

Identical to every intermediate measurement in `followup-precision-fix.md` --
none of the four Greptile-driven corrections changed the dev-set's numbers,
because none of the 39 pairs' diffs exercise the multi-hunk or
cross-file-collision edge cases those corrections fix. This is expected,
not a coincidence: those corrections widen *correctness* (real bugs a
richer test corpus would eventually hit), not the measured precision win
itself.

### Held-out set (`fixtures/py_repo/oxidepy/{auth,http_client}.py`, independent of the dev set)

Re-verified unchanged against the shipped binary -- `AuthService.login`/
`__init__` still score ~1.0 (Fix 1 removed the double-counted ~2.0 tier;
Fix 2 doesn't touch bare `sibling←`, so these remain false positives, as
already disclosed); `imported-definition` still never fires for
`parse_headers`' Python-style relative import (§ Python relative-import
resolution, below -- untouched, as instructed).

### Context tokens and latency (`raw/heldout_output_{prefix,postfix}.json`,
5 runs each, `target/release/oxide`)

| | H1 latency | H1 tokens | H2 latency | H2 tokens |
|---|---|---|---|---|
| Baseline | 16.8ms | ~2741 | 16.4ms | ~2340 |
| Fixed | 13.1ms | ~2741 | 12.9ms | ~2340 |

**Context payload size is unchanged.** `related.truncate(15)` and both
runs stay under that cap with the same item count either way -- OXIDE's
always-on semantic top-up (`VectorOnly` search, unaffected by either fix)
already surfaces the same candidate set regardless of whether it also
carries a strong structural reason. The fix changes **which items are
trustworthy** (score), not **how much is transmitted**. Latency is
marginally *lower*, not higher (fewer relation edges to compute in the
common case); the ~4ms difference is within normal process-noise range for
a ~13-17ms call, not claimed as a meaningful speedup.

### Correctness gates

- `mise run verify`: full pass on the final commit -- 967 tests (0 failed),
  fmt clean, clippy clean (default and `--no-default-features`), canonical
  benchmark byte-identical to `docs/canonical-baseline.md` (hybrid recall@5
  `0.909`, vector-only `0.818`), 64/64 installer checks.
- `determinism_stress.rs`'s 4 tests pass -- the `HashMap<u64, usize>` dedup
  index is used only for O(1) lookup, never iterated; output order still
  comes from the deterministic `Vec` push order.
- `review_e2e`/`git_context_e2e`: 16/16 pass, including
  `partial_deletion_attributes_to_enclosing_symbol` and
  `partial_deletion_in_a_real_repo_attributes_to_the_enclosing_symbol`,
  which directly exercise the attribution logic all four commits touch.
- CLI/MCP contracts: `ReviewContext`'s JSON shape is unchanged (same
  fields, same types) -- both fixes change *which* symbols populate
  `changed_symbols`/`related` and their scores, never the schema. `review`
  is not exposed over MCP at all (`docs/agent-surface.md`: HUMAN/ADMIN
  class), so the MCP contract was never in this change's reach.

## Final Pareto verdict

**Ship it.** Four commits, ~230 lines total (including regression tests),
zero `src/` files touched beyond `gitctx.rs`/`relations.rs`, zero schema
change, zero retrieval-benchmark change, zero context-payload-size change,
a genuine 27%-relative precision improvement and 33%-relative
false-positive-rate reduction at **zero recall cost** on the measurement
that carries the verdict (39 manually labeled pairs), independently
re-confirmed on held-out data, and hardened through four real rounds of
automated review against the actual committed diff -- not just the
originally-designed patch. The cost side of the ledger is small and
enumerable: ~230 lines of new/changed code, one disclosed pre-existing
limitation carried forward (import aliasing), and the deliberately-scoped
gap below.

**Remaining gaps, kept as separate follow-up work per instruction, not
touched this task:**

1. **Python relative-import resolution** (`resolve_module` doesn't handle
   `.module`-style single-dot imports) -- a distinct, false-*negative*-causing
   bug in a different function, discovered during held-out validation,
   explicitly carved out as its own follow-up experiment.
2. **Activation-hook regex generalization** -- the four pattern widenings in
   `contrib/agent-hooks/claude-code/oxide_suggest.py` were fit to the exact
   34-prompt set that motivated them; a genuinely fresh second prompt set
   to confirm they generalize is still open, explicitly kept separate per
   instruction.
3. **Aliased-import resolution** for `uses←`/`imported-definition←` (this
   task's own finding, see above) -- real, disclosed, not attempted here;
   needs alias-mapping data no `Symbol` field currently carries.

## What was preserved, as instructed

- `logo.png`: untouched (already committed in `d512db1`, before this task
  began).
- TypeSafe research (`docs/evals/phase-4.1/`, `docs/evals/phase-4.2-typesafe/`,
  `contrib/`): untouched, still untracked/uncommitted exactly as this task
  found them.
- Nothing pushed. `git status -sb`: `main...origin/main [ahead 6]` -- 4 new
  commits from this task, 2 pre-existing ones already ahead before it
  started.
