# Agent context overhead audit (Phase 2 + 2.1)

The failure mode this guards against: *OXIDE saves N repository tokens but
costs more than N explaining itself.* Two costs to measure separately —
the one-time/session cost of teaching an agent to use OXIDE at all, and the
per-call cost of every response it returns.

## J — cost of exposing OXIDE

| cost | size | when paid |
|------|-----:|-----------|
| `skills/oxide-code-context/SKILL.md` (what actually loads into context today) | ~800 tokens (3,211 chars / 4) | once, when the skill is triggered |
| `docs/agent-usage-policy.md` (referenced, not force-loaded) | ~1,290 tokens | only if the agent/human explicitly reads it |
| actual `context` MCP schema (namespace `oxide`) | 285 chars / ~71 tokens | every turn |
| actual `search` MCP schema (namespace `oxide`) | 302 chars / ~76 tokens | every turn |
| server instructions | 278 chars / ~70 tokens | every initialization/context load |
| compact MCP persistent total | 868 chars / **~217 tokens** | every turn |
| hypothetical seven-operation CLI/admin schema | 1,501 chars / ~375 tokens | estimated, every turn |
| compact savings vs. full schema estimate | ~911 chars / **~228 tokens** | estimated, every turn |

The actual compact MCP surface is larger than the earlier "well under 200"
schema estimate because each tool includes its typed input object and safety
constraints. It remains a small recurring cost: approximately 217 tokens for
the two schemas plus initialization instructions, measured as chars/4 from
the real `tools/list` and `initialize` responses.
The hypothetical full-surface estimate includes the existing seven CLI
operations and their current argument definitions; those tools are not exposed.

The skill's one-time ~800 tokens is spent once per session, not per call — a
single `context` response at typical budget (README's official benchmark:
budgeted mode averages ~1,944 tokens; this repo's own smoke test returned
416 tokens against a 2,048 budget for a 43-symbol fixture) already exceeds
that one-time cost, and a session makes many `context`/`search` calls against a
single skill load. The recurring MCP cost is what matters for this transport,
since tool schemas sit in the system prompt on every turn.

## K — context/search response field audit

Every field on `Evidence` (`src/service.rs`) and its `context`-only
extensions (`role`, `est_tokens` on `ContextEvidence`; `id`/`why` on
`Omitted`) was checked against four buckets: required for coding, useful
for navigation, diagnostic-only, redundant. No ranking, allocation, or
retrieval-route logic was touched doing this — this is response-shape
review only.

| field | bucket | keep? |
|-------|--------|-------|
| `file`, `start_line`, `end_line` | required for coding | yes — where to look/edit |
| `snippet` | required for coding | yes — bounded source, avoids a full-file read for orientation |
| `qualified_name`, `id` (`path#qualified_name`) | useful for navigation | yes — cross-references other items in the same pack |
| `kind`, `language`, `name` | useful for navigation | yes — cheap (a few tokens), lets an agent skim without re-parsing `qualified_name` |
| `reasons[]` | useful for navigation | yes — the trust-weighting mechanism the whole "verify before editing" policy depends on (`lexical=`/`semantic=` = direct match; `parent←`/`imported-definition←` = structurally resolved; `uses←`/`test←` = heuristic, weaker) |
| `score` | useful for navigation | yes, single float — not duplicated elsewhere; items already arrive sorted, so this is a secondary signal, not the primary one |
| `role` (context only) | useful for navigation | yes — distinguishes a direct hit from a structural/test neighbor |
| `est_tokens` (context only) | useful for navigation | yes — lets an agent reason about the pack's budget usage |
| `omitted[].id`/`.why` (context only) | useful for navigation | yes — tells the agent what didn't fit and why, so it knows to broaden rather than assume completeness |

No field fell into "diagnostic-only" or "redundant" in the Phase 2 audit.
Phase 2.1's smaller real-agent sample classified path/location/symbol/snippet
as observably useful, ranking/provenance fields as possibly useful, and token
accounting as apparently unused in that sample. This is not enough evidence
to change the frozen DTO; see `docs/evals/phase-2.1/results.md`.
