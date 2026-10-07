# LadybugDB feasibility spike (Phase 2A)

Evidence for [ADR-0002](../../../docs/adr/0002-ladybugdb-knowledge-store.md).
This is a throwaway Cargo workspace, not an adapter. It is not a member of
`oxide_kernel/`, is not run by `mise run verify`, and nothing in `kernel/` or
`runtime/` may depend on it.

```bash
mise run spike:ladybug   # fetches the pinned archive + extensions once (network), then runs offline
```

On the development laptop it was run under
`systemd-run --user --scope -p MemoryMax=2G -p MemorySwapMax=0 -p CPUQuota=200%`
and `unshare -rn` (no network namespace). Every database uses a 64 MiB buffer
pool and 2 threads (`config()` in `tests/feasibility.rs`).

## Inspected versions (2026-10-07)

| Item | Version / digest |
| --- | --- |
| Crate | `lbug` **0.21.2** (crates.io, 2026-10-01, MIT, `rust-version` 1.81), source `github.com/LadybugDB/ladybug-rust` |
| Native library | GitHub release `LadybugDB/ladybug` **v0.21.2**, `liblbug-static-linux-x86_64-compat.tar.gz`, sha256 `60147b2a…a6a3f` (full digest in `fetch-liblbug.sh`) |
| Storage format | `StorageVersionInfo` 47 (0.20.0–0.21.2); readable: 40–47 |
| Extensions | extension version **0.21.0** (`LBUG_EXTENSION_VERSION`, shared by all 0.21.x), `libfts`/`libvector.lbug_extension` for `linux_amd64`, sha256 pinned in `fetch-liblbug.sh` |
| Toolchain | Rust 1.98.0 (repo pin), GCC 16.2.1, Fedora 44 x86_64, glibc, OpenSSL 3.5 |
| Docs read | [concurrency](https://docs.ladybugdb.com/concurrency/), [transactions](https://docs.ladybugdb.com/cypher/transaction/), [extensions](https://docs.ladybugdb.com/extensions/), [vector](https://docs.ladybugdb.com/extensions/vector/), [FTS](https://docs.ladybugdb.com/extensions/full-text-search/), crate docs/source of 0.21.2 |

Release cadence observed: 0.20.0 → 0.21.2 in 33 days, three 0.21.x patches
in three days. Storage format moved 40 → 47 across 0.17–0.20.

## Results

V = verified by a test in `tests/feasibility.rs` at the pin. D = documented
only. S = read in crate/library source. U = unknown or not verified.

| Area | Finding | Basis |
| --- | --- | --- |
| Rust API | `Database`, `Connection<'db>` (borrows the database), `prepare`/`execute` with typed `Value` params, `QueryResult` iterator of `Vec<Value>`. `Database` and `Connection` are `Send + Sync` (C++-side synchronization). No async API | S, V |
| Embedded model | In-process C++ engine via `cxx`; on-disk directory or `:memory:` | S, V |
| Persistence/reopen | Data written, `Database` dropped, reopened: identical bounded adjacency | V |
| Graph operations | Node/rel tables with PK; a rel table with two FROM/TO pairs (`Entity→Entity`, `Entity→Unresolved`); `ORDER BY … LIMIT` adjacency | V |
| Mutation | `SET`, edge `DELETE`, `DETACH DELETE`; plain `DELETE` of a node with edges fails; duplicate PK rejected | V |
| Dangling endpoints | `MATCH … CREATE` with a missing endpoint creates nothing **and reports no error**; adapters must check endpoints themselves | S (Cypher semantics), not separately tested |
| Transactions | `BEGIN TRANSACTION` / `COMMIT` / `ROLLBACK`; DDL inside a transaction rolls back; **any failed statement aborts the whole manual transaction** (a later `COMMIT` says "No active transaction") | V |
| Crash safety | Child process `abort()`s mid-transaction: committed row present, uncommitted row absent after reopen | V |
| Write concurrency | One write transaction per `Database`; a second **fails immediately** ("Only one write transaction at a time is allowed"), it does not wait. `SystemConfig::enable_multi_writes` exists in the Rust API but is undocumented | V; multi-writes U |
| Read isolation | A `BEGIN TRANSACTION READ ONLY` transaction keeps its snapshot across another connection's commit | V |
| Ownership, other process | A second read-write open fails ("Could not set lock on file"). A **read-only open succeeds** while a read-write owner holds the file, contrary to the docs' "one READ_WRITE or many READ_ONLY" | V |
| Ownership, same process | A second read-write `Database` on the same directory **is not refused** | V |
| Cancellation | `set_query_timeout(ms)` and `Connection::interrupt()` both stop a running query with "Interrupted"; the connection stays usable. A 200 ms timeout fired after about 385 ms in a debug build. Interrupting a write transaction (rollback?) was not tested | V; write interruption U |
| Memory | Default `buffer_pool_size` is derived from host RAM (crate docs: about 6 GB resident on a 15 GiB host); explicit 64 MiB worked for every test | S, V |
| Bulk load | Crate docs: per-row `CREATE`/`MERGE` costs tens of ms of CPU; use `COPY FROM` files or the `arrow` feature | D (crate), U for OXIDE scale |
| FTS | Not in the core library. `LOAD fts` fails offline ("has not been installed"). Loaded from the pinned file: BM25 index maintained on insert and delete; case-insensitive; `parse_config` is split on `_`. camelCase splitting and result order with `TOP` are not verified (docs: "return order not guaranteed") | V; rest U |
| Vector | Same extension model. Loaded from the pinned file: cosine HNSW, top-K changes correctly after insert and delete. Recall, determinism and filtered (projected-graph) search not tested | V; rest U |
| Extension install | `INSTALL` downloads `https://extension.ladybugdb.com/v<ext-version>/<platform>/<name>/lib<name>.lbug_extension` into `~/.lbdb/extension/…`; no checksum or signature check found in the 0.21.2 source; files are mutable at a URL | S |
| Extension loading | `LOAD EXTENSION '<path>'` works offline only if the host binary exports symbols: the spike's `build.rs` adds `-rdynamic` (lbug's own build.rs flag does not reach dependents). Windows/MSVC differs per crate docs, untested | V (Linux) |
| Default build | `lbug`'s build.rs, with no env set, runs `scripts/download_lbug.sh`, which **fetches `download-liblbug.sh` from `LadybugDB/ladybug@main` and runs it**; that links the **latest** release, not the crate's version. Falls back to a CMake C++20 source build of the vendored 45 MB source | S |
| Pinned build | `LBUG_LIBRARY_DIR`/`LBUG_INCLUDE_DIR` pointing at the sha256-checked archive ("external" mode): no build-time network, links statically | V |
| Source build | `LBUG_BUILD_FROM_SOURCE=1` builds the vendored C++ (offline, needs CMake + C++20). Deliberately not run on this laptop (memory/CPU) | S, U |
| Linked artifact | Test binary 52 MB debug, 24 MB stripped. Dynamic deps: libstdc++, libssl.so.3, libcrypto.so.3, libz, glibc | V (Linux x86_64) |
| Platforms | Release assets exist for Linux x86_64/aarch64 (static compat/perf, about 30 MB), macOS arm64/x86_64 (about 24 MB), Windows x86_64/arm64 (static zips about 240–280 MB). Only Linux x86_64 was built and run | V (assets listed), U (others) |
| License | `lbug` crate MIT; `LadybugDB/ladybug` repository MIT (GitHub license API); bundled third-party libraries (antlr4, re2, parquet/thrift, zstd, lz4, brotli, mbedtls, simsimd, …) carry their own licenses; inventory before redistribution | S, U (full inventory) |
| Docker/Explorer | Docs: host file locks are not visible inside Docker (Explorer) | D |

## Not tested

Disk-full and corrupt-file recovery; storage-format upgrade across versions;
crash while `CHECKPOINT` runs; behavior on network or case-insensitive
filesystems; `COPY FROM` throughput at repository scale; ANN recall versus
exact search; macOS and Windows builds.
