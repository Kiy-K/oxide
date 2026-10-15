# OXIDE public Python API

The public API is what `oxide` (`src/oxide/__init__.py`) exports:
`open_repository`, `index_repository`, `Repository`, the result types in
`oxide.models` and `OxideError`. Integrations (the CLI, future MCP and editor
adapters, SDK users) depend on these only, never on `oxide.core` or
`oxide.storage`. Decision: [ADR-0011](adr/0011-python-engine-vectorless.md).
The TypeScript API this replaces is recoverable at `f79808a`.

```python
import oxide

with oxide.open_repository("/work/repo") as repo:   # owns the store until closed
    report = repo.index()                           # IndexReport: "built" | "unchanged"
    hits = repo.search("where failed requests are retried", limit=20)
    bundle = repo.build_context(
        "where failed requests are retried",
        budget=1024,                                # oxide-units-v1
        symbols=["RetryPolicy"],                    # optional typed hints
    )
    print(bundle.payload)                           # agent-facing context, used <= budget
```

The data location is `data_dir=`, else `$OXIDE_DATA_DIR`, else
`$XDG_DATA_HOME/oxide/py` (or `~/.local/share/oxide/py`). It is never captured as
source, even inside the repository: the data directory and its `repos/` are
excluded by device and inode, so symlinked or differently spelled paths to it are
excluded too. Lexical search needs the pinned FTS extension: run `oxide prepare`
once.

## Operations

| Operation | Returns | Notes |
| --- | --- | --- |
| `open_repository(path, *, data_dir=None)` | `Repository` | Takes the store's exclusive lock; a second owner in any process gets `OwnershipConflict` |
| `Repository.index()` | `IndexReport` | Capture → derive → publish a new generation atomically; `unchanged` when snapshot and derivation are already current |
| `Repository.search(query, *, limit, paths, symbols, selection, changed)` | `SearchResult` | Entry points for inspection: seeds in hint order, then BM25 rank; never final context |
| `Repository.build_context(query, budget, *, paths, symbols, selection, changed)` | `ContextBundle` | Retrieval → TreeRouter → deterministic selection → packing |
| `Repository.entity(key)` / `relations(key)` / `files()` | `Entity \| None`, `list[Relation]`, `list[FileInfo]` | Typed graph and coverage inspection |
| `Repository.snapshot` | `Snapshot` | `repo_id`, `snapshot_id`, `derivation_id` of the published generation |
| `index_repository(path, *, data_dir=None)` | `IndexReport` | Open, index, close |
| `Repository.close()` | — | Releases the read view, its native connection and the lock; also on `with` exit. Idempotent. A repository dropped without `close()` releases them when garbage-collected |

Hints are typed query context: `paths` and `changed` resolve exactly, then by a
unique case-folded match; `symbols` are names or dotted suffixes
(`AuthService.refresh_token`); `selection` is `(path, start_byte, end_byte)` and
seeds the innermost declaration. Unmatched hints come back as `HintIssue`s.

## Results

Every result is a frozen dataclass naming the `Snapshot` it was read from.

- `Entity`: `key`, `category` (`repository`/`module`/`file`/`symbol`), `kind`,
  `name`, `signature`, `source: SourceRef | None`, `test`. Keys are
  deterministic and snapshot-local (ADR-0010): `repo`, `module:python:a.b`,
  `file:<path>`, `symbol:<path>#Outer.inner~1`.
- `SourceRef`: `path`, half-open byte `start`/`end`, file `digest`
  (`sha256:` of the captured bytes).
- `Relation`: `kind` (`contains`, `contains_logical`, `defines`, `references`,
  `calls`, `imports`, `tested_by`), `source`, `target` (None when unresolved),
  `resolution` (`resolved`/`ambiguous`/`unresolved`), `basis`, `name`, `site`
  (shared by the rows of one ambiguous relation), `evidence`.
- `SearchResult`: `entries` (`EntryPoint`: entity, `lexical_rank`,
  `lexical_score`, `seeds`), `lexical` (`ChannelStatus`: `ran` with the scorer
  name, or `disabled`/`unavailable` with a reason, never a zero score), `hints`.
- `ContextBundle`: `items` (`ContextItem`: `source`, `lines`, `entities` with
  `complete` and `reason`, `units`, `text`), `payload`, `budget`, `used`,
  `units` (`oxide-units-v1`), `omissions` (stage and reason), `route`
  (`RouteSummary`), `lexical`, `hints`, `degradations`, `versions`.

