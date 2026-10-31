# JEV reliability: PASS under fallback, not retry evidence

Historical provider-only study, 2026-10-24; [preregistration](preregistration.md), frozen at `1deec3b`. **Keep immediate deterministic fallback.** No quality claim, product change or reinterpretation of Decision Study v2's INCONCLUSIVE verdict.

Reported run: eight windows 40 minutes apart, 05:24–10:05 UTC on one day; 17 logical requests at concurrency 1/2/4 per window = 408 requests using v2's 15 public dev bodies (~15.2k input tokens). All 408 returned HTTP 200 from `jev-1.14.0`; no malformed/wrong-model responses. Pooled p50/p95/max latency 0.90/1.03/2.14 s; $0.2597 of a $0.40 cap.

One answer missed the 2.0 s deadline: fallback failure rate 1/408 (0.002, CI 0.000–0.014). O0/O1/O1w/O2/O3 passed. **Retry/breaker gates did not:** zero retryable failures or bursts were sampled; retry amplification/cost criteria passed only vacuously. No `Retry-After` or request ID was returned.

Do not infer that 529 outages are rare: v2 had a substantial overload episode earlier that day. Payloads were dev-only, concurrency ≤4, windows covered five hours. A breaker candidate (three failures, 15 s open) remained untested. [Decision Study v3](../decision-study-v3/README.md) later confirmed quality on fresh tasks; it does not erase these limits.

## Reproduce and evidence

[scripts/probe.py](scripts/probe.py) sends authorized calls; [scripts/analyze.py](scripts/analyze.py) replays the frozen policy analyses; [results/gate.txt](results/gate.txt) retains the complete gate output. Exact window/payload/spend protocol is in the preregistration. Source-bearing dev bodies, raw status/time/header attempts and ledger remain external in `~/Projects/oxide-eval-data/oxide-jev-reliability/`; live reruns need new authorization. No source bodies were committed, and no live calls were made during consolidation.
