# ADR-0009 M3 live check: criteria and budget (written before any live request)

This is an integration check only. Nothing is scored, no consumed corpus or task is
used, and the policy, manifest and defaults are unchanged. The run is
`scripts/session.py live`: one real `oxide mcp` stdio session through a FastMCP client.

## Corpus and requests

Three public open-source code bases, none used by any OXIDE benchmark or decision study,
indexed in place with a private data location:
- CPython 3.13 standard library: 1,039 `*.py` files.
- pip: 406 files.
- fastmcp: 260 files.

There are 10 fresh queries per corpus, listed in `session.py`, each run twice at budget
4,096. That is **60 judged requests**. Offline (`results/sizes.json`), these requests
carry a median of 63.5 candidates (the cap is 64) and are 86 KB at the median, which is
comparable to Decision Study v3's pools (about 24.3k billed tokens).

## Budget

- A hard cap of **70 requests** in `server.py`. Request 71 and later are not sent.
- Expected cost: about 60 × 25k tokens × $0.042/M ≈ **$0.06**.
- Worst case: 70 × 64k tokens (the per-request context limit) × $0.042/M = **$0.19**.
- There are no retries and nothing is re-run. A failure is counted, not repeated.

## Acceptance criteria

| Id | Criterion | Threshold |
| --- | --- | --- |
| L1 | Compatibility: every HTTP 200 reply is judged (no `wrong_model`, `unparsable` or `incomplete`) | 0 violations |
| L2 | Deadline: `deadline` outcomes among the 60 | ≤ 3 (5%) |
| L3 | Availability: other fallbacks (`http …`, `transport`) | ≤ 6 (10%) |
| L4 | Judged `build_context` through MCP, client wall time | p50 ≤ 1.5 s, p95 ≤ 2.3 s |
| L5 | Every non-judged bundle equals the default bundle (spans and units) | all |
| L6 | Comparability: median billed input tokens | 15k–35k. Outside this range is a scope note, not a fail |
| L7 | After the session: no child process, and threads and fds back to their start values | exact |
| L8 | Spend | ≤ 70 requests, ≤ $0.10 billed |

**Verdict rule:** M3 is ready for an opt-in release (default unchanged) only if L1–L5,
L7 and L8 pass. An L2 or L3 failure means an outage episode was sampled. It is reported
with its counts and not rerun until it passes.
