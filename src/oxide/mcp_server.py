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

from oxide.api import MAX_DEPTH, Repository, open_repository
from oxide.models import ContextBundle, Impact, OxideError, SearchResult, advice

log = logging.getLogger(__name__)

# Output bounds: entries per search, and units per bundle (api.MAX_BUDGET is far larger).
MAX_RESULTS = 100
MAX_BUDGET = 32_768
DEFAULT_LIMIT = 20
DEFAULT_BUDGET = 2048
DEFAULT_IMPACT = 25
DEFAULT_DEPTH = 3
LOOPBACK = ("127.0.0.1", "localhost", "::1")

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
    Literal["search", "context", "impact"],
    Field(
        description="search: ranked locations, never source (default). context: a source "
        "bundle within `budget`. impact: what depends on the entity `key`."
    ),
]
Key = Annotated[
    str,
    Field(
        max_length=1024,
        description="impact: entity key from a mode=search entry, e.g. `symbol:pkg/core.py#low`.",
    ),
]
Direction = Annotated[
    Literal["upstream", "downstream"],
    Field(
        description="upstream: what depends on the entity (callers, importers, tests). "
        "downstream: what the entity depends on."
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
            detail = (
                str(error)
                if error.code == "invalid_argument"
                else advice(error, repository, private_data_dir=data_dir is not None)
            )
            raise ToolError(f"error[{error.code}]: {detail or 'request failed'}") from None


def _invalid(message: str) -> ToolError:
    return ToolError(f"error[invalid_argument]: {message}")


def server(repos: Sequence[str] = (), data_dir: str | None = None) -> FastMCP:
    """The MCP server; non-empty ``repos`` restricts which repositories calls may name.

    One tool, ``oxide_search``, whose ``mode`` picks one API operation: ``search``
    (``Repository.search``), ``context`` (``build_context``) or ``impact``
    (``Repository.impact``). Structural search stays a Python API: an agent trial found no
    benefit over grep (docs/decisions.md, structural-mcp-trial).
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
        "is defined (mode=search), builds a budgeted bundle of the relevant source with "
        "provenance (mode=context), or lists what depends on an entity (mode=impact).",
        # Unexpected exceptions reach the client as a generic message, never their text.
        mask_error_details=True,
        lifespan=lifespan,
    )
    results = TypeAdapter(SearchResult | ContextBundle | Impact).json_schema()
    output = {"type": "object", **results}

    @app.tool(annotations=READ_ONLY, output_schema=output)
    def oxide_search(
        query: Query | None = None,
        repository: Repo = None,
        mode: Mode = "search",
        limit: Annotated[
            int | None,
            Field(
                ge=0,
                le=MAX_RESULTS,
                description=f"search: lexical hits (default {DEFAULT_LIMIT}). impact: entities "
                f"returned (default {DEFAULT_IMPACT}).",
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
        key: Key | None = None,
        direction: Direction | None = None,
        depth: Annotated[
            int | None,
            Field(
                ge=0,
                le=MAX_DEPTH,
                description=f"impact: hops to follow (default {DEFAULT_DEPTH}).",
            ),
        ] = None,
    ) -> ToolResult:
        """Search an indexed repository. mode=search (default) finds candidate symbols,
        files and modules: hint seeds first, then BM25 rank; locations (path, byte range,
        digest) and symbol metadata, never source text. mode=context builds a bounded,
        source-backed bundle: relevant declarations and their enclosing headers, each
        under a `### path:first-last` heading, within `budget` units; the structured
        result adds omissions, routing, degradations and the snapshot. `paths` and
        `symbols` are hints that seed both. mode=impact lists what depends on the entity
        `key` from a mode=search entry: upstream (default) callers, importers and tests, or
        downstream what it calls. Hops come nearest first with their proven depth;
        `stopped_by` names the bound that cut the walk; `unresolved` counts same-name call
        sites OXIDE could not resolve, so dependents may be missing. Locations, never source.
        `query` is required for search and context; `key` for impact. Not needed to find
        where one name is defined: use mode=search."""
        target = resolve(repository)
        if mode == "impact":
            for name, given in (("query", query), ("budget", budget)):
                if given is not None:
                    raise _invalid(f"`{name}` does not apply to mode=impact")
            if paths or symbols:
                raise _invalid("`paths` and `symbols` do not apply to mode=impact")
            if not key:
                raise _invalid("mode=impact needs `key`, an entity key from a mode=search entry")
            entity = key
            walk = _read(
                target,
                data_dir,
                lambda repo: repo.impact(
                    entity,
                    direction=direction or "upstream",
                    depth=DEFAULT_DEPTH if depth is None else depth,
                    limit=DEFAULT_IMPACT if limit is None else limit,
                ),
                handles,
            )
            value = {"hops_returned": len(walk.hops)} | dataclasses.asdict(walk)
            text = json.dumps(value, separators=(",", ":"), ensure_ascii=False)
            return ToolResult(content=text, structured_content=value)
        if key is not None or direction is not None or depth is not None:
            raise _invalid("`key`, `direction` and `depth` apply to mode=impact only")
        if query is None:
            raise _invalid("`query` is required for mode=search and mode=context")
        words = query
        if mode == "search":
            if budget is not None:
                raise _invalid("`budget` applies to mode=context only")
            count = DEFAULT_LIMIT if limit is None else limit
            found = _read(
                target,
                data_dir,
                lambda repo: repo.search(words, limit=count, paths=paths, symbols=symbols),
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
            raise _invalid("`limit` applies to mode=search and mode=impact only")
        size = DEFAULT_BUDGET if budget is None else budget
        bundle = _read(
            target,
            data_dir,
            lambda repo: repo.build_context(words, size, paths=paths, symbols=symbols),
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
        head += "".join(f"# hint {h.kind} {h.hint}: {h.issue}\n" for h in bundle.hints)
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
