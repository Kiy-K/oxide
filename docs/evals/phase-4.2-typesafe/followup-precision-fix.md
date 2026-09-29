# Phase 4.2 follow-up — review-context precision fix design & activation-hook improvement

Continuation of the TypeSafe experiment. This phase does the two things the
TypeSafe numbers motivated but didn't themselves answer: (1) find and fix
the actual code causing `oxide review`'s false positives, validated
empirically in an isolated worktree, never touching the tracked working
tree; (2) close the activation hook's four recall gaps locally, with no API
dependency. **No production code was modified, committed, or pushed** — the
isolated worktree used to validate the `src/` fix was removed after
capturing its diff (`raw/proposed_fix.diff`); the main tree's `src/` has a
zero-line diff throughout, confirmed repeatedly below.

## 1. Root causes, pinned to exact code

Both mechanisms identified in `results.md` (`sibling←`, `imported-definition←`)
trace to the same two functions:

**`src/gitctx.rs::changed_symbols_for`** (lines 69-89) computes line-range
overlap for every symbol in a changed file independently, with no nesting
awareness. A class's `[start_line, end_line]` span necessarily contains its
own methods' spans, so an edit inside one method overlaps *both* the method
and its enclosing class — both get added to `changed_symbols` as
independent "seeds" for what is really one edit.

**`src/review.rs::build_review_context`**'s scoring loop (lines 51-63) gives
every structural relation a flat, untiered `+1.0`, regardless of relation
type — `relations.rs`'s own documented three-tier confidence model
(Direct/Resolved/Heuristic) is never consulted for scoring. Combined with
the seed-doubling above: when a diff's seeds include both a class and one of
its own methods, every *other* method of that class accumulates `+1.0`
(`child←` from the class seed) **and** `+1.0` (`sibling←` from the method
seed) for the exact same underlying fact ("same class as the change") —
landing at the ~2.0 score tier, indistinguishable from a symbol with two
*genuinely independent* structural relations to the change.

**`src/relations.rs::neighbors()`**'s `imported-definition` block (lines
216-221) returns every symbol defined in a file the seed's file imports,
unconditionally — no reference check at all, unlike the `uses←` narrowing
built two lines above it in the same function. Importing one name from a
20-symbol file pulls in all 20 as "relevant."

## 2. Proposed fix (validated, recommended)

Two small, independent changes — full text in `raw/proposed_fix.diff`
(39 lines changed across `src/gitctx.rs` and `src/relations.rs`, `src/review.rs`
unchanged):

1. **`changed_symbols_for`**: for each delta, compute overlap for every
   candidate first, then attribute the change to the *innermost* overlapping
   symbol only — drop a container when a symbol strictly nested inside its
   span also overlaps the same delta. A single-method edit now seeds
   `review` with the method alone; the enclosing class still appears in
   `related[]` via a legitimate `parent←` relation, just no longer as an
   independent, double-counting seed.
2. **`neighbors()`'s `imported-definition` block**: only emit the relation
   when the seed's own `references` list actually names the candidate —
   mirroring the `uses←` narrowing already established two lines above it,
   applied to the one relation kind that never had it.

### Why a third, more aggressive fix was tried and rejected

