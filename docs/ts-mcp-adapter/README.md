# TS MCP reference adapter vs Rust `oxide mcp` (#36 T3)

**Verdict:** keep the Rust `oxide mcp` canonical. The TS adapter
(`@oxide/mcp`) reaches full behavioral parity on the tested surface, and
`deno compile` packages it into a working standalone stdio server. But it is
slower on every axis measured, because each tool call spawns `oxide`, which
reloads the native embedder. Its server process alone uses more memory (1.4×
the Rust server with the native embedder, 4.7× with hashed), before counting
the per-call `oxide` children. Its packaging is also more complex. It stays a reference implementation and conformance
check, not a replacement.

## Parity (measured)

`packages/mcp/test/integration/parity.test.ts` sends identical requests to all
three servers on one indexed `fixtures/py_repo` (hashed embedder). All 20
checks pass for both the Node-run adapter and the Deno binary:

- `initialize` (protocol version, capabilities, instructions, server name)
  and `tools/list`.
- 17 tool calls: query (default, budget plus blast radius plus a padded,
  mixed-case `profile`, `git`), search (default, options, literal, empty
  literal, literal without an index), `index_missing`, `repository_not_found`
  (including a `path` starting with `-`), and six malformed-argument or
  unknown-tool protocol errors.

Known differences, outside the tested surface:

- **Server version:** `serverInfo.version` is the adapter's package version,
  not the crate version. This is deliberate.
- **Integer arguments:** JSON numbers become JS doubles, so the adapter accepts
  `limit: 5.0` (Rust rejects it as a non-integer). It also rejects integers
  above 2^53, which Rust accepts.
- **Null or non-object `arguments`:** the SDK's request schema rejects
  `arguments: null` or a non-object before the adapter runs, with `-32602` and
  the SDK's own message. Rust treats `null` as `{}` and a non-object as
  `-32601`.

None of these occur in normal agent use. The review that found them
(`arguments: null`, `5.0`, a dash-leading `path`) also led to a client fix:
explicit repository paths are now passed as `--path=<repo>`, or after `--`, so
a path starting with `-` reaches oxide as a path.

## Performance (measured)

- Command: `OXIDE_BIN=$PWD/target/release/oxide node packages/mcp/bench/compare.ts [hashed|native]`.
- Setup: a copy of this repo's `src/` (94 files, 1,429 symbols), i7-13620H,
  Node v24.21.0, Deno 2.9.7, release `oxide`. Median / p95 of 30 calls after
  3 warm-up calls; startup is 10 fresh processes.
- Peak RSS is the server process only. The TS servers' per-call `oxide`
  children are not included, and each of those also loads the embedder.

### Hashed embedder

| server | startup ms | search ms | query ms | peak RSS |
|---|---|---|---|---|
| Rust `oxide mcp` | 3.8 / 4.0 | 3.3 / 4.9 | 3.2 / 4.9 | 22.6 MB |
| TS adapter, Node | 138.2 / 150.2 | 16.1 / 21.3 | 19.5 / 26.7 | 107.5 MB |
| TS adapter, Deno binary | 85.6 / 94.1 | 14.1 / 22.1 | 16.9 / 29.6 | 104.4 MB |

### Default native embedder (arctic-embed-xs-q, cached)

| server | startup ms | search ms | query ms | peak RSS |
|---|---|---|---|---|
| Rust `oxide mcp` | 5.0 / 6.4 | 6.9 / 10.5 | 7.5 / 10.8 | 79.2 MB |
| TS adapter, Node | 144.4 / 161.4 | 105.9 / 114.0 | 104.8 / 120.9 | 111.7 MB |
| TS adapter, Deno binary | 80.7 / 85.8 | 99.3 / 109.4 | 100.9 / 112.9 | 104.0 MB |

The ~100 ms per call with the native embedder repeats T2's spawn-overhead
finding (`docs/ts-client-spawn-overhead/README.md`). The Rust server keeps the
model and corpus snapshot warm across calls; one process per call cannot.

## Size and packaging

| | artifact | size | runtime needed |
|---|---|---|---|
| Rust | `target/release/oxide` (also the CLI) | 55.4 MiB | none |
| Node | `src/main.ts` + `node_modules` (SDK 6.4 MB, core 1.4 MB, zod 8.1 MB) | — | Node ≥ 24 (120.7 MiB binary) |
| Deno | `dist/oxide-mcp` | 113.1 MiB (about 13 MB embedded JS, the rest the Deno runtime) | none, but `oxide` must be on `PATH` |

Packaging complexity, in increasing order:

- **Rust:** none beyond the existing release.
- **Node:** a pnpm install plus Node 24 on the target.
- **Deno:** a pinned Deno and `deno compile`. Measured setup was needed to get a
  working binary:
  - **pnpm layout:** under pnpm's `node_modules` layout, `deno compile`
    embedded all of `node_modules`, dev tools included, but missed the SDK's
    transitive `@modelcontextprotocol/core`. The binary failed at startup.
  - **Fix:** managed npm (`--node-modules-dir=none --exclude-unused-npm`),
    which re-resolves packages from the registry at compile time, and a direct
    `@modelcontextprotocol/core` dependency.
  - **Workspaces:** Deno also wrote a `"workspaces"` field into the root
    `package.json` that must mirror `pnpm-workspace.yaml`.
  - **Lockfile:** a second lockfile, `deno.lock` (seeded from
    `pnpm-lock.yaml`), makes the compile reproducible with
    `--frozen-lockfile`. It must be refreshed with
    `deno install --lockfile-only` whenever dependencies change; a stale lock
    fails the compile (measured).
  - **Network:** compiling needs network access, and Deno refuses npm releases
    younger than a day.
  - **Permissions:** the baked `--allow-env --allow-run=oxide` is minimal (no
    read, write or net), but it ties the binary to the `oxide` on `PATH`.

## Deno stdio compatibility (measured)

The official SDK's stdio transport (`serveStdio`, `process.stdin`/`stdout`)
works unchanged under Deno's Node compatibility layer, both under `deno run`
and in the compiled binary. No transport code was rewritten.

The two Deno-specific findings are both permission-related:

- **Env access:** spawning a child needs `--allow-env`, because
  `node:child_process` copies `process.env`.
- **Run access:** `--allow-run=oxide` also accepts an absolute path that
  resolves to the same `oxide`.

## Not pursued

- `deno compile --bundle` (experimental) was not tried, per the task.
- No startup optimization, daemon or N-API (T5 and the native-backend
  evaluation are separate, evidence-gated decisions).