## Arguments

Checked before any query is built. A violation is `InvalidArgument`, which is both
an `OxideError` (`invalid_argument`) and a `ValueError`:

- `limit`: an `int` in `[0, 1000]` (`api.MAX_LIMIT`); `0` returns seeds only.
- `budget`: an `int` in `[0, 1048576]` units (`api.MAX_BUDGET`).
- `query`: a `str`. `paths`, `symbols`, `changed`: iterables of `str`, never a
  bare `str`. `selection`: a `(str, int, int)` tuple with
  `0 <= start <= end < 2**63`.

## Errors

`OxideError` subclasses carry a stable `code`:

| Code | Raised when |
| --- | --- |
| `invalid_path` | The repository path is missing or not a directory, or a store/extension path is not UTF-8 |
| `invalid_argument` | See § Arguments |
| `ownership_conflict` | Another `Repository` in any process owns the store |
| `not_indexed` | A read before the first `index()` |
| `incompatible_index` | The current generation was built by another derivation (component versions, including `ladybug`) or store schema. Checked from the manifest before the database is opened. `index()` rebuilds |
| `store_corrupt` | Damage (below). Reads fail closed |
| `capture_conflict` | The worktree kept changing during capture, or a `.gitignore` is unreadable |
| `capture_limit` | More than 1 GiB in scope |
| `closed` | Use after `close()` |

Damage is classified like this:

- **Derived state is rebuilt by `index()`**, because the repository identity
  (`repo.json`) and the source are still established. This covers an
  unreadable, truncated, empty, missing or foreign `graph.lbug`, a malformed or
  foreign `MANIFEST.json`, and a missing or digest-mismatched retained source
  file. Reads raise `store_corrupt` until then, and `index()` never reports
  such a generation `unchanged`.
- **Ambiguous state fails closed at `open_repository`, and nothing is deleted or
  rewritten.** This covers a malformed `repo.json`, a missing `repo.json` while
  a `CURRENT` exists, a `CURRENT` that is unreadable or not a generation name,
  and a `CURRENT` naming a generation that is absent (with no crash record) or has no `MANIFEST.json`. The
  message names the store directory to remove by hand.
- **A crash inside publication** keeps the previous generation before `CURRENT`
  moves and the new one after. A crash between the two renames of a same-name
  rebuild is undone on the next open, and `index()` then rebuilds. All of these
  windows are crash-injection tested.

## Lifecycle and concurrency

- A `Repository` owns one store until `close()`, `with` exit or garbage
  collection. Its finalizer holds only the lock descriptor, never the
  repository, so dropping the last reference releases the lock.
- One owner per store across processes (`flock`). An MCP server or any other
  long-running client should hold one handle for its lifetime and close it on
  shutdown. While it runs, the CLI gets `ownership_conflict` for that
  repository.
- A `Repository` is not thread-safe, and neither is the native binding.
  Serialize calls.

## Platform and native limitations

- The FTS extension is pinned for Linux x86_64 only. Elsewhere the lexical
  channel reports `unavailable`. Atomic publication and the crash tests are
  verified on Linux only.
- The pinned binding segfaults when it re-runs a parameterized
  `QUERY_FTS_INDEX`, so the FTS query is a literal built only from OXIDE terms
  (`[a-z0-9 ]`). Search text can never reach it unsanitized.
- Store and extension paths may contain `'`, `\`, spaces, control and non-ASCII
  characters (escaped in the two literal paths, `COPY` and `LOAD EXTENSION`).
  Paths that are not valid UTF-8 are `invalid_path`.
- Indexing is a full rebuild per change. Capture holds every captured file in
  memory: at most 1 GiB in scope, 1 MiB per file, and peak RSS was about 1.0×
  the captured bytes plus ~30 MiB for capture alone. Derivation adds per-entity
  memory: 239 MiB peak for a 14 MiB stdlib. Measurements are in
  `docs/migration-audit/README.md`.

## CLI

`oxide index|search|context <path> [...] [--data-dir D] [--json]` and
`oxide prepare`. `context` prints the payload to stdout and `used/budget units`
to stderr. OXIDE errors print `error[<code>]: <message>` to stderr and exit 2,
and malformed command lines print argparse usage and also exit 2. Any exit 1
with a traceback is a bug.
