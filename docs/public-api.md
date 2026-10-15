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
`$XDG_DATA_HOME/oxide/py` (or `~/.local/share/oxide/py`). Lexical search needs
the pinned FTS extension: run `oxide prepare` once.

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
| `Repository.close()` | — | Releases the lock; also on `with` exit |

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

## Errors

`OxideError` subclasses carry a stable `code`: `invalid_path`,
`ownership_conflict`, `not_indexed`, `incompatible_index` (built by another
derivation or store schema; `index()` rebuilds), `store_corrupt` (fail closed),
`capture_conflict`, `capture_limit`, `closed`. A negative budget is a
`ValueError`.

## CLI

`oxide index|search|context <path> [...] [--data-dir D] [--json]` and
`oxide prepare`. `context` prints the payload to stdout and `used/budget units`
to stderr. Errors print `error[<code>]: <message>` and exit 2.
