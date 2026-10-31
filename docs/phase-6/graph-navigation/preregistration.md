# Agent-facing graph navigation: preregistration

Frozen before any arm is scored (hash in `results/freeze.txt`). Question: does OXIDE's
existing graph evidence (`Repository.impact`, `Repository.relations`) answer multi-file
impact questions better than what an agent already has (grep, `oxide_search`
search/context), enough to justify an agent-facing representation? Retrieval, routing,
selection and packing are not touched.

## Priors

- Agents called OXIDE's search/context modes 0 times in 48 runs
  ([structural-mcp-trial](../structural-mcp-trial/README.md)) and the navigation tools 0
  times in 36 runs (agent-navigation-eval, git `9350b6e`). Grep answered a direct-callers
  task at F1 1.00 (K2).
- So an MCP graph tool must first carry information grep does not. Stage 1 measures that
  deterministically; Stage 2 (an agent trial) runs only if Stage 1 passes.

## Stage 1: information value (deterministic, no model)

**Corpora**: flask, requests, pytest checkouts of the structural trial
(`$OXIDE_TRIAL_DATA/repos`), indexed at the current derivation.

**Targets** (24): per corpus, 4 module-level functions and 4 methods, drawn in order
from a `random.Random(20261025)` shuffle of non-test symbols outside `tests/`, `testing/`,
`docs/`, `examples/` and names starting with `__`, keeping the first that qualify: jedi
finds direct dependents in ≥ 2 distinct files. Selection uses the oracle only, never
OXIDE's edges.

**Truth** (jedi 0.20.1, independent of OXIDE edges): `get_references` at the definition
name, definition sites dropped; each site maps to the innermost OXIDE symbol entity whose
span contains it, else its file entity; the target itself is excluded. Depth-2 truth adds
the direct dependents (same procedure) of every depth-1 dependent that is a function or
method. Every claim outside truth by any arm, and every truth item no arm reached, is
hand-audited on a sample of ≤ 40 per corpus; confirmed oracle errors are corrected in
truth and counted.

**Questions**: Q1 direct dependents; Q2 dependents within 2 hops.

**Arms**:

| Arm | What | Claims |
| --- | --- | --- |
| G | `rg -nw -t py NAME` (Q2: plus one `rg` per claimed function/method name), definition line dropped, each hit mapped to its enclosing entity | every enclosing entity |
| C | `build_context(f"What calls or depends on {name}?", 4096, symbols=[name])` | none (recall only: truth entities whose span overlaps a delivered item) |
| I | `impact(key, depth=1|2)` upstream, every followed edge | every hop |
| P | the same walk, `proven_depth` not None | proven hops |

**Metrics** per target, macro-averaged: precision (1 − false impact claims share),
recall (1 − missing dependents share), F1; delivered o200k tokens of the arm's raw text
(G: rg stdout; C: payload; I/P: one line per hop `depth proven via path:line`); tool
calls; wall latency (warm, median of 3).

**Path precision** (I): share of depth-1 hops whose witness (the evidence span of a
relation from the hop to a root) contains a jedi reference site of the target. Q2: share
of depth-2 hops whose two-step chain has a valid witness at each step.

**Completeness signal**: a target is *incomplete* in I when its recall < 1. The signal
fires when `unresolved > 0` or `stopped_by` is set. Report sensitivity (fires on
incomplete targets) and specificity.

## Stage 1 → Stage 2 gate

Proceed to an agent trial only if, macro over 24 targets, I or P beats G by ≥ 0.10 F1 on
Q1 or Q2, at ≤ 1.0× G's tokens on that question, **and** the completeness signal has
sensitivity ≥ 0.9. Otherwise: no new tool, mode or public field; the result and any
negative findings are recorded here and in `docs/public-api.md` where they change advice.

## Stage 2 (only if the gate passes)

A separate preregistration, frozen before any scored run, modelled on the structural
trial: paired arms (grep + search/context vs the same plus the graph representation),
the same isolation, an adoption criterion (graph used in ≥ half the relevant runs), and
correctness/cost criteria defined there.

## Not tested

Classes as targets (a class root stands for its methods, while jedi references name only
the class), TypeScript/Go/Rust, repositories larger than pytest.
