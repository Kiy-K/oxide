# Agent context overhead audit (sections J/K)

The failure mode this guards against: *OXIDE saves N repository tokens but
costs more than N explaining itself.* Two costs to measure separately —
the one-time/session cost of teaching an agent to use OXIDE at all, and the
per-call cost of every response it returns.

## J — cost of exposing OXIDE

| cost | size | when paid |
|------|-----:|-----------|
| `skills/oxide-code-context/SKILL.md` (what actually loads into context today) | ~800 tokens (3,211 chars / 4) | once, when the skill is triggered |
| `docs/agent-usage-policy.md` (referenced, not force-loaded) | ~1,290 tokens | only if the agent/human explicitly reads it |
| a future 2-tool MCP schema (`context`, `search` — name + one-line description + a handful of typed params each, no prose) | estimated well under 200 tokens | every turn (MCP tool schemas are typically resident in the system prompt) |

The skill's one-time ~800 tokens is spent once per session, not per call — a
single `context` response at typical budget (README's official benchmark:
budgeted mode averages ~1,944 tokens; this repo's own smoke test returned
416 tokens against a 2,048 budget for a 43-symbol fixture) already exceeds
that one-time cost, and a session makes many `context`/`search` calls
against a single skill load. The 7-command full-CLI framing (all of
`index`/`status`/`search`/`review`/`stats`/`context`/`eval` explained) would
cost meaningfully more to describe and, per the section G/H audit, does not
buy proportionally more useful behavior — `status`/`stats`/`review`/`eval`
have no normal-implementation use case (see `docs/agent-surface.md`).

Recurring per-turn cost is what actually matters for an MCP transport,
since tool schemas sit in the system prompt on every turn of a session, not
just once. That is the strongest argument for the compact surface over the
full CLI: `context`+`search` schemas are two short entries; all 7 commands'
schemas (index's embedder/path/json flags, search's mode/limit/no_expand,
review's diff range, etc.) would be several times larger and paid every
turn, not once.

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

No field fell into "diagnostic-only" or "redundant." This matches what
Phase 1.1 already did in the JSON-contract work (`README.md`'s "JSON
migration note": internal `Symbol` fields like `content_hash`, `imports`,
`parent`, `references`, and the internal `query_used` string were already
stripped from wire output before this phase started). The conclusion of
this audit is that no further trimming is needed, not that trimming was
skipped — recorded here so the audit itself is on record, not just its
absence of findings.
