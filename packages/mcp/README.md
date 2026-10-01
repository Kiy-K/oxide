# @oxide/mcp

A reference TypeScript MCP server over `@oxide/client` (#36 T3), built on the
official SDK (`@modelcontextprotocol/server` 2.x, stdio). Private; not
published. **The Rust `oxide mcp` is canonical.** This adapter exists to keep
the client and protocol honest against a second implementation, and to
measure what a TS server costs (`docs/ts-mcp-adapter/README.md`).

## What it serves

The adapter serves the Rust server's surface verbatim: `serverInfo.name`,
`instructions` and `tools/list` (`query` and `search` with the same input
schemas and descriptions). The source is `fixtures/mcp/surface.json`, which
`tests/protocol_fixtures.rs` writes from the running Rust server, so the
schemas cannot drift. Regenerate it with `mise run protocol:fixtures`.

`src/tools.ts` mirrors `src/mcp.rs`:

- **Same argument checks, in the same order.** Malformed arguments return
  JSON-RPC `-32602` with the Rust message. Literal search does not read
  `profile` or `blast_radius`.
- **OXIDE failures** become `isError: true` results with
  `structuredContent: { error: { code, action, message } }`, the same JSON in
  `content[0].text`.
- **Success** returns the result as compact JSON text.
- **A binary that cannot run** is JSON-RPC `-32603`.
- **Omitting `path`** lets `oxide` discover the repository from the server's
  cwd. Passing it targets that repository explicitly.

Each call runs one `oxide` process. Nothing here retrieves, ranks or reads the
index.

## Running

```bash
OXIDE_BIN=/path/to/oxide node packages/mcp/src/main.ts   # Node 24 strips the types
mise run mcp:compile    # standalone executable: packages/mcp/dist/oxide-mcp
```

`OXIDE_BIN` defaults to `oxide` on `PATH`, and every other variable passes
through to `oxide`.

The compiled binary bakes in only `--allow-env --allow-run=oxide`: no file,
network or other subprocess access. `--allow-env` is needed because Deno's
`node:child_process` reads the whole environment to hand it to the child.

Consequence: the compiled binary can only run the `oxide` found on `PATH`. An
`OXIDE_BIN` pointing at any other file is refused by Deno's permission check.

## Packaging (`deno compile`)

- **Flags:** `--node-modules-dir=none --exclude-unused-npm --frozen-lockfile`,
  with no experimental `--bundle`.
- **Reproducible resolution:** the committed root `deno.lock` (seeded from
  `pnpm-lock.yaml`) pins every npm package and its integrity hash, and the
  compile fails if the lock is out of date. After changing any `package.json`
  dependency, refresh it with `deno install --lockfile-only` at the repository
  root (it touches only `deno.lock`, not `node_modules`).
- **What gets embedded:** Deno resolves the npm packages itself from the exact
  pins in `package.json` and embeds only those the entry point reaches: the
  SDK, its `core` package, zod, and the workspace packages' built JS (about 13
  MB). Under pnpm's `node_modules`, `deno compile` embedded the whole tree
  (dev tooling included) yet missed the SDK's transitive `core` package.
- **`@modelcontextprotocol/core` is a direct dependency** at the SDK's exact
  pinned version. Deno only discovers declared packages.
- **The root `package.json` `"workspaces"` was written by Deno** (it migrates
  `pnpm-workspace.yaml` on first run). It lets Deno resolve `workspace:*`
  members and must list the same globs as `pnpm-workspace.yaml`.
- **Compiling needs network access** (npm registry, plus the Deno runtime on
  first use), unless Deno's cache already holds the locked packages. Turbo
  never caches the `compile` task.

## Tests

- **`test/`:** the Node-run server over real stdio without Rust. It checks that
  `tools/list` and `instructions` equal the Rust surface, and that every
  malformed-argument case returns `-32602` with the Rust message.
- **`test/integration/parity.test.ts`:** the Rust server, the Node-run adapter
  and the compiled binary on one indexed repository. The same requests must
  produce the same results: protocol errors by code and message, tool results
  by `isError`, `structuredContent` and parsed payload. Run it with
  `mise run ts:integration`; it is also in `mise run verify` and CI's
  client-integration job.
