# OXIDE

A structural context engine for coding agents.

OXIDE captures a repository, derives a typed graph of its files, modules, symbols, and
relations, persists that graph in LadybugDB, and answers "what code matters for this
task?" with a bounded, source-backed context bundle. Retrieval is vectorless: BM25 over
code-aware terms plus structural seeds from typed hints, then TreeRouter and
deterministic selection and packing. No embeddings, model runners, or network access on
the query path.

Status: pre-release, on branch `rewrite/v2`. Python, TypeScript/TSX, JavaScript/JSX, Go, Rust and
Java have parser slices ([TypeScript](docs/typescript.md), [JavaScript](docs/javascript.md),
[Go](docs/go.md), [Rust](docs/rust.md), [Java](docs/java.md)); other files are kept as
unsupported file evidence. The public Python API is the product boundary
([docs/public-api.md](docs/public-api.md)). The legacy v1 implementation and its README
live on `main`; a copy is kept as history in
[docs/legacy/README-v1.md](docs/legacy/README-v1.md).

## Install

OXIDE needs CPython 3.12-3.14. Development uses [mise](https://mise.jdx.dev) and
[uv](https://docs.astral.sh/uv/):

```bash
git clone https://github.com/Kiy-K/oxide && cd oxide && git checkout rewrite/v2
mise run bootstrap        # uv sync --locked + `oxide prepare`
```

Without mise, run `uv sync --locked`, then `uv run oxide prepare`. `oxide prepare` is
the only network access OXIDE makes. It downloads the pinned LadybugDB FTS extension for
your platform (Linux x86_64, macOS 15+ x86_64/arm64, Windows x86_64;
[docs/platforms.md](docs/platforms.md)) and checks its SHA-256. Without that extension,
the lexical channel reports `unavailable` and structural seeds and routing still work.

OXIDE is not on PyPI yet. To use it outside the checkout, build it with `uv build` and
install the wheel into any environment (`pip install dist/oxide-*.whl`), then run
`oxide prepare`. That needs only Python and the native wheels. It does not need Rust,
Node.js, or a compiler. On Windows, LadybugDB also needs the OpenSSL 3 x64 DLLs in the
system directory. It opens only ASCII paths there, so with a non-ASCII user name, set
`OXIDE_DATA_DIR` and `OXIDE_FTS_EXTENSION` ([docs/platforms.md](docs/platforms.md)).

## Use

```bash
uv run oxide index  path/to/repo                      # built | unchanged
uv run oxide search path/to/repo "where are failed requests retried" --limit 20
uv run oxide context path/to/repo "where are failed requests retried" --budget 1024 \
    --symbol RetryPolicy --file src/http/client.py
```

Every command takes `--json` (the typed result) and `--data-dir`. `context` prints
the payload on stdout and `used/budget units` on stderr. Errors print
`error[<code>]: <message>` and exit 2.

```python
import oxide

with oxide.open_repository("path/to/repo") as repo:   # owns the store until closed
    repo.index()
    hits = repo.search("where are failed requests retried")
    bundle = repo.build_context("where are failed requests retried", budget=1024)
    print(bundle.payload)                             # used <= budget
```

### MCP

`oxide mcp` serves one read-only tool, `oxide_search`, over stdio (FastMCP). Its
`mode` is `search` (ranked locations, the default) or `context` (a budgeted source
bundle); `oxide_context` was folded into it on 2026-10-18. It never indexes: run
`oxide index` first, and again whenever you want fresher results, even while the
server runs.

```bash
uv run oxide mcp --repo path/to/repo                      # stdio, for a local agent
uv run oxide mcp --repo path/to/repo --http --port 8765   # stateless HTTP, loopback only
```

For example, in Claude Code: `claude mcp add oxide -- uv run --directory /path/to/oxide
oxide mcp --repo /path/to/repo`. With `--repo` (repeatable) calls may only name those
repositories, and with exactly one they may omit `repository`.

The store lives under `--data-dir`, else `$OXIDE_DATA_DIR`, else
`$XDG_DATA_HOME/oxide/py` (`~/.local/share/oxide/py`). It is never captured, even
when it sits inside the repository.

## Limits

- One owner per store, across processes. A `Repository` is not thread-safe.
- Indexing is a full rebuild per change, and capture holds the captured bytes in
  memory (at most 1 GiB in scope, 1 MiB per file).
- Verified on Linux x86_64, macOS 15+ (x86_64, arm64) and Windows x86_64 by the
  Platforms workflow. Windows needs the OpenSSL 3 DLLs and ASCII store paths; see
  [docs/platforms.md](docs/platforms.md).

[docs/public-api.md](docs/public-api.md) has the full list, with errors, bounds and
recovery behavior.

## Develop

```bash
mise run verify           # ruff, mypy --strict, pytest (CI runs exactly this)
uv run python scripts/bench.py path/to/corpus   # latency and peak RSS, JSON report
```

Architecture: [docs/spec/SPEC.md](docs/spec/SPEC.md), [docs/BOOTSTRAP.md](docs/BOOTSTRAP.md)
and the decisions in [docs/adr/](docs/adr/), in particular
[ADR-0011](docs/adr/0011-python-engine-vectorless.md) (Python engine, vectorless
retrieval). Contributor workflow: [docs/agents/workflow.md](docs/agents/workflow.md).

## License

[MIT](LICENSE)
