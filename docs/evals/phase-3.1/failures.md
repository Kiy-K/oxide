# Phase 3.1 failure attribution and evidence utilization

## Failure attribution (phase brief §19 categories)

| Category | Observed in this phase? | Evidence |
|---|---|---|
| `ACTIVATION` | Yes — the dominant failure mode | Bucket A/B "missed" cases: the model went straight to native `grep`/`read` without ever attempting OXIDE, despite guidance present (conditions B/C/D). 6/12 misses under B, 9/12 under C, 2/12 under D (Bucket A, muse-spark). |
| `TRANSPORT_SELECTION` | **Not observed as a failure** | Condition E never produced `BOTH_REDUNDANT` (`transport-selection.md`) — the one failure mode this category exists to catch did not occur. |
| `TOOL_SELECTION` | Not observed | No run called `search` where `context` was clearly needed or vice versa in a way that blocked task completion — see "search's role" below. |
| `RETRIEVAL` | Not observed as a cause of task failure | All 34 coding tasks succeeded (100%) regardless of transport; no case where OXIDE returned evidence that was retrieved but wrong/misleading and caused an incorrect fix. |
| `ALLOCATION` | Not applicable | Out of scope for this phase (context allocation is frozen product behavior, not touched). |
| `UTILIZATION` | Not observed as a failure | Every run that called OXIDE at all (68/68) followed up with at least one native `read` of actual source — consistent with the skill's explicit "read source before editing" instruction being followed, not with results being fetched and ignored. See "evidence utilization" below. |
| `MCP_PROTOCOL` | Not observed in real agent runs | The only protocol-level facts (malformed args, error shapes) come from the scripted client (`mcp-protocol-behavior.md`), not from a real agent hitting a protocol bug — no real run showed a client mishandling the MCP contract. |
| `FRESHNESS` | Not applicable | Every condition pre-built the index before the agent ran (protocol.md §4); no stale-index scenario was exercised in the main matrix. |
| `CODING_REASONING` | Not observed | 100% coding success — no case of the model reasoning incorrectly about the bug once it had located the right file. |
| `VERIFICATION` | Not observed | Every coding run's fix was checked against the task's real test suite (`verify.sh`), not just "text changed" — the phase brief's explicit requirement (§6). |
| `INFRASTRUCTURE` | Yes, one confirmed incident | muse-spark went silently unresponsive mid-batch (protocol.md §4.5) — 21 nav-run timeouts total, all during that window, all retained and excluded from activation math, none miscounted as `ACTIVATION` failures. |

**The overwhelming majority of "failures" in this phase are `ACTIVATION`
misses** (the model simply didn't reach for OXIDE), not retrieval quality,
transport confusion, or coding-reasoning problems — consistent with every
prior phase's finding (Phase 2.2/2.3's own miss-forensics reached the same
conclusion for CLI alone).

## Evidence utilization (phase brief §14)

Of 68 total runs (across all conditions and both models) that called
OXIDE at all, **68/68 (100%) also performed at least one native `read`
call** — the model consistently used OXIDE's output to identify a
narrower set of files, then read the actual source before answering or
editing, matching the skill's explicit instruction ("read the actual
source ... never edit from the pack alone") and the MCP server's own
`instructions` string ("Read source before editing"). This is consistent
with **retrieved + used**, not **retrieved + mostly ignored** — though
this phase did not do a per-run manual read of final answer text against
retrieved file paths to confirm the *specific* files read matched
OXIDE's output exactly (a stronger, more expensive utilization check
possible in a future phase). No run showed **retrieved + duplicated by
native tools** in the sense of the phase brief's warning example (calling
OXIDE, then grepping the exact same symbols again) — Bucket A/B/D/E's
`native_grep_calls` after an OXIDE call were low or zero in the sampled
logs reviewed during harness validation (`protocol.md` §3's smoke tests),
though this was not exhaustively re-verified across all 68 runs given
time constraints; flagged as a partial finding.

## Search's role (phase brief §15)

`oxide_search_calls`/`oxide_cli_search_calls` were rare relative to
`context` calls across the matrix (visible in the raw
`oxide_mcp_search_calls`/`oxide_cli_search_calls` fields of
`results.jsonl`) — matching Phase 2.3's own finding that a single
`context` call usually supplied enough evidence to answer these task
sizes outright, leaving less room for `search` to demonstrate a distinct
follow-up role. This phase does not have a case where `search` was used
as a "generic one-shot lookup" replacing `context` entirely, nor strong
evidence of the desired `context → uncertainty → search` workflow given
how rarely `search` fired independent of `context` in the same run — an
observation carried forward from Phase 2.3, not a new finding unique to
MCP.

## Error/fallback behavior with real agents (phase brief §16, agent half)

The main matrix pre-built the index for every condition except A
specifically to avoid measuring error-fallback behavior by accident
(protocol.md §4) — so no real-agent run in the main matrix exercised an
`IndexMissing`/`IndexIncompatible`/`EmbedderUnavailable` error path. The
scripted-client half of §16 (malformed arguments, `index_missing`, both
confirmed as spec-compliant) is in `mcp-protocol-behavior.md`. **A
dedicated agent-facing error-fallback check (deleting `.oxide/index.db`
mid-session, or pointing at a non-repo, with a real agent watching) was
not run in this phase** given time constraints — recorded here as a gap,
not glossed over.

## Client-compatibility gotcha discovered mid-phase, not a research finding

The Codex CLI "auth-gated" claim from an earlier draft of this phase was
wrong — a self-inflicted harness bug (isolating `CODEX_HOME` wiped the
ChatGPT-login credential file). Corrected in `client-compatibility.md`
with real behavioral data once fixed. Recorded here too since it's exactly
the kind of methodology error the phase brief's honesty requirements exist
to catch, and it would have understated Codex's real capability if left
uncorrected.
