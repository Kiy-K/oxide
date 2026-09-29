# Phase 4.1 recommendation — hook-based agent activation (roadmap #9)

## 1. Is the prototype hook itself sound?

**Yes, on Tier 1's evidence.** Precision 1.00, false-positive rate 0.00
across 14 held-out Bucket-C prompts (including adversarial ones), recall
0.80 on Bucket-A/B with four honestly-disclosed, fixable regex-coverage
gaps, 0/34 false-fires with no local index present, ~40ms overhead, 0
tokens injected on no-fire, ~70 on fire. It never blocks a prompt, never
touches a tool call, and restates rather than forks the existing policy
document. This is the part of the roadmap item that is fully answered by
this phase's evidence.

## 2. Does hook-based activation beat skill-only activation?

**Not decidable from this phase's evidence, and that is itself the honest
finding, not a dodge.** Tier 2's n=6-attempted sample (2 completed cleanly
per condition at best) is too thin and too confounded by this machine's own
ambient `~/.claude` configuration (`results.md`'s disclosed confound: token
counts inflated 4-25x by unrelated personal skills/plugins loading on every
run) to support a directional verdict the way phase-3.1's n=12 Bucket-A
comparison could. What Tier 2 *did* surface, reliably, is not "hook wins" or
"skill wins" but a causal mechanism worth carrying forward: **a longer,
more complete activation surface (the skill's documented "Index behavior"
section) has a larger action space than a short hook-injected suggestion,
and in this sample that larger surface is what invited a reactive
`oxide index` call that hit a real, documented network dependency
(unhashed-embedder ONNX download) the hook's terser text never triggered.**
That is a genuine, sourced, causally-traced result — just not the
"hook vs. skill, which is faster/cheaper/better" result the roadmap item
asked to measure, because the sample that would answer that question
honestly costs more paid, isolated runs than this phase's approved budget
covered.

## 3. Pareto verdict

Plot the two axes the roadmap item cares about — **activation quality**
(precision/recall/false-positive rate) and **cost to ship** (lines of new
code, blast radius, reversibility):

| | Tier-1-measured activation quality | Cost to ship |
|---|---|---|
| **Hook (this phase's prototype)** | precision 1.00, recall 0.80, FPR 0.00, ~40ms, 0/~70 tokens | ~150 lines, zero `src/` diff, opt-in per-user file, one-time folder-trust dialog |
| **Skill-only (existing, shipped since phase 2.2)** | Not re-measured this phase (phase-2.3's own numbers stand) | Already shipped, already tuned across 3 prior phases |

The hook **dominates on activation-quality evidence gathered this phase**
(a deterministic, reproducible, zero-cost measurement) **at a cost that
never touches the frozen `src/` surface** — it is strictly additive,
reversible by deleting one file, and requires no product-code review. It
does **not** dominate on "is it worth shipping instead of, or alongside, the
skill" — that question needs the Tier-2-shaped evidence this phase
explicitly chose not to fully fund (per the user's own scope decision), so
it stays open.

**Verdict: keep the prototype as an opt-in `contrib/` artifact, not a
shipped default, pending a properly isolated Tier-2 run.** This is not
"reject the hook" — Tier 1's numbers are good enough that the design itself
is not in question — it is "the evidence bar for changing what OXIDE ships
by default has not been met yet," which is a different, higher bar than
"the prototype works," per this repo's own retrieval-scoring discipline
(`CLAUDE.md`: "any diff in the results is a regression to explain") applied
by analogy to activation-surface changes.

## 4. What would close the gap

1. **Fix the harness environment-parity bug before any further Tier-2
   run**: propagate `OXIDE_EMBED_NATIVE=hashed` into the *agent's* own
   environment (`tier2_agent_run.py`'s `run_task`), not only the setup-time
   indexing step. Tier 2's two unexplained timeouts are very likely this bug,
   not a hook-vs-skill property, and re-running without fixing it first
   would re-spend money on the same artifact.
2. **Re-run Tier 2 with `--bare` and a dedicated `ANTHROPIC_API_KEY`**, not
   this machine's subscription login, once that key is available — the
   disclosed ambient-config confound (4-25x token inflation) makes every
   absolute Tier-2 number in `results.md` untrustworthy in isolation; only
   the fact pattern (which cell succeeded, which mechanism was invoked) is.
3. **Widen Tier 1's regex coverage** for the four disclosed misses
   (`A6`, `B2`, `B3`, `B4`) — cheap, zero-cost-to-verify changes, unlike
   Tier 2.
4. **Extend Codex and OpenCode from sourced feasibility to prototypes**
   only after the Claude Code hook clears a real Tier-2 bar — both are
   confirmed mechanically feasible (`codex-feasibility.md`,
   `opencode-feasibility.md`), so this is a sequencing choice, not a
   blocker.

## 5. What this phase explicitly did not do

- No `src/` file was touched. `mise run verify` is byte-identical to the
  frozen baseline (`results.md`).
- No release, no `oxide install` wiring, no default-on behavior change.
- No Greptile review — nothing has been committed yet for it to review
  (`results.md` §Greptile review).
- No large real-agent matrix — the user was asked and explicitly scoped
  Tier 2 to a small, paid sample before any money was spent past the
  initial $0.21 smoke test.
