# OXIDE

[![PyPI](https://img.shields.io/pypi/v/oxide-agent?include_prereleases)](https://pypi.org/project/oxide-agent/) [![CI](https://github.com/Kiy-K/oxide/actions/workflows/ci.yml/badge.svg)](https://github.com/Kiy-K/oxide/actions/workflows/ci.yml) [![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](https://github.com/Kiy-K/oxide/blob/main/LICENSE)

OXIDE indexes a repository into a graph of files, modules, symbols and relations, stores it in LadybugDB, and answers "what code matters for this task?" with a bounded context bundle of real source. It is made for coding agents and the people who work with them.

Retrieval needs no embeddings, no model calls and no network. It combines BM25 over code-aware terms with structural seeds from typed hints, then applies TreeRouter and deterministic selection and packing.

## Status

0.2.0rc1 is a release candidate. Python, TypeScript/TSX, JavaScript/JSX, Go, Rust, Java, C and C++ have parser slices ([TypeScript](https://github.com/Kiy-K/oxide/blob/main/docs/typescript.md), [JavaScript](https://github.com/Kiy-K/oxide/blob/main/docs/javascript.md), [Go](https://github.com/Kiy-K/oxide/blob/main/docs/go.md), [Rust](https://github.com/Kiy-K/oxide/blob/main/docs/rust.md), [Java](https://github.com/Kiy-K/oxide/blob/main/docs/java.md), [C](https://github.com/Kiy-K/oxide/blob/main/docs/c.md), [C++](https://github.com/Kiy-K/oxide/blob/main/docs/cpp.md)). Other files are kept as unsupported file evidence.

Upgrading from 0.1.1? Read the [changelog](https://github.com/Kiy-K/oxide/blob/main/CHANGELOG.md) first. It lists the breaking changes and what to do about each.

## Install

OXIDE needs CPython 3.12 to 3.14 on Linux x86_64, macOS 15 or later (arm64) or Windows x86_64 ([platform details](https://github.com/Kiy-K/oxide/blob/main/docs/platforms.md)). It needs no Rust, Node.js or compiler.

The PyPI project is `oxide-agent`; the import package and the command are `oxide`. This is a pre-release, so the commands pin the exact version.

With [uv](https://docs.astral.sh/uv/):

```bash
uv tool install oxide-agent==0.2.0rc1
```

With pipx or pip:

```bash
pipx install oxide-agent==0.2.0rc1
pip install oxide-agent==0.2.0rc1
```

To try it once without installing, name the command separately because it differs from the distribution:

```bash
uvx --from oxide-agent==0.2.0rc1 oxide --help
```

Explicit structural search (`Repository.structural_search`) needs the optional `structural` extra, which adds ast-grep (about 46 MB):

```bash
uv tool install 'oxide-agent[structural]==0.2.0rc1'
```

Then fetch the lexical-search extension once:

```bash
oxide prepare
```

`oxide prepare` is the only network access OXIDE makes. It downloads the pinned LadybugDB FTS extension for your platform and checks its SHA-256. Without it the lexical channel reports `unavailable`, and structural seeds and routing still work.

On Windows, LadybugDB also needs the OpenSSL 3 x64 DLLs in the system directory, and it opens only ASCII paths. With a non-ASCII user name, set `OXIDE_DATA_DIR` and `OXIDE_FTS_EXTENSION` ([details](https://github.com/Kiy-K/oxide/blob/main/docs/platforms.md)).

## Quick start

```bash
oxide index  path/to/repo                      # built | unchanged
oxide search path/to/repo "where are failed requests retried" --limit 20
oxide context path/to/repo "where are failed requests retried" --budget 1024 \
    --symbol RetryPolicy --file src/http/client.py
```

Every command takes `--json` (the typed result) and `--data-dir`. `context` prints the payload on stdout and `used/budget units` on stderr. Errors print `error[<code>]: <message>` and exit 2.

The same operations are available from Python:

```python
import oxide

with oxide.open_repository("path/to/repo") as repo:   # owns the store until closed
    repo.index()
    hits = repo.search("where are failed requests retried")
    bundle = repo.build_context("where are failed requests retried", budget=1024)
    print(bundle.payload)                             # used <= budget
```

`oxide status` shows the last published snapshot without touching the worktree. `oxide impact` lists what depends on an entity, and `search` and `context` results carry a short `blast` summary per entry point. `oxide outline`, `oxide inspect` and `oxide refs` read indexed facts, and they can read the snapshot of a process that is already running. While a handle is open, filesystem events keep its index fresh, so you rarely need to index again by hand.

## MCP server

`oxide mcp` serves one read-only tool, `oxide_search`, over stdio (FastMCP). Its `mode` is `search` for ranked locations (the default) or `context` for a budgeted source bundle. It never indexes, so run `oxide index` first.

```bash
oxide mcp --repo path/to/repo                      # stdio, for a local agent
oxide mcp --repo path/to/repo --http --port 8765   # stateless HTTP, loopback only
```

In Claude Code:

```bash
claude mcp add oxide -- oxide mcp --repo /path/to/repo
```

With `--repo` (repeatable), calls may only name those repositories, and with exactly one they may omit `repository`.

The server keeps one handle per repository until it shuts down, and it keeps the index fresh itself. That handle owns the store, so `oxide index` on the same store fails with `ownership_conflict` while the server runs. Stop the server first. The read-only `outline`, `inspect` and `refs` commands still work.

The store lives under `--data-dir`, else `$OXIDE_DATA_DIR`, else `$XDG_DATA_HOME/oxide/py` (`~/.local/share/oxide/py`). It is never captured, even when it sits inside the repository.

## Limits

- One owner per store across processes. A `Repository` serializes concurrent calls on the same handle.
- Capture holds the captured bytes in memory, at most 1 GiB in scope and 1 MiB per file.
- Each publication covers the whole snapshot. Unchanged files reuse their captured bytes and parse facts.
- The Platforms workflow verifies Linux x86_64, macOS 15 or later (arm64) and Windows x86_64.

The [public API reference](https://github.com/Kiy-K/oxide/blob/main/docs/public-api.md) has the full list, with errors, bounds and recovery behavior.

## Documentation and development

- [Public API](https://github.com/Kiy-K/oxide/blob/main/docs/public-api.md), [platforms](https://github.com/Kiy-K/oxide/blob/main/docs/platforms.md) and the [security model](https://github.com/Kiy-K/oxide/blob/main/SECURITY_MODEL.md)
- [Specification](https://github.com/Kiy-K/oxide/blob/main/docs/spec/SPEC.md) and [architecture decisions](https://github.com/Kiy-K/oxide/tree/main/docs/adr)
- [Contributor workflow](https://github.com/Kiy-K/oxide/blob/main/docs/agents/workflow.md) and the [documentation index](https://github.com/Kiy-K/oxide/blob/main/docs/README.md)

To work on OXIDE itself, use [mise](https://mise.jdx.dev) and uv:

```bash
git clone https://github.com/Kiy-K/oxide && cd oxide
mise run bootstrap        # uv sync --locked + `oxide prepare`
mise run verify           # ruff, mypy --strict, pytest (CI runs exactly this)
uv run python scripts/bench.py path/to/corpus   # latency and peak RSS, JSON report
```

Without mise, run `uv sync --locked` and then `uv run oxide prepare`. The v1 implementation is pinned at commit [`ac985b28`](https://github.com/Kiy-K/oxide/tree/ac985b28fcff7663aedadf2db5b40aa2dc325580), with its [README](https://github.com/Kiy-K/oxide/blob/ac985b28fcff7663aedadf2db5b40aa2dc325580/README.md).

## License

[MIT](https://github.com/Kiy-K/oxide/blob/main/LICENSE)
