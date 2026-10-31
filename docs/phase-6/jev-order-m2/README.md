# ADR-0009 milestone M2: opt-in judge and live check (2026-10-24)

[ADR-0009](../../adr/0009-jev-assisted-context-ordering.md) § M2. This is **integration
verification only**: no new ranking experiment, no consumed corpus re-run, no quality
claim, no change of default. The quality evidence remains Decision Studies v2 and v3.
Numbers are measured unless marked otherwise.

## What changed

| Piece | Where | Contract |
| --- | --- | --- |
| `Repository.judge(enable=None) -> JudgeStatus` | `src/oxide/api.py`, `models.py` (exported) | `True` stores consent for this root and the current manifest digest in the repository's store (`judge.json` under the data location, never in the repository). `False` deletes it. `None` reads it. Rejects non-booleans |
| `build_context` | `src/oxide/api.py` | Passes `jev.configured(self)`: `None` without consent (today's bundle, one `stat`), else the M1 ordering |
| `configured`, `status`, `authorize`, `revoke`, `KEY_ENV`, `DISCLOSES` | `src/oxide/jev.py` (still internal) | Consent naming another manifest, an unreadable record or another root → `not_authorized`, unsent. Consent without `TYPESAFE_API_KEY` → `no_credential`, unsent |
| `oxide judge PATH [--enable \| --disable] [--json]` | `src/oxide/cli.py` | Prints state, endpoint, manifest, whether the key is set, what is sent, unverified retention, and the enable/disable commands. `oxide context` prints `judged by …` or the fallback line to stderr |

The policy, mapping, renderer, deadline and transport are M1's, unchanged. Nothing new
was added to `pyproject.toml` or `uv.lock`. Tests remove `TYPESAFE_API_KEY` from every
test's environment (`conftest.py`).

## Offline tests (`mise run verify`, no network)

All in `tests/python/test_jev.py`, against localhost:
- Disabled by default: with a key set and no consent, the bundle is identical and no
  connection is made.
- Enabled: one request with the bearer key, and `versions` name the manifest. Consent
  is stored under the private data location and nothing is written in the fixture.
  Revoking gives back the identical default bundle.
- Stale manifest, another root, an unreadable record, or no key: falls back with the
  named outcome. Production's spans and `versions` are kept and nothing is sent.
- Consent for one repository does not cover a copy of it elsewhere.
- The CLI round trip: status, enable, `context` reporting `no_credential`, and disable
  with `--json`.

`mise run verify`: lint, `mypy --strict`, 379 passed, 7 skipped (the TypeScript gate,
off by default), including the frozen baselines.

## Live check ([results/live.json](results/live.json))

`scripts/smoke.py live`, authorized by the maintainer (spend and corpus) on 2026-10-24:
- **Corpus:** the public fixture `fixtures/py_repo`, copied with a private data
  location.
- **Requests:** 5 queries × 2 repeats at budget 1,024, through
  `Repository.judge(True)` and `build_context`. There were two fallback probes, each run
  on 2 queries.
- **Spend:** a hard cap of 14 requests.
- **Measured under:** `systemd-run --user --scope -p MemoryMax=3G -p CPUQuota=200%`.

| Check | Result |
| --- | --- |
| Request/response compatibility, model identity | 10 / 10 `judged`. Every reply named `jev-1.14.0` and answered every candidate |
| End-to-end judged `build_context` (child start, TLS, network, inference) | p50 **0.70 s**, p95 = max 1.20 s (n = 10) |
| Default `build_context`, same queries | p50 0.010 s, max 0.019 s |
| Adapter call alone (child process to parsed reply) | p50 0.69 s, max 1.18 s |
| Invalid key | `http 401` both times (0.73 s, 0.50 s). Production's spans, no judge thread left |
| Deadline enforcement (0.3 s adapter deadline) | `deadline` both times, the request returned at 0.33 s and 0.32 s. Production's spans, no judge thread left |
| Request size | 22–44 KB. Billed 3,065–11,641 input tokens per judged request |
| Cost | 67,534 billed tokens, **$0.0028** for the run (the two killed and two 401 requests reported no usage) |

Offline baseline ([results/offline.json](results/offline.json)): the same run against a
localhost server that answers at once. Judged `build_context` p50 0.053 s versus 0.007 s
for the default. The adapter call, which is mostly child process start-up, p50 0.042 s.
So OXIDE's own overhead is about 45 ms a request. The rest of the live 0.70 s is network
and inference (*inferred* from the difference).

## M2 readiness and what remains before release

Ready (measured): the opt-in, consent and disable path, and live compatibility with
the pinned model. Deadline kill and fallbacks were checked live, and the default is
unchanged.

Still open:
- **Availability in real use.** Ten live requests in one window say nothing about
  outage rates. The v2 529 episode is the known risk, and immediate fallback is the
  mitigation.
- **Latency on real repositories.** The fixture's requests are smaller than the
  studies' (~24k tokens). v3's p95 was 1.45 s, with 2/52 killed at 2.0 s.
- **The provider's data retention and terms.** Not verified. They are disclosed in
  `JudgeStatus.discloses` and in `oxide judge`.
- **Long-running MCP servers.** Each request uses one child process. That was
  verified per request, not over hours.
- **Scope of the claims.** Python only, budgets 256 / 1,024 / 4,096, and repositories
  under 3,000 files (ADR-0009 open limits).
- **Changing the default** remains out of scope.

## Reproduce

```sh
uv run pytest tests/python/test_jev.py
uv run python docs/phase-6/jev-order-m2/scripts/smoke.py offline results/offline.json
TYPESAFE_API_KEY=... uv run python docs/phase-6/jev-order-m2/scripts/smoke.py live results/live.json  # sends the fixture, ≤14 requests
```