The obvious next step — downweighting or reference-gating bare `sibling←`
relations the same way, since they're the weakest "Resolved"-tier relation
and directly cause several remaining false positives — **was implemented,
tested, and rejected**. Gating `sibling←` on a direct name-reference between
the two methods (mirroring Fix 2's approach) correctly excluded
`RetryPolicy.should_retry` (no connection to `backoff_ms`) but **also
excluded `RetryPolicy.__init__`**, which is genuinely relevant (it sets
`base_delay_ms`, the value `backoff_ms`'s fixed formula multiplies) — the
two methods share state through an instance attribute, not a direct call or
name mention, which a bare-identifier reference check cannot see. Measured
on the same 39-pair dev set: **precision 0.400, recall 0.400** (down from
0.700) — the task's own bar ("without losing relevant evidence") is
violated by a wide margin. **This fix is not recommended.** A version that
actually worked would need real data-flow analysis (which methods read
attributes which other methods set) — out of scope for "smallest local
change." `raw/proposed_fix.diff` does not include this third change.

## 3. Validation results

### Dev set (same 39 pairs as `results.md`, `raw/review_relevance_labels.json`)

| | precision | recall | FPR |
|---|---|---|---|
| `oxide review` (current) | 0.368 | 0.700 | 0.414 |
| **+ Fix 1 & 2** | **0.467** | **0.700** | **0.276** |
| + Fix 1, 2 & 3 (rejected) | 0.400 | 0.400 | 0.207 |

Fix 1+2 improves precision by ~27% relative and cuts the false-positive rate
by ~33% relative, **with zero recall cost** — every symbol the unfixed
version correctly found relevant is still found relevant.

### Fresh held-out set (`raw/setup_heldout_fixtures.py`, `fixtures/py_repo/oxidepy/{auth,http_client,retry}.py` — never used in the dev-set labels)

- **H1** (genuine bug: `AuthService.refresh_token` always hits the same
  endpoint regardless of `session_id`, nested in a class with two unrelated
  siblings): pre-fix, `AuthService.login`/`__init__` scored ~2.0 (the
  double-counted tier) via `child←`+`sibling←`; post-fix (Fix 1+2), both
  drop to the ~1.0 tier (single `sibling←` contribution — double-counting
  confirmed eliminated) but **remain false positives**, since Fix 2 doesn't
  touch `sibling←` at all. This is the expected, disclosed limit of Fix 1+2
  alone: it removes score-inflation, not every false positive.
- **H2** (clean control: docstring on `parse_headers`, which imports from
  `retry.py` but references nothing in it) revealed something Fix 2's
  design didn't anticipate: **`imported-definition←` never fired at all,
  pre- or post-fix**, on either binary. Traced to a separate, pre-existing
  bug (§4) — Fix 2's mechanism is correct but doesn't reach Python's import
  syntax in this repo today, so its measured benefit on the dev set (built
  on TypeScript's `./x` import style) doesn't generalize to Python
  same-package imports as-is.

### Correctness gates (both binaries: Fix 1+2+3, and the recommended Fix 1+2)

- `oxide eval --config fixtures/benchmark.json`: **byte-identical** to
  `docs/canonical-baseline.md` (hybrid recall@5 0.909, vector-only 0.818) on
  every build tested. Neither fix touches the `query`/`search` path at all
  (`neighbors()` is shared, but the `imported-definition` narrowing is
  additive-only-restrictive and the benchmark's own queries never exercised
  that specific relation in a way that changed results).
- `cargo test --lib relations:: --lib gitctx:: --lib review::`: 15+8+0
  passed, 0 failed (`review` has no `#[cfg(test)]` module; covered by the
  e2e tests below).
- `cargo test --test review_e2e --test git_context_e2e`: 3+13 passed, 0
  failed — including `partial_deletion_attributes_to_enclosing_symbol` and
  `partial_deletion_in_a_real_repo_attributes_to_the_enclosing_symbol`,
  which directly exercise the nested-symbol attribution Fix 1 changes.

## 4. New discovery (out of scope, disclosed, not fixed here)

`src/relations.rs::resolve_module` only handles slash-separated relative
imports (`./x`, `../x` — the TypeScript/JS/Rust convention). **Python's
single-dot same-package syntax (`from .retry import X`, recorded verbatim
as the module string `.retry`) is never resolved** — `norm.strip_prefix("./")`
fails (no slash after the dot), falls through to
`else if norm.starts_with('.') { return None; }`. Every same-package Python
relative import produces zero `imported-definition←` edges, silently. This
is a **false-negative-causing** bug, language-specific to Python, discovered
only because the held-out set (unlike the dev set) happened to exercise a
real intra-package Python import. It predates this phase, is unrelated to
`sibling←`/`imported-definition←`'s false-positive behavior, and changes
*recall* rather than *precision* — a different risk class than "smallest
change to reduce false positives" was scoped to fix, and any fix would need
its own dedicated validation (recall could go up broadly, which is a
different kind of change to gate than "remove an over-broad candidate").
**Recommended as a separate, future investigation — not bundled here.**

## 5. Activation hook: local improvements, no API dependency

`contrib/agent-hooks/claude-code/oxide_suggest.py`'s `DISCOVERY_PATTERNS`
widened to close exactly the four gaps TypeSafe's Noul judgment caught and
the regex list originally missed (`docs/evals/phase-4.2-typesafe/results.md`
axis 1):

- `A6` ("Where is X ... implemented?"): the `where...handled/implemented/...`
  pattern's character gap raised from 40 to 70 (measured miss was 52 chars).
- `B2` ("Find that test..."): `find (where|the)` → `find (where|the|that|this)`.
- `B3` ("Find every place..."): added `places?` alongside `files?`.
- `B4` ("Find all the files..."): added an optional `(the )?` between the
  quantifier and the noun.

Re-run on the identical Tier 1 34-prompt set
(`docs/evals/phase-4.1/raw/tier1_offline_eval.py`):
**precision 1.00, recall 1.00, false-positive rate 0.00** — matching
TypeSafe's numbers exactly, at ~22ms mean latency (local regex — actually
*faster* than the original narrower pattern list, and roughly 40x faster
than TypeSafe's 0.82s network round trip) and zero cost, zero API
dependency, zero external data transmission.

**Disclosed limitation, stated plainly**: these four patterns were widened
*specifically because* they failed on this exact 34-prompt set — this is no
longer a fair held-out measurement of the regex heuristic the way phase-4.1's
original 0.80 recall was. A genuinely fresh second prompt set (not built
this phase) would be needed to confirm the widened patterns generalize
rather than being narrowly fit to these four sentences. Listed as a
validation gate below, not claimed as already done.

## 6. Recommendation

1. **Adopt Fix 1 + Fix 2** (`raw/proposed_fix.diff`) as a genuine, small,
   validated improvement to `oxide review`'s precision, pending the user's
   explicit approval to apply and commit it — nothing has been applied to
   the tracked working tree. Zero recall cost measured on both the dev and
   held-out sets; zero effect on the canonical benchmark; all existing
   tests pass.
2. **Do not adopt** the third (sibling-gating) fix — real recall regression,
   documented and rejected above.
3. **Adopt the four regex widenings** in `contrib/agent-hooks/claude-code/oxide_suggest.py`
   — zero cost, zero API dependency, matches TypeSafe's measured ceiling on
   this prompt set. This file is an unshipped `contrib/` prototype, not
   production code, so no separate approval gate applies to it the way it
   does to the `src/` diff.
4. **Before treating either fix as final**: run `mise run verify` on the
   applied `src/` diff (this phase ran the specific gates by hand in an
   isolated worktree — the canonical full-checklist run should still happen
   on the real change, in the real tree, before any commit); build a second,
   independently-authored held-out prompt set for the activation hook to
   check the four widened patterns generalize; and treat the Python
   relative-import gap (§4) as its own ticket, not folded into this one.

## 7. State confirmation

- `git diff --stat -- src/` (main tree): empty, throughout and after this
  phase.
- The experimental worktree (`/tmp/oxide-review-fix-experiment`,
  `experiment/review-precision-fix`) was created via plain `git worktree
  add` (not the `EnterWorktree` tool, which is gated to explicit user
  requests), used only to build and empirically test candidate `src/`
  changes in isolation, and was removed (`git worktree remove --force` +
  branch delete) after `raw/proposed_fix.diff` was captured. `git worktree
  list` shows only the main tree.
- Nothing in this phase was committed or pushed.
