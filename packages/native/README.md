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
- `mise run native:build` builds `oxide_native.linux-x64-gnu.node`, for Linux
  x64 only. `index.cjs` loads the file named for the running platform and
  throws on a platform with no build.
  `mise run lint:native` runs fmt and clippy.
- This is its own Cargo project, not a member of the root crate. The build
  remaps this machine's paths (Cargo home, toolchain, repo) out of the
  artifact and fails if any remain. The prebuilt ONNX Runtime it links keeps
  its own upstream build paths (`/home/runner/work/ort-artifacts/...`), as
  the CLI binary does. The remap flags differ from the root build's, so it
  builds under `target/native/`.
- `Cargo.lock` was seeded from the root lock. After a root dependency change,
  run `cp Cargo.lock packages/native/ && cargo metadata --format-version 1 --manifest-path packages/native/Cargo.toml >/dev/null`. It adds napi's entries and keeps every shared version.
