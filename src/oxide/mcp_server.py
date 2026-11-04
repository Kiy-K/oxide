"""MCP adapter over the public API with server-lifetime repository ownership.

Tool calls are session-independent; local tracking keeps opened indexes fresh.
Unindexed repositories still fail without creating a store.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import os
import threading
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import asynccontextmanager
from typing import Annotated, Literal

import anyio.from_thread
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.tools import ToolResult
from mcp.types import ToolAnnotations
from pydantic import Field, TypeAdapter

from oxide.api import Repository, open_repository
from oxide.models import ContextBundle, OxideError, SearchResult

log = logging.getLogger(__name__)

# Output bounds: entries per search, and units per bundle (api.MAX_BUDGET is far larger).
MAX_RESULTS = 100
MAX_BUDGET = 32_768
DEFAULT_LIMIT = 20
DEFAULT_BUDGET = 2048
LOOPBACK = ("127.0.0.1", "localhost", "::1")

# What a caller can do about each expected failure. Engine messages name store
# directories and native errors, so they go to the server log only.
_ADVICE = {
    "invalid_path": "the repository path does not exist or is not a directory",
    "not_indexed": "the repository is not indexed; run `oxide index <repository>` first",
    "ownership_conflict": "another OXIDE process (e.g. `oxide index`) holds the repository; "
    "retry shortly",
    "incompatible_index": "the index was built by another OXIDE version; "
    "run `oxide index <repository>`",
    "store_corrupt": "the index is damaged; run `oxide index <repository>` "
    "(the server log has details)",
}

# shortcut: one lock serializes calls across all repositories; use per-store locks if
# multi-repository throughput ever matters.
_lock = threading.Lock()

Repo = Annotated[
    str | None,
    Field(
        description="Repository root path. Optional when the server serves exactly one "
        "repository (`oxide mcp --repo PATH`).",
        max_length=4096,
    ),
]
Query = Annotated[
    str, Field(description="What you are looking for, in words or identifiers.", max_length=2000)
]
Hint = Annotated[str, Field(max_length=1024)]
Paths = Annotated[
    tuple[Hint, ...],
    Field(description="Repository-relative file paths to seed from.", max_length=32),
]
Symbols = Annotated[
    tuple[Hint, ...],
    Field(description="Symbol names or dotted suffixes (`Class.method`).", max_length=32),
]
Mode = Annotated[
    Literal["search", "context"],
    Field(
        description="search: ranked locations, never source (default). context: a "
        "source bundle within `budget`."
    ),
]
READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=False)


def _resolver(repos: Sequence[str]) -> Callable[[str | None], str]:
    served = sorted({os.path.realpath(r) for r in repos})

    def resolve(repository: str | None) -> str:
        if repository is None:
            if len(served) == 1:
                return served[0]
            raise ToolError(
                "`repository` is required" + (f"; served: {', '.join(served)}" if served else "")
            )
        if served and os.path.realpath(repository) not in served:
            raise ToolError(f"{repository!r} is not served; served: {', '.join(served)}")
        return repository

    return resolve


def _read[T](
    repository: str,
    data_dir: str | None,
    read: Callable[[Repository], T],
    handles: dict[str, Repository],
) -> T:
    """Retain one handle per root until shutdown; evict failed handles for explicit repair."""
    with _lock:
        anyio.from_thread.check_cancelled()
        key = os.path.realpath(repository)
        try:
            try:
                if key not in handles:
                    handles[key] = open_repository(key, data_dir=data_dir, create=False)
                return read(handles[key])
            except BaseException:
                if repo := handles.pop(key, None):
                    repo.close()
                raise
        except OxideError as error:
            log.warning("%s: %s", error.code, error)
            advice = str(error) if error.code == "invalid_argument" else _ADVICE.get(error.code)
            raise ToolError(f"error[{error.code}]: {advice or 'request failed'}") from None


def _invalid(message: str) -> ToolError:
    return ToolError(f"error[invalid_argument]: {message}")


def server(repos: Sequence[str] = (), data_dir: str | None = None) -> FastMCP:
    """The MCP server; non-empty ``repos`` restricts which repositories calls may name.

    One tool, ``oxide_search``, whose ``mode`` picks one API operation: ``search``
    (``Repository.search``) or ``context`` (``build_context``). Structural search stays
    a Python API: an agent trial found no benefit over grep (docs/decisions.md,
    structural-mcp-trial).
    """
    resolve = _resolver(repos)
    handles: dict[str, Repository] = {}

    def close_handles() -> None:
        with _lock:
            for repo in handles.values():
                repo.close()
            handles.clear()

    @asynccontextmanager
    async def lifespan(app: FastMCP) -> AsyncIterator[None]:
        try:
            yield
        finally:
            await anyio.to_thread.run_sync(close_handles)

    app = FastMCP(
        "oxide",
        instructions="Structural code context for an indexed repository (`oxide index` "
        "builds the initial index; local tracking keeps it fresh). oxide_search finds what "
        "is defined (mode=search), or builds a budgeted bundle of the relevant source "
        "with provenance (mode=context).",
        # Unexpected exceptions reach the client as a generic message, never their text.
        mask_error_details=True,
        lifespan=lifespan,
    )
    results = TypeAdapter(SearchResult | ContextBundle).json_schema()
    output = {"type": "object", **results}

    @app.tool(annotations=READ_ONLY, output_schema=output)
    def oxide_search(
        query: Query,
        repository: Repo = None,
        mode: Mode = "search",
        limit: Annotated[
            int | None,
            Field(
                ge=0,
                le=MAX_RESULTS,
                description=f"search: lexical hits (default {DEFAULT_LIMIT}).",
            ),
        ] = None,
        budget: Annotated[
            int | None,
            Field(
                ge=0,
                le=MAX_BUDGET,
                description=f"context: size in oxide-units-v1 (default {DEFAULT_BUDGET}).",
            ),
        ] = None,
        paths: Paths = (),
        symbols: Symbols = (),
    ) -> ToolResult:
        """Search an indexed repository. mode=search (default) finds candidate symbols,
        files and modules: hint seeds first, then BM25 rank; locations (path, byte range,
        digest) and symbol metadata, never source text. mode=context builds a bounded,
        source-backed bundle: relevant declarations and their enclosing headers, each
        under a `### path:first-last` heading, within `budget` units; the structured
        result adds omissions, routing, degradations and the snapshot. `paths` and
        `symbols` are hints that seed both."""
        target = resolve(repository)
        if mode == "search":
            if budget is not None:
                raise _invalid("`budget` applies to mode=context only")
            count = DEFAULT_LIMIT if limit is None else limit
            found = _read(
                target,
                data_dir,
                lambda repo: repo.search(query, limit=count, paths=paths, symbols=symbols),
                handles,
            )
            # The status leads: clients that clip the tail (OpenCode code mode) keep it
            # (docs/phase-6/output-integrity/ at 013e791).
            more = found.lexical.truncated or any(h.issue == "truncated" for h in found.hints)
            value = {"entries_returned": len(found.entries), "more_available": more}
            value |= dataclasses.asdict(found)
            text = json.dumps(value, separators=(",", ":"), ensure_ascii=False)
            return ToolResult(content=text, structured_content=value)
        if limit is not None:
            raise _invalid("`limit` applies to mode=search only")
        size = DEFAULT_BUDGET if budget is None else budget
        bundle = _read(
            target,
            data_dir,
            lambda repo: repo.build_context(query, size, paths=paths, symbols=symbols),
            handles,
        )
        head = (
            f"# oxide context: {bundle.used}/{bundle.budget} {bundle.units}, "
            f"{len(bundle.items)} items, {len(bundle.omissions)} omitted, "
            f"snapshot {bundle.snapshot.snapshot_id}\n"
        )
        if "judge" in bundle.versions:  # this repository's source went to the judge
            head += f"# judged by {bundle.versions['judge']}\n"
        head += "".join(f"# degraded: {d}\n" for d in bundle.degradations)
        # The model reads the payload as text; applications get the whole bundle.
        return ToolResult(
            content=head + bundle.payload, structured_content=dataclasses.asdict(bundle)
        )

    return app


def serve(repos: Sequence[str], data_dir: str | None, http: bool, host: str, port: int) -> None:
    """Runs until stdin closes (stdio) or the process is interrupted (HTTP)."""
    app = server(repos, data_dir)
    if not http:
        app.run(show_banner=False)
        return
    if host not in LOOPBACK:
        # Unauthenticated: never listen beyond this machine.
        raise ValueError(f"--host must be one of {', '.join(LOOPBACK)}")
    app.run(
        transport="http",
        host=host,
        port=port,
        stateless_http=True,
        host_origin_protection=True,
        show_banner=False,
    )
