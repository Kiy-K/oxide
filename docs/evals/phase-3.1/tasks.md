# Phase 3.1 tasks

## Navigation tasks (reused verbatim from Phase 2.2, `raw/run_activation_eval.py`)

Repos: `fixtures/py_repo`, `fixtures/ts_repo` (pinned benchmark fixtures).

### Bucket A — OXIDE should help (unfamiliar, multi-file discovery)

- **A1** (py): "There's a report that our HTTP client sometimes retries
  requests it shouldn't (e.g. permanent 4xx client errors), wasting time
  before giving up. Find where retry eligibility is decided in this repo
  and identify the exact check involved. Report the file and function name
  only — do not edit anything."
- **A2** (py): "Some users report getting stale cached data back even
  though it should have expired by now. Find where cache expiration is
  implemented in this repo and describe how expiry is checked. Report the
  file and function only — do not edit anything."
- **A3** (ts): "We refresh an auth token somewhere after it goes stale, but
  nobody remembers where that logic lives or what triggers it. Find it and
  report the file, the function, and what calls it. Do not edit anything."
- **A4** (ts): "The API client's retry backoff delay doesn't seem to grow
  the way engineers expect for the first couple of retries. Find where the
  backoff delay is computed and what implements the retry policy. Report
  the file and function only — do not edit anything."

### Bucket B — optional (subsystem known, implementation unknown)

- **B1** (py): "Somewhere in this repo's retry logic there's a test that
  checks the retry policy gives up after exhausting all attempts. Find
  that test and report which file and test function it is."
- **B2** (ts): "This repo has a `VersionedStore` class for tracking
  versioned values. Find every other file in the repo that imports or uses
  it, and report which ones (or report none, if there are none)."

### Bucket C — should not activate (exact-file, literal, tiny edit)

- **C1** (py): "In `oxidepy/cache.py`, rename the `TTLCache` class to
  `TimedCache`. Only touch that one file."
- **C2** (ts): "In `src/ui/Button.tsx`, add a one-line comment directly
  above the component saying `// TODO: memoize`. Only touch that one
  file."
- **C3** (py): 'In `oxidepy/http_client.py`, add a module-level docstring
  line at the very top if one is not already present: `"""Thin HTTP client
  wrapper."""`. Only touch that one file.'
- **C4** (ts): "In `src/net/retry.ts`, rename the exported const
  `defaultRetryPolicy` to `DEFAULT_RETRY_POLICY`. Only touch that one
  file."

## Coding tasks (real bug fixes with runnable acceptance tests)

- **coding-py** — `eval-agent/tasks/py_bug_retry` (reused from Phase
  2.2/2.3's coding-outcome tier). Prompt: "There's a bug reported against
  this repo's retry/backoff logic: clients are hammering the server harder
  on each retry instead of backing off. Find the bug and fix it. Do not
  change test files." Verified to fail (`verify.sh`, `python3 -m unittest
  discover`) on the unmodified fixture before any run.
- **coding-ts** — `eval-agent/tasks/ts_bug_store` (new to this phase).
  Bug: `VersionedStore.set()` never advances its internal version counter,
  breaking optimistic-concurrency checks that read `versionOf()`. Prompt:
  "There's a bug reported against this repo's VersionedStore: version
  numbers used for optimistic-concurrency checks never advance when a key
  is updated. Find the bug and fix it. Do not change test files." Verified
  to fail (`verify.sh`, `bun test`) on the unmodified fixture before any
  run.

Neither coding task's prompt was derived from OXIDE's own retrieval output
— both are pre-existing fixtures with known, injected bugs and independent
test suites.
