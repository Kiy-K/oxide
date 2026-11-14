# Changelog

Release notes for the `oxide-agent` package. The import package and the command are `oxide`.

## 0.2.0rc1 - 2026-11-14

Release candidate. Install it with the exact pin, `oxide-agent==0.2.0rc1`.

### Breaking changes and upgrade steps

1. Rebuild every existing index. Stores written by 0.1.1 are not readable. Any command on one fails with `error[incompatible_index]` and exit code 2 (`built by store schema 'oxide-ladybug-py-v3'; re-index`). Run `oxide index path/to/repo` once per repository. The context payload format is now `oxide-payload-v4`, which merges whitespace-adjacent views into one item, so payload text differs from 0.1.1 even for the same query.
2. The MCP server holds the store while it runs. It keeps one repository handle per repository until shutdown, so `oxide index` on the same store fails with `ownership_conflict`. Stop the server before you index. `oxide outline`, `oxide inspect` and `oxide refs` still read a running owner's snapshot. The server keeps the index fresh on its own (see item 4).
3. A blank `query` is `invalid_argument` in `search` and `build_context` and over MCP, unless `paths`, `symbols`, `changed` or `selection` supplies a seed. MCP `context` text can now include `# hint` lines for hints that matched nothing.
4. `open_repository` on an existing index now refreshes it and starts a background watcher, using the new dependency `watchfiles`. Filesystem events trigger updates, and `close()` stops and joins the watcher. `create=False` still never creates a store.
5. `SearchResult` and `ContextBundle` have a new `blast` field. It has a default, but it sits before `versions` in `ContextBundle`, so code that builds these dataclasses positionally must adapt.
6. `OxideError` has a new code, `resource_exhausted`, for a full read buffer pool. The index is intact in that case. Code that matches on every `code` must handle it.
7. macOS Intel is no longer supported. `cryptography>=50` applies on every platform, and `fastmcp` is pinned to 4.1.2.

### Added

- `oxide status` and `oxide.repository_status`, which return a `RepositoryStatus` for the last published snapshot without touching the worktree.
- `oxide impact`, the command-line form of `Repository.impact`.
- `oxide outline`, `oxide inspect` and `oxide refs`, with `Repository.outline`, `inspect` and `refs`.
- `open_repository(..., shared=True)`, which falls back to a read-only handle on the running owner's latest snapshot instead of raising `ownership_conflict`.
- A precomputed blast radius (`blast`) for each entry point in `search` and `context` results.
- Automatic index freshness tracking while a handle is open.
- A threat model in `SECURITY_MODEL.md`.

### Changed

- Indexing is faster. The lexical index is built in its own database and reused when its documents are unchanged. Graph rows are streamed to CSV. Reconciliation no longer rereads unchanged bytes. The Python resolver indexes scope declarations by name.
- `search` is faster.
- Package metadata now has classifiers and Changelog and Issues links.

### Fixed

- Repeated `impact` calls no longer exhaust the read buffer pool (#55).
- A full read buffer pool is reported as `resource_exhausted`, not `store_corrupt`.
- A failed background watcher restarts with backoff and resyncs after the gap.
- On Windows, a file rewritten with its modification time restored is no longer served from the capture cache. The cache key now includes NTFS ChangeTime.
- A hard-linked lexical index is no longer flushed again at seal.

### Platforms and dependencies

Linux x86_64, macOS 15 or later (arm64) and Windows x86_64 on CPython 3.12 to 3.14. Windows still needs the OpenSSL 3 x64 DLLs and ASCII store paths ([platforms](https://github.com/Kiy-K/oxide/blob/main/docs/platforms.md)).

### Known issue

A long-lived `Repository` or MCP server accumulates read buffer pool use. If you see `resource_exhausted`, close and reopen the handle (restart the server).

## 0.1.1

See the git tag [`v0.1.1`](https://github.com/Kiy-K/oxide/tree/v0.1.1).
