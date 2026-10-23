# OXIDE public Python API

The public API is what `oxide` (`src/oxide/__init__.py`) exports:
`open_repository`, `index_repository`, `Repository`, the result types in
`oxide.models` and `OxideError`. Integrations (the CLI, `oxide mcp`, future editor
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
| `open_repository(path, *, data_dir=None, create=True)` | `Repository` | Takes the store's exclusive lock; a second owner in any process gets `OwnershipConflict`. `create=False` (read-only clients) raises `NotIndexed` for a repository without a store and writes nothing |
| `Repository.index(*, progress=None)` | `IndexReport` | Capture → derive → publish a new generation atomically; `unchanged` when snapshot and derivation are already current. Incremental: Python and TypeScript files whose bytes the current generation parsed reuse their parse facts; resolution and publication always cover the whole snapshot, so the result equals a full rebuild. `progress(line)` is called as each stage starts |
| `Repository.search(query, *, limit, paths, symbols, selection, changed)` | `SearchResult` | Entry points for inspection: seeds in hint order, then BM25 rank; never final context |
| `Repository.build_context(query, budget, *, paths, symbols, selection, changed)` | `ContextBundle` | Retrieval → TreeRouter → deterministic selection → packing |
| `Repository.structural_search(rule, *, language, paths, limit=100)` | `StructuralResult` | Explicit structural search (§ Structural search). Never ranked, packed or used by `search`/`build_context` |
| `Repository.impact(key, *, direction="upstream", depth=3, limit=200)` | `Impact` | Bounded dependents (`upstream`) or dependencies (`downstream`) of one entity, with proven and unproven depths (§ Impact). Never ranked or packed |
| `Repository.entity(key)` / `relations(key)` / `files()` | `Entity \| None`, `list[Relation]`, `list[FileInfo]` | Typed graph and coverage inspection |
| `Repository.snapshot` | `Snapshot` | `repo_id`, `snapshot_id`, `derivation_id` of the published generation |
| `index_repository(path, *, data_dir=None)` | `IndexReport` | Open, index, close |
| `Repository.close()` | — | Releases the read view, its native connection and the lock; also on `with` exit. Idempotent. A repository dropped without `close()` releases them when garbage-collected |

Hints are typed query context: `paths` and `changed` resolve exactly, then by a
unique case-folded match; `symbols` are names or dotted suffixes
(`AuthService.refresh_token`); `selection` is `(path, start_byte, end_byte)` and
seeds the innermost declaration. Unmatched hints come back as `HintIssue`s.
In `build_context`, candidates in a file resolved from `paths` are packed before all
others, by the usual value and floor, and that file contributes only its header
view (its first module-level run). A path hint is a priority, not a guarantee: its
declarations still compete for the budget, routing bounds still apply, and the rest of
the budget goes to the usual evidence.

## Results

Every result is a frozen dataclass naming the `Snapshot` it was read from.

- `Entity`: `key`, `category` (`repository`/`module`/`file`/`symbol`), `kind`,
  `name`, `signature`, `source: SourceRef | None`, `test`. Keys are
  deterministic and snapshot-local (ADR-0010): `repo`, `module:python:a.b`,
  `module:typescript:src/a` ([docs/typescript.md](typescript.md)),
  `module:javascript:src/a.js` ([docs/javascript.md](javascript.md)),
  `module:go:src/store/store` ([docs/go.md](go.md)),
  `module:rust:crates/a/src/lib.rs::store` ([docs/rust.md](rust.md)), `file:<path>`,
  `symbol:<path>#Outer.inner~1`. Symbol kinds are the slice's: Python `class`,
  `function`, `method`; TypeScript also `interface`, `type`, `enum`, `namespace`,
  `variable`; Rust also `struct`, `enum`, `union`, `trait`, `impl` (named
  `<impl Trait for Type>`, holding its methods), `constant`, `variable`, `macro`.
  `FileInfo.language` is `python`, `typescript`, `javascript`, `go`, `rust` or `unknown`.
- `SourceRef`: `path`, half-open byte `start`/`end`, file `digest`
  (`sha256:` of the captured bytes).
- `Relation`: `kind` (`contains`, `contains_logical`, `defines`, `references`,
  `calls`, `imports`, `tested_by`), `source`, `target` (None when unresolved),
  `resolution` (`resolved`/`ambiguous`/`unresolved`), `basis`, `name`, `site`
  (shared by the rows of one ambiguous relation), `evidence`.
- `SearchResult`: `entries` (`EntryPoint`: entity, `lexical_rank`,
  `lexical_score`, `seeds`), `lexical` (`ChannelStatus`: `ran` with the scorer
  name, or `disabled`/`unavailable` with a reason, never a zero score), `hints`.
- `IndexReport`: `status` (`built`/`unchanged`), counts, `partial` paths,
  `unsupported`, `skipped`, `case_collisions`, `lexical`, `seconds`; and, for a
  build, `previous` (the `Snapshot` it replaced, None without a readable one),
  `added`/`modified`/`deleted` paths relative to it, and how many Python and
  TypeScript files were `parsed` or `reused`.
- `ContextBundle`: `items` (`ContextItem`: `source`, `lines`, `entities` with
  `complete` and `reason`, `units`, `text`; a `file:` entity in an item is a run of
  that file's module-level source, never `complete`), `payload`, `budget`, `used`,
  `units` (`oxide-units-v1`), `omissions` (stage and reason), `route`
  (`RouteSummary`), `lexical`, `hints`, `degradations`, `versions`.
- `Impact`: `root` entity, `direction`, `roots` (the keys standing for it), `hops`
  (`ImpactHop`: `entity`, `depth`, `proven_depth`, `via`, `cyclic`), `unresolved`,
  `stopped_by` (§ Impact).
- `StructuralResult`: `language`, `matches` (`StructuralMatch`: `source`
  `SourceRef`, 1-based `start_line`/`end_line`, `entity` the innermost declaration
  key or None at module level, `preview` the line the match starts on), `truncated`,
  `files` searched, `partial` (searched files whose index parse had errors, so matches
  inside them may be missing) and `skipped` (not UTF-8, not searched).

## Impact

`impact(key)` walks typed relations from one entity, breadth-first, nearest first:

- `upstream` (default): what depends on it. Callers, referrers and importers (incoming
  `calls`, `references`, `imports`) and the tests that exercise it (outgoing
  `tested_by`).
- `downstream`: what it depends on (outgoing `calls`, `references`, `imports`).

The root stands for itself and everything it physically contains (a class's methods, a
file's declarations) and, for a file, the modules that logically contain it, because
imports name modules; `roots` lists them. Each `ImpactHop` has two depths: `depth` over
every followed relation, `proven_depth` over relations that are `resolved` with a
non-heuristic basis only (`None` when no such path exists within `depth`). Ambiguous
candidates and the `tested_by` heuristic are reachable but never proven. `via` is
`kind:in|out:key`, the step a shallowest path took; `cyclic` marks an entity in a
dependency cycle with another reached entity (a strongly connected component of the
walk). Order is (`depth`, key).

`unresolved` (upstream only) counts unresolved `calls`/`references` sites whose text is
a root's name or ends with `.name`/`::name`. It is a by-name signal, not a bound: for a
common name (`get`) most of those sites target something else, and an aliased import is
not counted. Zero means no unresolved site even names it; a small upstream set with a
large `unresolved` is incomplete, not safe. Method calls through an instance
(`self.save()`, `client.get()`) are the main source, because OXIDE resolves no instance
or inherited attribute.

Bounds: `depth` in `[0, 8]`, `limit` reached entities in `[0, 1000]`, and 20,000
adjacency rows per pass; `stopped_by` names the one that cut the walk (`depth`,
`limit`, `edges`), `None` when the walk was exhausted. An unknown `key` is
`invalid_argument`. Measured cost (p50, 40 random symbols): 3–35 ms on requests,
xarray, pytest, ripgrep and gh-cli; 330 ms upstream on VS Code
([phase-6/graph-v1](phase-6/graph-v1/README.md)).
It is a Python API only; the MCP tool is unchanged (§ MCP).

## Structural search

`structural_search` runs one [ast-grep](https://ast-grep.github.io) rule over the
retained source of the published generation, the same bytes every other result names,
never the worktree. `rule` is either a pattern string (`"open($$$ARGS)"`) or a rule
config mapping (`{"rule": {...}, "constraints": {...}, "utils": {...}}`). Rule configs
add what patterns and grep can't express: `not`, `inside`/`has` with `stopBy`, `kind`,
`regex` and constraints on metavariables. `language` is `python` or `typescript`; a
TypeScript rule also runs on `.tsx` with the TSX grammar. `paths` keeps files equal to,
or under, any given repository path.

It answers code-shape questions OXIDE has no stored fact for: arguments, keyword
arguments, `except` bodies, a call that isn't inside a `try`, or an `await` inside a
loop. Callers, implementors, imports and tests are relations; use `relations(key)` for
them. Matches come in path order, then source order, at most `limit` (default 100, at
most 1000), with `truncated` set when more existed. It is read-only, deterministic, and
never part of retrieval, ranking or packing.

It is a Python API only; the MCP server does not offer it (§ MCP). It needs the
optional `structural` extra (`pip install 'oxide[structural]'`, which
pins `ast-grep-py` 0.51.0 and adds ~46 MB); without it the call raises
`native_unavailable`. ast-grep parses with its own bundled grammars. They are the same
`tree-sitter-python` 0.25.0 and `tree-sitter-typescript` 0.23.2 OXIDE pins, so
upgrading the extra must re-check that. The binding has no timeout and holds the GIL,
so `paths` and `limit` are the bounds. ast-grep alone took about 1 s per rule over VS
Code's 3,902 TypeScript files; through this API, with entity lookups,
0.35–0.81 s per rule on xarray and vue core (measured). Pattern pitfalls, measured in
[docs/phase-6/astgrep-feasibility/](phase-6/astgrep-feasibility/README.md):

- a pattern matches the syntax it spells: `new Promise($E)` misses
  `new Promise<T>(...)`;
- comments are named nodes: an `except` with a comment before its block isn't "a
  block first";
- relational `has` also visits unnamed tokens like `{` and `}`.

## Arguments

Checked before any query is built. A violation is `InvalidArgument`, which is both
an `OxideError` (`invalid_argument`) and a `ValueError`:

- `limit`: an `int` in `[0, 1000]` (`api.MAX_LIMIT`); `0` returns seeds only.
- `impact`: `key` a `str` naming an entity of the snapshot; `direction` `upstream` or
  `downstream`; `depth` an `int` in `[0, 8]` (`api.MAX_DEPTH`).
- `rule`: a non-blank pattern `str` or a mapping that ast-grep accepts for every grammar
  of `language` (`python`, or `typescript` and `tsx`); `language`: `python` or `typescript`.
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
| `not_indexed` | A read before the first `index()`, or `open_repository(..., create=False)` of a repository without a store |
| `incompatible_index` | The current generation was built by another derivation (component versions, including `ladybug`) or store schema. Checked from the manifest before the database is opened. `index()` rebuilds |
| `store_corrupt` | Damage (below). Reads fail closed |
| `capture_conflict` | The worktree kept changing during capture, or a `.gitignore` is unreadable |
| `capture_limit` | More than 1 GiB in scope |
| `native_unavailable` | LadybugDB's native library cannot load (on Windows: OpenSSL 3 DLLs missing, docs/platforms.md), or `structural_search` without the `structural` extra. Nothing is touched |
| `closed` | Use after `close()` |

Damage is classified like this:

- **Derived state is rebuilt by `index()`**, because the repository identity
  (`repo.json`) and the source are still established. This covers an
  unreadable, truncated, empty, missing or foreign `graph.lbug`, a malformed or
  foreign `MANIFEST.json`, and a missing or digest-mismatched retained source
  file. Reads raise `store_corrupt` until then, and `index()` never reports
  such a generation `unchanged`.
- **Lost reuse only.** A missing, damaged or older-format `facts.json` (the parse
  facts sealed with a generation, checked against the digest in its manifest) is
  ignored: the next `index()` parses every file again. An entry that matches its seal
  but isn't something the parser could produce (ill-typed, out of the file's bounds,
  not a scope tree) is parsed again and replaced, and `index()` reports it. Reads
  are unaffected.
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
- One owner per store across processes (`flock`). While a handle is open, every
  other owner (the CLI included) gets `ownership_conflict`, so long-running
  clients should open per request. `oxide mcp` opens, reads and closes on every
  tool call (9–13 ms open cost, measured in `docs/migration-audit/README.md`), so
  it never blocks `oxide index` between calls.
- A `Repository` is not thread-safe, and neither is the native binding.
  Serialize calls.

## Platform and native limitations

- On Windows, store and FTS extension paths must be ASCII (LadybugDB opens no other):
  a non-ASCII store path is `invalid_path`; see [platforms.md](platforms.md).
- The FTS extension is pinned for Linux x86_64, macOS x86_64/arm64 and Windows
  x86_64 ([platforms.md](platforms.md)). Elsewhere the lexical channel reports
  `unavailable`. Atomic publication and the crash tests run on all four in the
  Platforms workflow; [platforms.md](platforms.md) records which have passed.
- The pinned binding segfaults when it re-runs a parameterized
  `QUERY_FTS_INDEX`, so the FTS query is a literal built only from OXIDE terms
  (`[a-z0-9 ]`). Search text can never reach it unsanitized.
- Store and extension paths may contain `'`, `\`, spaces, control and non-ASCII
  characters (escaped in the two literal paths, `COPY` and `LOAD EXTENSION`).
  Paths that are not valid UTF-8 are `invalid_path`.
- Indexing reuses parse facts of unchanged bytes, but resolution and publication
  (the graph, FTS index and retained source) are rebuilt per change
  (`docs/phase-6/incremental-indexing/`). Capture holds every captured file in
  memory: at most 1 GiB in scope, 1 MiB per file, and peak RSS was about 1.0×
  the captured bytes plus ~30 MiB for capture alone. Derivation adds per-entity
  memory: 239 MiB peak for a 14 MiB stdlib. Measurements are in
  `docs/migration-audit/README.md`.

## CLI

`oxide index|search|context <path> [...] [--data-dir D] [--json]` and
`oxide prepare`. `index` prints its progress to stderr and a summary to stdout:
counts, changes since the last index, source files parsed and reused (`sources:`), then any
partial parses, skips, unsupported files and case collisions. `context` prints the
payload to stdout and `used/budget units` to stderr. OXIDE errors print `error[<code>]: <message>` to stderr and exit 2,
and malformed command lines print argparse usage and also exit 2. Any exit 1
with a traceback is a bug. `search` and `context` open with `create=False`, so
they never leave an empty store behind.

## MCP

`oxide mcp [--repo PATH]... [--data-dir D] [--http [--host H] [--port P]]`
(`src/oxide/mcp_server.py`, FastMCP 4.1) is another client of this API: it never
parses, routes, selects or packs.

It serves exactly one tool, `oxide_search`. Its `mode` picks one API operation:

| `mode` | Arguments (bounds) | Result |
| --- | --- | --- |
| `search` (default) | `query` (≤ 2000 chars), `repository`, `limit` (0–100, default 20), `paths`, `symbols` (≤ 32 × ≤ 1024 chars) | `Repository.search`: `SearchResult` as structured content and compact JSON text, led by `entries_returned` and `more_available` (the lexical limit was hit or a symbol hint was capped); locations, never source |
| `context` | `query`, `repository`, `budget` (0–32768 units, default 2048), `paths`, `symbols` | `Repository.build_context`: `ContextBundle` as structured content; the text is a one-line summary, any degradations, then `payload` |

`limit` with `mode="context"` and `budget` with `mode="search"` are `invalid_argument`
errors, never silently ignored. The output schema is either result type (`anyOf`).

**Migration (2026-10-18, intentional break).** `oxide_context` is gone. Call
`oxide_search` with `mode="context"`: the arguments, text and structured result are
the old tool's, byte for byte. A call without `mode` is the old `oxide_search`, with
the same arguments, defaults and output. Clients that list tools now see one.

Structural search (`Repository.structural_search`) is **not** an MCP mode. In a
preregistered agent trial, agents given it matched what they already found with grep
(shape-task F1 0.994 vs 0.978) ([phase-6/structural-mcp-trial](phase-6/structural-mcp-trial/README.md)).

- **Stateless.** Every call opens the repository, reads and closes it, inside a
  `with`; no handle, connection or lock outlives a call, and nothing depends on an
  MCP session. Calls are serialized in-process (one store owner per process, and
  the binding is not thread-safe). A call cancelled while it waits never opens
  the store; one already reading runs to the end and closes.
- **Repository.** Each call names `repository` (a path, relative to the server's
  working directory), or omits it when exactly one `--repo` is served. With
  `--repo`, other repositories are refused.
- **Errors** are MCP tool errors (`is_error`). OXIDE errors read
  `error[<code>]: <advice>`; the engine message, which names store paths, goes
  to the server log (stderr) only, except for `invalid_argument`. Schema
  violations are rejected before the call, and unexpected exceptions are masked.
- **Transports.** stdio by default; stdout carries only protocol frames.
  `--http` serves stateless Streamable HTTP at `/mcp` on a loopback host only
  (`127.0.0.1`, `localhost`, `::1`), with Host/Origin validation (foreign Host →
  421, foreign Origin → 403). It is unauthenticated, so it never listens beyond
  this machine.
- **No side effects.** Calls open with `create=False`: naming an unindexed
  directory is `not_indexed` and leaves nothing under the data location.
- **Launch it with the `oxide` command.** `python -m oxide` puts the working
  directory first on `sys.path`. Started inside a Python checkout, that checkout's
  top-level packages can shadow OXIDE's dependencies: FastMCP imports `requests`, and
  inside a checkout of requests it loaded that checkout's package (measured).
