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
- `mise run native:build` builds `oxide_native.<platform>.node` for this host:
  `linux-x64-gnu` or `darwin-arm64`. `index.cjs` loads the file named for the
  running platform and throws on a platform with no build.
  `mise run lint:native` runs fmt and clippy.
- This is its own Cargo project, not a member of the root crate. The build
  remaps this machine's paths (Cargo home, toolchain, repo) out of the
  artifact and fails if any remain. The prebuilt ONNX Runtime it links keeps
  its own upstream build paths (`/home/runner/work/ort-artifacts/...`), as
  the CLI binary does. The remap flags differ from the root build's, so it
  builds under `target/native/`.
- `Cargo.lock` was seeded from the root lock. After a root dependency change,
  run `cp Cargo.lock packages/native/ && cargo metadata --format-version 1 --manifest-path packages/native/Cargo.toml >/dev/null`. It adds napi's entries and keeps every shared version.
- Release assets (#36 P1/P2): on its `native: true` targets (x86_64 Linux
  on `ubuntu-24.04`, Apple Silicon macOS on `macos-15`), `release.yml`
  builds the addon with `native:build`, packs it with
  `scripts/native_package.sh` as `oxide-native-<version>-<target>.tar.gz`
  (npm-pack layout, release version; system-library-only dependencies, no
  RPATH/LC_RPATH, glibc or minimum-macOS floor recorded), and gates the
  upload on
  `scripts/native_smoke.sh`: the committed client suite from a clean install
  with no Rust on PATH, the default embedder, and a process-only install.
  Nothing is published to npm. Locally: `mise run native:build`, then
  `scripts/native_package.sh v0.0.0-dev /tmp/out` and
  `scripts/native_smoke.sh /tmp/out/oxide-native-*.tar.gz target/release/oxide`.
