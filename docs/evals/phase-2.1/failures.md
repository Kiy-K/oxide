# Phase 2.1 Failure Classification

## Observed infrastructure failure

- One initial OpenCode runner screen used the account's default Clinepass
  provider and received HTTP 429 monthly-limit responses. Those runs produced
  zero agent tool calls and are excluded from behavioral numerators, but remain
  raw artifacts as `infrastructure failure`.
- Claude initialization was successful but the model request stopped with
  `Not logged in`; Codex transport was accepted but model execution was not
  authenticated. Neither is counted as a behavioral run.
- No MCP transport failure occurred in the seven real-binary protocol tests.

## Classification rules

Every non-infrastructure run is classified against the following buckets:

- **AGENT ACTIVATION** — missed OXIDE on A, or unnecessary OXIDE on C
- **TOOL SELECTION** — context/search order or redundant calls
- **RETRIEVAL** — relevant evidence absent from candidates
- **ALLOCATION** — candidate evidence excluded from context pack
- **UTILIZATION** — evidence supplied but not used in read/edit/reasoning
- **FRESHNESS** — missing, stale, incompatible, or corrupt index state
- **TRANSPORT** — protocol/client/schema/response failure
- **CODING/REASONING** — adequate evidence but incorrect implementation
- **VERIFICATION** — implementation likely correct but not tested/checked

## Current evidence

The Phase 2 behavioral screen found: no-OXIDE baseline completed a focused
navigation task with native grep/read; MCP without instructions used a redundant
search→context sequence; compact instructions produced one context call; a
known-file negative control made zero OXIDE calls; and a context→search focused
follow-up used both results. These are directional observations, not a claim
of general performance.

No retrieval/indexing change is justified by the Phase 2.1 evidence. No MCP
error semantics are changed.
