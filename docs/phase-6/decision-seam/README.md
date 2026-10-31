# Decision seam: infrastructure verified, not judge quality

Historical implementation verification, 2026-10-24. The optional provider replaces candidate value only; deterministic admission, hints, caps, ties, prerequisites and budget remain policy. Replay supports captured judgments. This report is not the current API contract; see [public API](../../public-api.md) and [ADR-0009](../../adr/0009-jev-assisted-context-ordering.md).

Reported checks:

- No judge and heuristic replay reproduced the frozen baseline bundles; Phase 3/Python gates and 17 TypeScript tests passed. No frozen artifact regenerated.
- Missing/abstained/invalid candidate judgments fall back per candidate; provider exceptions, non-mappings and unknown keys fall back for the whole request. Bool, non-finite and out-of-range values are invalid.
- `oxide-capsule-v1` canonical JSON/digests change with evidence, omit gold/packing outcomes and hydrate only captured subject bytes. Bounds: 1,024-byte key/query, 4,096-byte excerpt, 16,384-byte canonical capsule. Clipping is explicit; unsendable subjects keep the heuristic.
- Adapter timeout stops waiting, **not the daemon thread**; a real adapter must bound its own I/O. The default path starts no thread. Recorded judgments replay by digest; batch order is digest-sorted.

Cost: [overhead.txt](results/overhead.txt), fixture pool (48 candidates, 41 asked), 500 calls, Python 3.13.17/x86_64: validation ~25 µs, heuristic replay ≤0.1 ms packing overhead; no pre-change pack timing. [capsules.json](results/capsules.json), 15 Python dev requests: median build 4.7 ms, median 60 capsules / 128,919 canonical bytes / 38,349 o200k tokens; 245/707 capsules truncated, none unsendable. Whole-request repetition of the query made canonical rendering expensive.

Limits: subject excerpt only, no hydrated neighbours; long queries clip; lexical features need rank-ablation; bounded schema was not quality-tuned. No model evaluated here. [Decision Study v1](../decision-study-v1/README.md) and its successors evaluated judgment separately; later integration supersedes this report's then-core-only status.

## Reproduce

```sh
uv run pytest tests/python/test_judgment.py tests/python/test_capsule.py
uv run python docs/phase-6/decision-seam/scripts/overhead.py 500
uv run --with tiktoken python docs/phase-6/decision-seam/scripts/capsules.py OUT.json
```

Capsule measurement requires the pinned ContextBench corpora and tokenizer cache; use private output/data locations and `CPUQuota=200%`. Results/scripts remain unchanged.
