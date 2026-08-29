# Phase 2.1 Pinned Tasks

All repos pinned: `fixtures/py_repo` and `fixtures/ts_repo` from freeze commit `9453067` (no external git), `deepseek-harness` at `99f6f02`, and `seaborn` at `f04b6cd` (working copies pinned by SHA before indexing). Indexes are frozen before A/B/C.

| id | bucket | repo | task | acceptance |
|----|--------|------|------|------------|
| A1 | A | py_repo | `RetryPolicy.should_retry` incorrectly retries 4xx — fix to retry only 5xx/transient (attempt gate + status_code >=500) | `grep -q "status_code.*>= 500" oxidepy/retry.py` and existing `tests/test_retry.py` still passes for 5xx case (or direct `python -c` check) |
| A2 | A | py_repo | Cross-file refresh behavior: locate `AuthService.refresh_token`, its token-store dependency, and its tests; explain whether a missing stored token is rejected before refresh | `oxidepy/auth.py:40-47` and `tests/test_auth.py` identified; existing auth tests pass |
| A3 | A | ts_repo | `VersionedStore` TTL expiry: locate expiry logic and ensure expired entries are evicted on read | `src/cache/versioned_store.ts` read, patch or answer references TTL branch |
| A4 | A | oxide | Parser: locate Python decorator span handling and explain where decorator range is included | answer/patch references `src/parser.rs` or `src/languages/python.rs` decorator logic |
| B1 | B | py_repo | Subsystem `oxidepy/cache.py` known, find TTLCache expiry handling (file unknown → package known) | relevant file `oxidepy/cache.py` reached within ≤3 tool calls |
| B2 | B | ts_repo | Subsystem `src/net/` known, locate retry/backoff implementation | hits `src/net/retry.ts` or `src/net/client.ts` |
| B3 | B | oxide | Subsystem `review` known, find diff→changed symbols logic | hits `src/review.rs` |
| C1 | C | py_repo | Exact file/line: rename `base_delay_ms` param in `oxidepy/retry.py:22` → requires no discovery | edit stays within that file, 0 OXIDE calls is ideal |
| C2 | C | ts_repo | Literal lookup: find exported `Button` component with known path hint `src/ui/Button.tsx` | direct read, 0 OXIDE ideal |
| C3 | C | oxide | Typo/known file: fix doc typo in `src/mcp.rs:13` instruction string | direct read, 0 OXIDE ideal |

Shaping: A tests discovery→edit, B tests subsystem narrowing, C tests avoidance. Each task run with prompt variant that either withholds file path (A/B) or provides it (C), without leaking OXIDE output.
