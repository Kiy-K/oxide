# Phase 4.2 recommendation — TypeSafe AI integration

## 1. Does TypeSafe beat the existing workflow?

**Yes, on both axes, on this phase's evidence** — and by different margins,
for different reasons, confirming the task's own instinct not to assume the
two false-positive types share a cause:

- **Activation (axis 1)**: TypeSafe closes phase-4.1's entire recall gap
  (0.80 → 1.00) while holding perfect precision and zero false positives,
  at negligible cost (~$0.0006 for the full 34-prompt set).
- **Code-review relevance (axis 2)**: TypeSafe nearly doubles `oxide
  review`'s precision (0.37 → 0.70) and quarters its false-positive rate
  (0.41 → 0.10) at matched recall (0.70 both), by being sensitive to
  whether a diff is actually cosmetic — something `oxide review`'s
  structural-proximity reasons (`sibling←`, `imported-definition←`) cannot
  express by construction.

Neither win is free, and the costs differ by axis too — axis 1's is
latency (~20x slower per call, still well inside timeout budgets); axis 2
has no comparable existing latency to beat (`oxide review` is local/instant)
so TypeSafe's ~0.8s per candidate is a straightforward added cost, not a
trade against an existing slow path.

## 2. Should TypeSafe go into OXIDE's production path?

**No — and the task's own framing already rules this out ("keep TypeSafe
optional and outside OXIDE's production retrieval path"), which this
phase's evidence independently supports, not just complies with by
instruction:**

- **A private, per-user API key in the runtime path is a hard no for a
  local-first tool.** OXIDE's entire positioning (`docs/agent-usage-policy.md`,
  phase-4.1's "no LSP, daemon, or remote dependency") is a local index with
  no external service dependency. Wiring TypeSafe into `query`/`search`/
  `review` would make every OXIDE call depend on a third-party API key,
  network reachability, and a per-account rate limit ("adjusting
  dynamically" per TypeSafe's own docs) — a categorically different
  reliability and privacy posture than shipping today.
- **Axis 2's caveat is real**: TypeSafe's measured numbers used less
  information per candidate than OXIDE's own index already has available.
  A fair "should this replace `oxide review`'s heuristic" comparison needs
  a version of TypeSafe given the same call-graph/body information OXIDE
  already computes — not measured this phase, and likely to change the
  numbers (probably favorably for TypeSafe, but unverified).
- **n=34 / n=39 is enough to see a real, consistent direction, not enough to
  finalize a threshold.** Both axes' `>=0.5` cutoff was the docs' own
  suggested default, never tuned against this data — a real integration
  would calibrate it, and TypeSafe's own docs say as much ("thresholds
  evaluated on the user's data and consequences").

## 3. What this phase recommends concretely

1. **Do not integrate TypeSafe into OXIDE's production CLI/MCP path.**
   Consistent with the task's instruction and with the local-first,
   no-remote-dependency design this whole roadmap thread (#9, phase-4.1) has
   built toward.
2. **Keep both TypeSafe scripts as optional, standalone experiments** under
   `docs/evals/phase-4.2-typesafe/raw/` — reusable if someone wants to
   re-run this comparison against a future OXIDE heuristic change, but never
   imported by any `src/` file (verified: zero `src/` diff, and nothing in
   `raw/` is referenced from outside this directory).
3. **If TypeSafe is ever considered for a genuinely optional, opt-in
   secondary-opinion feature** (e.g., "double-check this review pack before
   trusting it," explicitly user-triggered, never automatic) — re-run axis 2
   with richer `state` (candidate body + known callers) first; the current
   numbers are a floor, not a ceiling, and the gap could close or widen.
4. **`oxide review`'s two concrete, sourced false-positive mechanisms are
   independently worth fixing without TypeSafe at all**: `sibling←` firing
   on completely unrelated sibling methods for a cosmetic diff, and
   `imported-definition←` treating an entire imported file's surface as
   relevant to a diff that touches none of it. Both are cheap, local,
   `src/`-only fixes (tightening when those two reason-kinds should fire) that
   this phase's data motivates but does not implement, per "do not modify
   OXIDE production code... without explicit approval."

## 4. What this phase explicitly did not do

- No `src/` file touched (`git diff --stat -- src/` empty).
- No commit, no push.
- No OXIDE production code sent to TypeSafe, ever — only phase-4.1's own
  synthetic eval prompts and the two pre-existing `eval-agent/tasks/*`
  fixtures.
- No attempt to reverse-engineer the unrelated third-party GateGuard plugin
  — the "code-review false positives" axis was scoped, with the user, to
  OXIDE's own `oxide review` surface instead.
- No threshold tuning, no richer-`state` re-run, no production integration
  design — all listed above as follow-up, not done here.
