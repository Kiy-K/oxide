# TreeSelect v1 diagnosis (development set; not a verdict)

The 149 #42 tasks are used here **only as a development set**. Every number is measured on them,
in-sample, with #42's metrics and repo-clustered bootstrap, at matched tokens (A's `used_tokens`)
unless noted. Nothing here is gated.

- Code: `research-dev.patch` (#42's research patch plus dev knobs).
- Runner: `dev_run.py [round2]`. Scorer: `dev_score.py [round2]`.
- Tables: `diagnosis.txt` (round 1, 36 arms) and `diagnosis-round2.txt` (9 arms). Raw packs are in
  `~/.cache/oxide-treeselect-eval/dev/` (machine-local).
- The knob refactor reproduces #42's committed traces exactly for A, B, T=−x, T=, T=−c and T4096.

## Questions and answers

**1. Is v0's loss caused by candidate-count bias in NodeScore?** No.

| tree arm (flat, no collapse, 16 seeds) | Δ cov vs B | up/down | first-5 offers that are tests |
|---|---|---|---|
| `sqrt` Σ/√(N+1) (v0) | −0.0016 [−0.0048, +0.0001] | 1/6 | 21.7 % |
| `max`, no count reward | −0.0016 [−0.0048, +0.0001] | 1/6 | 21.7 % |
| `lin` Σ/(N+1) | −0.0018 | 1/6 | 21.9 % |
| `mean` Σ/N | −0.0314 [−0.0640, −0.0056] | 11/20 | 24.3 % |
| B (flat role-then-score) | 0 | — | 12.1 % |

- `max` has no count reward, yet it gives the same packs as `sqrt`.
- What every tree arm loses is **production's role order**: tests reach the first offers about twice as
  often. Shrink-to-fit then halves the windows of the primaries that come after them.

**2. Is the 16-seed pool too shallow for hierarchy?** No.

| depth | pool ceiling (gold coverage of all pooled spans) | B Δ cov vs B@16 | tree Δ cov vs B at the same depth |
|---|---|---|---|
| 16 | 0.583 | 0 | −0.0016 |
| 32 | 0.664 | −0.0045 (rel/used −6.1 %) | −0.0009 |
| 50 | 0.694 | −0.0051 (rel/used −7.9 %) | −0.0007 |

Deeper pools hold more gold, but at matched tokens both selectors pack it less efficiently. The
tree-vs-flat gap stays at or below 0.

**3. Do packing and snippet windows interact with the order?** Yes, but this is not a tree effect.

- Window loss, the gold inside a packed item's span cut off by its window, is 21 % of gold lines for
  B and 15 % for A.
- Two-pass fill (full windows first, then shrink-to-fit for the rest) lowers it to 20 % (16 seeds) and
  16 % (32 seeds).
- Coverage moves only noisily: B two-pass +0.0021 [−0.0171, +0.0280], 7/9; at 32 seeds +0.0089
  [−0.0206, +0.0421], 8/17, with rel/used −6.3 %.

**Round 2: role-first TreeSelect** keeps the role order and uses the tree only within a role.

| arm | Δ cov vs B | up/down | rel/used |
|---|---|---|---|
| role-first, flat (`sqrt` or `max`) | **+0.0000** | 0/0 | −0.2 % |
| role-first, v0 nesting + collapse | +0.0000 [0, +0.0002] | 1/0 | −0.7 % |
| role-first, `mean` | −0.0204 [−0.0509, +0.0007] | 10/15 | −3.6 % |
| role-first + two-pass | +0.0019 [−0.0173, +0.0277] | 6/9 | −1.7 % (≈ B two-pass) |

## Conclusion

At this seam (re-ordering an existing 16–50-seed shortlist), a post-retrieval tree has **no lever** on
the development set. This says nothing about routing through an index-time tree before the shortlist
exists (TreeIndex/TreeRouter, `../pageindex-notes.md`).

- At matched tokens a pack holds about 8 of the 12–14 post-floor candidates. Order only decides which
  few tail items are dropped and which windows shrink.
- Tree order that ignores roles hurts, because it promotes tests.
- Tree order within roles reproduces B exactly on 149/149 tasks.
- No score mode, pool depth or packing variant makes the tree beat B.
- Every remaining movement comes from **packing** (two-pass) or **cap removal** (#43), not from
  hierarchy.

Flat trees without nesting or collapse cost no latency (19 ms vs B's 20 ms), so v0's O1 failure is
fixable. There is just no quality gain to pay for.
