# ADR-0013: Selective, source-backed graph navigation and tiered context in `oxide_search`

## Status

**Proposed**, 2026-10-25. Nothing is implemented in `src/oxide`. The experimental server
lives only in `docs/phase-6/agc-v4/scripts/server.py`. The proposal becomes Accepted, and
then implemented, only if AGC v4's preregistered rule promotes the interface
([docs/phase-6/agc-v4](../phase-6/agc-v4/README.md)) and the maintainer agrees.

## Context

Earlier studies:

| Study | Result | What it showed |
| --- | --- | --- |
| AGC v1 (7eca108), REJECT | Tool cost doubled tokens | The cause was OpenCode's code mode, not the tool |
| AGC v2 (5cf699d), REJECT | The agent rarely used a direct tool | Tasks were ones grep/read already solved (94%) |
| AGC v3 (0381565), PROMOTE G (preregistered) | Structural tasks (dispatch, two-hop impact) | Forced use: success 0.86 vs 0.57 for grep/read, false claims 0.67 vs 2.05, cost per success 0.58× |

In v3, the agent picked the tool unprompted in 67% of runs. The guided arm (G) was worse
than the unguided one (D):

- **Measured:** CPS G/D 1.33 (CI 1.10–1.77). The success gap was not significant
  (16/21 vs 14/21, p = 0.73).
- **Mechanism (*inferred*):** the guidance ("callers first, depth 2, don't grep again")
  doubled tool calls and halved verification greps. Precision fell from 0.86 to 0.81 at
  the same recall, with depth-2 walks run from intermediate symbols.
- Analysis: `docs/phase-6/agc-v4/scripts/v3_dg.py`.

Production MCP already has one tool, `oxide_search`, with `mode=search|context`. A second
tool adds schema to every prompt and one more choice for the agent to make. A new mode
reuses an interface the agent already sees.

## Decision (proposed)

1. **One tool.** `oxide_search` gains three read-only modes. There is no separate
   `oxide_context`, unless AGC v4 shows the split shape has a clear advantage.
   - `mode=callers` or `mode=callees` with a symbol in `query`:
     - the bounded upstream or downstream walk (`Repository.impact`);
     - each hop's count from that symbol and the source line that links it
       (`Repository.relations` evidence);
     - for callers, the name's occurrences that no edge explains, or for callees, the
       calls that weren't resolved.
     - `depth` 1–3, default 1.
   - `mode=outline`: the package map (empty `query`), a file's declarations with line
     ranges, or a symbol's signature, docstring and members. This needs one new read-only
     API method (`Repository.outline`), additive.
2. **Source-backed.** Every listed relation cites `path:line` evidence from the index.
   The tool never returns source beyond those lines: exact source is the client's `read`,
   or `mode=context`.
3. **Selective.**
   - No ranking, no packing, and no change to `search` or `context`.
   - The tool's description says when the modes help (relationship questions,
     orientation) and when grep/search is enough (where a single name is defined).
   - No fixed depth, and no "call this first" or "don't grep again" instructions.
   - No system-prompt guidance ships: v3 measured the cost.
4. **Honest about completeness.**
   - Output says it is not exhaustive.
   - It reports the bound that stopped the walk (`stopped_by`) and the unexplained
     residual.
   - Heuristic and ambiguous edges are labelled ("likely"), as in `Impact.proven_depth`.
5. **Boundaries.**
   - MCP stays a client of `oxide.api` (AGENTS.md § Layout): no parsing, routing or
     selection in the server.
   - LadybugDB stays authoritative ([ADR-0002](0002-ladybugdb-knowledge-store.md)).
   - Graph algorithms over projections follow [ADR-0012](0012-networkx-graph-algorithms.md).
   - Code mode is not used.

## Evidence required before Accepted

AGC v4's preregistered rule must pass. In brief:

- **Interface:** the single shape is chosen unless the split shape shows the defined clear
  advantage.
- **Gate:** the chosen shape must clear it with *natural* use against a capable grep/read
  baseline. Forced use is a separate diagnostic and never counts as adoption.
- **Tasks:** fresh, rule-selected, across Python and TypeScript and five question types.

If v4 doesn't promote, this ADR stays Proposed or is withdrawn, and graph navigation stays
API-only (`Repository.impact`).

## Consequences

- **Costs:**
  - About 600 more first-step prompt tokens per session for the larger `oxide_search`
    schema (dev measurement: 615).
  - A new public API read (`outline`).
  - The MCP surface grows by three modes rather than a tool.
- **Risks:**
  - Agents may over-call the graph modes, as G did in v3. The description must stay
    selective, and the gate measures cost per success.
  - Graph incompleteness, which the residual and `stopped_by` make visible.
- **Not decided here:** CLI parity for the new modes; non-OpenCode clients (to be
  measured separately).
