# @oxide/client

A small typed client over the `oxide` binary's `--json` process boundary
(#36 T2). Private; not published.

```ts
import { Oxide, OxideError } from "@oxide/client";

const oxide = new Oxide({ cwd: "/path/to/repo" }); // binary defaults to `oxide` on PATH
await oxide.index();                                 // IndexResult
await oxide.status();                                // StatusResult
await oxide.search("refresh token", { limit: 5 });   // SearchResult (Evidence[])
await oxide.query("fix the retry bug", { budgetTokens: 4000 }); // ContextResult
```

Result types come from `@oxide/protocol`, and every answer is validated
against it before it is returned.

## Errors

- **`OxideError`**: OXIDE reported a failure through its JSON error envelope.
  Branch on `code` (open-ended; `index_missing`, `no_source_files`, …) or on
  `action` (`index`, `repair`, `retry`, `fall_back`, `stop`). `message` is for
  humans.
- **`OxideClientError`**: there was no valid answer, and `reason` says why:
  - `spawn`: the binary could not start.
  - `exit`: it ended without a JSON answer, such as a usage error with exit
    status 2 or a signal.
  - `invalid-output`: it answered with JSON that `@oxide/protocol` rejects.

  `stderr` and `exitCode` are kept for diagnostics only.

The client never parses CLI prose.

## Scope

- One `oxide <command> --json` process per call (`src/process.ts`).
- `src/backend.ts` is the internal seam: a future in-process or persistent
  backend would implement it without changing `Oxide`. Nothing like that
  exists yet; see the spawn-overhead numbers in
  `docs/ts-client-spawn-overhead/README.md`.
- Not offered yet: `search --mode literal` (different shape), `query --git`
  (unmodeled output), `review`, `watch`, `setup`.
- No SQLite access, no daemon, and no native bindings.

## Tests

- `test/`: unit tests against `test/fake-oxide.mjs`, which replays the
  committed `fixtures/protocol/` output. They run in `verify:ts`, with no Rust
  needed.
- `test/integration/`: integration tests against the real binary. Run them
  with `mise run ts:integration` (`$OXIDE_BIN`, default
  `target/release/oxide`); they are also part of `mise run verify` and CI's
  client-integration job.
