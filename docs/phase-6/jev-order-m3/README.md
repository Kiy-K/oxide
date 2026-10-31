# JEV M3: lifecycle checks passed; release was not ready

Historical release-readiness study, 2026-10-24; [preregistration](preregistration.md). **NOT READY at this checkpoint**, not a new quality experiment. [Release blockers](../release-blockers/README.md) records subsequent work; current contracts live in [ADR-0009](../../adr/0009-jev-assisted-context-ordering.md) and the [public API](../../public-api.md).

Reported measurements:

- Local fault soak: 429 MCP calls / 423 provider requests; 200/200 fallback bundles matched, 40/40 recovery calls succeeded. Three threads, eight FDs, no child leak; RSS grew ~0.3 MB over the final 320 calls. This was minutes, not an hours-long soak.
- Authorized live run: 60 calls over CPython stdlib (1,039 files), pip (406), FastMCP (260), Linux only. All HTTP 200 and judged; latency p50/p95/max 1.00/1.17/1.66 s, 1,289,562 tokens, $0.054. No new task-quality ground truth.
- Fixes covered proxy/CA environment forwarding, judged MCP headers, and portable Windows connection-reset handling. `CREATE_NO_WINDOW` desktop behavior was inferred, not desktop-tested; CI/platform follow-ups were not yet run.

Blockers then: replacement FTS binaries reported a 0.22-dev version rather than the pinned 0.21 ABI (no blind repin), and provider terms/retention needed clarification. Provider-policy snapshot: no training without consent, no fixed retention promise, enterprise-only ZDR, a perpetual telemetry license, US service providers, unread subprocessor list, uncertain self-serve MCA versus website confidentiality scope, no SLA. These are dated review observations, not current legal advice or verified current terms.

## Evidence and reproduction

Retain `results/`, the preregistration and [session harness](scripts/session.py). Its `soak`, `sizes` and `live` commands take a private work directory and output JSON; exact setup/flags remain in the harness. Live calls need new disclosure/spend authorization and credentials. Source bodies, keys and raw provider replies were not committed; committed summaries alone cannot recreate those replies. No live calls were made during documentation consolidation.
