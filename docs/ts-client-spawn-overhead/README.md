# @oxide/client process-spawn overhead (#36 T2)

This doc measures what one-process-per-call costs, so any daemon or N-API
proposal starts from data (#36 T2 acceptance; T5 gate). The transport has not
been optimized.

## Setup

- Command: `OXIDE_BIN=$PWD/target/release/oxide node packages/client/bench/spawn.ts [hashed|native]`
- Script: `packages/client/bench/spawn.ts`. Each row is the median and p95 of
  30 runs after 3 warm-up runs.
- Machine: Intel i7-13620H (13th gen hybrid, 16 threads), Linux, Node
  v24.21.0, release build at `dd24f58` plus this change. The Rust code is
  unchanged.
- Repositories: copies of `fixtures/py_repo` (9 files, 55 symbols) and of
  this repo's `src/` (94 files, 1,429 symbols), indexed with the embedder
  under test.
- Rows:
  - **process floor**: `oxide --version`, which is spawn plus exit with no
    work.
  - **client.\***: a full `Oxide` call, which is spawn, the command's work,
    and parse plus validate.
  - **MCP, warm process**: the same search or query over one long-lived
    `oxide mcp` session. It approximates what a persistent backend could
    reach, and the bench fails if a call errors.
  - **parse + validate**: `JSON.parse` plus the `@oxide/protocol` schema, on
    that command's real output.

## Results (measured, ms, median / p95)

### Hashed embedder (`OXIDE_EMBED_NATIVE=hashed`, no model)

| call | py_repo | oxide `src/` |
|---|---|---|
| process floor | 3.3 / 5.0 | 3.6 / 6.6 |
| client.status() | 9.5 / 10.7 | 22.8 / 32.2 |
| client.search() | 8.2 / 9.8 | 16.6 / 22.7 |
| client.query() | 9.6 / 11.5 | 15.8 / 33.3 |
| MCP search, warm | 1.5 / 2.3 | 2.7 / 5.2 |
| MCP query, warm | 0.9 / 1.0 | 3.1 / 3.7 |
| parse + validate (search / query) | 0.1 / 0.1 | ≤ 0.1 |

### Default native embedder (arctic-embed-xs-q, weights already cached)

| call | py_repo | oxide `src/` |
|---|---|---|
| process floor | 5.3 / 6.7 | 4.6 / 6.4 |
| client.status() | 9.4 / 11.6 | 23.1 / 30.8 |
| client.search() | 88.2 / 93.7 | 92.6 / 107.7 |
| client.query() | 89.1 / 110.5 | 96.9 / 110.3 |
| MCP search, warm | 6.5 / 8.1 | 6.2 / 10.2 |
| MCP query, warm | 6.2 / 7.5 | 5.8 / 7.3 |
| parse + validate (search / query) | 0.1 / 0.1 | ≤ 0.1 |

Output sizes are 9–19 KB for search and 5–13 KB for query. Repeated hashed
runs moved medians by a few ms (for example, py_repo `client.search()` went
5.1 then 8.2), so treat differences under ~5 ms as noise on this hybrid CPU.

## Reading

- **The process floor (3–6 ms) and client-side validation (≤ 0.2 ms) are
  small.** JSON serialization over the pipe is not the cost.
- **With the shipped default embedder, each search or query call spends
  about 80–90 ms on work that a warm process does once.** That's ~90 ms per
  spawned call against ~6 ms warm, about 15×, measured. Inferred cause: the
  per-process model load, which matches roadmap #9's "model-load dominated
  (~70–130 ms)" one-shot native calls. With the hashed embedder the gap is
  about 5–15 ms per call.
- **`status` pays for hashing every tracked file on each call** (about 23 ms
  on `src/`, the same with either embedder). That's command work, not spawn
  overhead, and a persistent process would not remove it as written.
- **For one-shot CLI-style use, the process client is adequate.** For
  interactive editor use (many searches per session), about 90 ms per query
  is material. That is the evidence a future persistent-backend proposal (T5,
  or a napi-rs backend behind the same `Oxide` API) would need. Neither is
  built here.
