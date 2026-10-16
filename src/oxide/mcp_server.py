"""``oxide mcp``: a stateless MCP server over the public API, built on FastMCP.

Every tool call opens the repository, reads, and closes it: no handle, lock or native
connection outlives a call, and no call depends on an MCP session, so ``oxide index``
can run between calls. Calls are serialized in-process because a store has one owner
and the native binding is not thread-safe. Nothing is indexed or created here: an
unindexed repository is a ``not_indexed`` tool error and leaves no store behind.
"""

from __future__ import annotations

import dataclasses
import logging
import os
import threading
from collections.abc import Callable, Sequence
from typing import Annotated

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


def _read[T](repository: str, data_dir: str | None, read: Callable[[Repository], T]) -> T:
    """Open, read, close, holding the in-process lock throughout. Runs on a worker thread."""
    with _lock:
        # A call cancelled while it queued never opens the store.
        anyio.from_thread.check_cancelled()
        try:
            with open_repository(repository, data_dir=data_dir, create=False) as repo:
                return read(repo)
        except OxideError as error:
            log.warning("%s: %s", error.code, error)
            advice = str(error) if error.code == "invalid_argument" else _ADVICE.get(error.code)
            raise ToolError(f"error[{error.code}]: {advice or 'request failed'}") from None


def server(repos: Sequence[str] = (), data_dir: str | None = None) -> FastMCP:
    """The MCP server; non-empty ``repos`` restricts which repositories calls may name."""
    resolve = _resolver(repos)
    app = FastMCP(
        "oxide",
        instructions="Structural code context for an indexed repository (`oxide index` "
        "builds the index; this server never indexes). Use oxide_search to find where "
        "something is defined, then oxide_context for a budgeted bundle of the relevant "
        "source with provenance.",
        # Unexpected exceptions reach the client as a generic message, never their text.
        mask_error_details=True,
    )

    @app.tool(annotations=READ_ONLY)
    def oxide_search(
        query: Query,
        repository: Repo = None,
        limit: Annotated[int, Field(ge=0, le=MAX_RESULTS, description="Lexical hits.")] = 20,
        paths: Paths = (),
        symbols: Symbols = (),
    ) -> SearchResult:
        """Find candidate symbols, files and modules for a query in an indexed repository:
        hint seeds first, then BM25 rank. Returns locations (path, byte range, digest),
        symbol metadata and the lexical channel status, never source text. Use it to
        locate code; use oxide_context to read it."""
        target = resolve(repository)
        return _read(
            target,
            data_dir,
            lambda repo: repo.search(query, limit=limit, paths=paths, symbols=symbols),
        )

    @app.tool(annotations=READ_ONLY, output_schema=TypeAdapter(ContextBundle).json_schema())
    def oxide_context(
        query: Query,
        repository: Repo = None,
        budget: Annotated[
            int, Field(ge=0, le=MAX_BUDGET, description="Context size in oxide-units-v1.")
        ] = 2048,
        paths: Paths = (),
        symbols: Symbols = (),
    ) -> ToolResult:
        """Build a bounded, source-backed context bundle for a task: relevant declarations
        and their enclosing headers, each under a `### path:first-last` heading, within
        `budget` units. The structured result adds omissions, routing, degradations and
        the snapshot it was read from."""
        target = resolve(repository)
        bundle = _read(
            target,
            data_dir,
            lambda repo: repo.build_context(query, budget, paths=paths, symbols=symbols),
        )
        head = (
            f"# oxide context: {bundle.used}/{bundle.budget} {bundle.units}, "
            f"{len(bundle.items)} items, {len(bundle.omissions)} omitted, "
            f"snapshot {bundle.snapshot.snapshot_id}\n"
        )
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
