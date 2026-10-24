# ADR-0009 milestone M1: in-tree JEV ordering, offline parity (2026-10-24)

[ADR-0009](../../adr/0009-jev-assisted-context-ordering.md) § Smallest implementation
milestone. This is **implementation verification only**: no live call, no new ranking
experiment, and no new quality claim. The quality evidence remains Decision Studies v2
and v3. Numbers are measured.

## What changed

| Piece | Where | Contract |
| --- | --- | --- |
| `AdmittedOrder`, `Unanswered`, `POLICY` (`oxide-order-admitted-v1`) | `src/oxide/core/ordering.py` | ADR-0009 § 1 as a `DecisionProvider` over `CapsuleJudge`. The heuristic admits (`value ≥ INCLUDE_FLOOR`), admitted candidates get `F + 0.59 p`, others abstain. Any unsent, unjudged or invalid admitted candidate raises, so `packing.judged` keeps the heuristic for the whole request. `outcome` records `judged` or the fallback reason |
| `Jev`, `order`, `body` | `src/oxide/jev.py` (internal, not exported) | `ds1-compact-v1` rendering and v1's questions, ported verbatim. The pinned manifest is asserted equal to v3's (`sha256:d9ae934c…`). One stdlib call in a child process killed at 2.0 s, no retry, model check. Outcomes: `deadline`, `transport`, `http <status>`, `wrong_model`, `unparsable`, `incomplete`, plus `unsendable` and `not_authorized` from the ordering and authorization. `order` sends nothing unless `authorized` resolves to the repository's root |
| `Repository._context(…, by)` | `src/oxide/api.py` | Internal entry point. `build_context` calls it with `None`, so its signature and output are unchanged. A judged bundle adds `order`, `judge` and `judge_manifest` to `versions`. A fallback adds one `judge … fell back: <outcome>` degradation and keeps today's `versions` |

`packing`, `capsule`, `oxide/__init__.py`, the CLI, MCP, `pyproject.toml` and `uv.lock`
are unchanged. There is no new dependency or public configuration.

## Offline tests (`mise run verify`, no network)

`tests/python/test_ordering.py`:

- the mapping, and the order change at a one-item budget;
- every incomplete-answer class (missing, `None`, NaN, bool, out of range) packs the
  heuristic;
- unsendable, deadline and adapter failures;
- `_context(None)` equals `build_context` at budgets 0 to 4,096;
- judged `versions`;
- a fallback packs production's spans and units, with one degradation.

`tests/python/test_jev.py`, against a localhost server:

- the body sent is `jev.body(capsules)` with the bearer key;
- 529, wrong model, partial, garbage, hang and late each make exactly one attempt and
  give their named outcome within the deadline (a late reply is never read);
- an unreachable server is `transport`;
- an authorized repository is judged with one call;
- **an unauthorized one opens no connection**;
- a hung judge falls back by the deadline, and no `oxide-judge` thread survives it.

## Parity with the evidence ([results/parity.txt](results/parity.txt))

`scripts/parity.py` reads the studies' recorded requests and judgments (outside the
repository) and makes no call. It ran under `systemd-run --user --scope -p MemoryMax=3G
-p MemorySwapMax=0 -p CPUQuota=200%`. The bundle check runs the unchanged ContextBench
scorer, with `build_context` routed through `Repository._context` and the recorded
judgments replayed by capsule digest. Every task's `line_recall` and `used` must equal
the study's scored arm at every budget (asserted, not just the macro).

| Set | Request bodies identical to the study adapter's | Tasks equal at every budget | Macro LR @256 / 1,024 / 4,096 | Fallback bundles |
| --- | ---: | ---: | --- | ---: |
| v2 dev, primary | 15 / 15 | 15 / 15 | 0.262 / 0.6086 / 0.7829 | 0 |
| v2 held-out, primary | 29 / 29 | 29 / 29 | 0.0515 / 0.2298 / 0.3602 | 30 |
| v3, primary | 52 / 52 | 52 / 52 | 0.0334 / 0.3026 / **0.4755** | 0 |
| v3, repeat | (same requests) | 52 / 52 | 0.0318 / 0.2977 / 0.4686 | 6 |

The numbers are v2's dev gate (0.7829), v2's held-out end-to-end result (0.360), and
v3's primary and repeat arms, exactly. Fallback bundles are task × budget pairs. v2
held-out's 30 are its 10 tasks without an answer (the 529s), and v3 repeat's 6 are its 2
requests killed at the deadline. In a replay those show as `incomplete`, because the
recording holds no judgment for them. They pack production, as in the studies.

## What M1 does not show

- **No live call.** The in-tree transport has only been exercised against localhost.
  A live smoke call (public fixture only) needs its own spend and corpus authorization.
- No new quality evidence, and no measurement of judged latency in real use. Exposure,
  configuration, authorization UX and any default change remain later decisions
  (ADR-0009 § 5).

## Reproduce

```sh
uv run pytest tests/python/test_ordering.py tests/python/test_jev.py
TMPDIR=<on disk> uv run python docs/phase-6/jev-order-m1/scripts/parity.py v2-dev v2-heldout v3 \
  > docs/phase-6/jev-order-m1/results/parity.txt   # needs ~/Projects/oxide-eval-data
```
