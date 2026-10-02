# @oxide/native

The Node-API addon behind `@oxide/client`'s `backend: "native"` (#36). Private;
not published.

- `src/lib.rs` binds `RepositoryService` with the process cache, the warm path
  `oxide mcp` uses. It exposes one `Repository` class with `status`, `index`,
  `search`, `searchLiteral` and `query`, and nothing below the service.
- Each call resolves to `{ ok, json }`. `json` is what `oxide <command> --json`
  prints: the result, or the `{error:{code,action,message}}` envelope. Calls
  run on libuv's worker pool.
- Defaults and flag validation mirror the CLI. `@oxide/client`'s integration
  suite runs over both backends to keep them in parity.
- `mise run native:build` builds `oxide_native.node`, for Linux x64 only.
  `mise run lint:native` runs fmt and clippy.
- This is its own Cargo project, not a member of the root crate. It builds
  into the root `target/` so it reuses the root release artifacts.
- `Cargo.lock` was seeded from the root lock. After a root dependency change,
  run `cp Cargo.lock packages/native/ && cargo metadata --format-version 1 --manifest-path packages/native/Cargo.toml >/dev/null`. It adds napi's entries and keeps every shared version.
