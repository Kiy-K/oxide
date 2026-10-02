# @oxide/client

A small typed client for OXIDE: the native addon in this process when it is
installed, otherwise the `oxide` binary's `--json` process boundary (#36).
Private; not published.

```ts
import { Oxide, OxideError } from "@oxide/client";

const oxide = new Oxide({ cwd: "/path/to/repo" }); // native if available, else `oxide` on PATH
oxide.backend;                                       // "native" | "process"
oxide.fallbackReason;                                // why auto chose process, if it did
await oxide.index();                                 // IndexResult
await oxide.status();                                // StatusResult
await oxide.search("refresh token", { limit: 5 });   // SearchResult (Evidence[])
await oxide.searchLiteral("TODO(", { limit: 20 });    // LiteralSearchResult
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
  - `native`: the native backend's addon could not load, or it rejected a
    call without an answer (for example, a negative `limit`).

  `stderr` and `exitCode` are kept for diagnostics only.

The client never parses CLI prose.

## Scope

- `backend` chooses where calls run, once, at construction:
  - `"auto"` (the default) prefers native and falls back to process when the
    addon cannot load or `binary`, `env` or `discover` is given. The fallback
    is explicit: `oxide.backend` says which backend runs and
    `oxide.fallbackReason` says why. A failing native call is never retried
    on the process backend.
  - `"native"` requires the addon and throws if it cannot load.
  - `"process"` always spawns.
- The process backend runs one `oxide <command> --json` process per call
  (`src/process.ts`).
- The native backend serves calls in this process through the
  `@oxide/native` addon (`src/native.ts`, `packages/native`). It keeps the
  index snapshot and embedding model warm between calls, returns the same JSON
  and error envelopes, and is validated the same way.
  - Linux x64 and macOS arm64, built with `mise run native:build`.
    `@oxide/native` is an optional dependency, so a process-only install can
    omit it and `auto` then uses the process backend.
  - It reads this process's environment, so `binary`, `env` and `discover`
    are rejected with it.
  - Numbers are in `docs/ts-client-spawn-overhead/README.md`.
- `src/backend.ts` is the internal seam both backends implement.
- `searchLiteral` runs `search --mode literal`. `query({ git: true })` passes
  `--git`; its extra `git` object is not modeled and passes through.
  `new Oxide({ cwd, discover: true })` lets oxide find the repository from
  `cwd` instead of targeting `cwd` exactly.
- Not offered yet: `review`, `watch`, `setup`.
- No SQLite access and no daemon.

## Tests

- `test/`: unit tests against `test/fake-oxide.mjs`, which replays the
  committed `fixtures/protocol/` output. They run in `verify:ts`, with no Rust
  needed.
- `test/integration/`: integration tests against the real binary and the
  native addon, with one suite (`suite.ts`) run over each backend. Run them
  with `mise run ts:integration` (`$OXIDE_BIN`, default
  `target/release/oxide`); they are also part of `mise run verify` and CI's
  client-integration job.
