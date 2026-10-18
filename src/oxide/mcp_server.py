"""``oxide mcp``: a stateless MCP server over the public API, built on FastMCP.

Every tool call opens the repository, reads, and closes it: no handle, lock or native
connection outlives a call, and no call depends on an MCP session, so ``oxide index``
can run between calls. Calls are serialized in-process because a store has one owner
and the native binding is not thread-safe. Nothing is indexed or created here: an
unindexed repository is a ``not_indexed`` tool error and leaves no store behind.
"""

import dataclasses
import logging
import os
import threading
from collections.abc import Callable, Sequence
from typing import Annotated, Any, Literal

import anyio.from_thread
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.tools import Tool, ToolResult
from fastmcp.tools.tool_transform import ArgTransform
from mcp.types import ToolAnnotations
from pydantic import Field, TypeAdapter

from oxide.api import Repository, open_repository
from oxide.models import ContextBundle, OxideError, SearchResult, StructuralResult

log = logging.getLogger(__name__)

# Output bounds: entries or matches per call, and units per bundle (the API's are larger).
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
Rule = Annotated[
    dict[str, Any] | None,
    Field(
        description="structural: ast-grep rule keys added to the `query` pattern, e.g. "
        '`{"not": {"inside": {"kind": "try_statement", "stopBy": "end"}}}` '
        "(inside, has, not, any, all, kind, regex, ...).",
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


def _structural_installed() -> bool:
    try:
        import ast_grep_py  # noqa: F401
    except ImportError:
        return False
    return True


def _invalid(message: str) -> ToolError:
    return ToolError(f"error[invalid_argument]: {message}")


def server(repos: Sequence[str] = (), data_dir: str | None = None) -> FastMCP:
    """The MCP server; non-empty ``repos`` restricts which repositories calls may name.

    One tool, ``oxide_search``, whose ``mode`` picks one API operation: ``search``
    (``Repository.search``), ``context`` (``build_context``) and, when the optional
    ``structural`` extra is installed, ``structural`` (``structural_search``).
    """
    resolve = _resolver(repos)
    structural = _structural_installed()
    modes: tuple[str, ...] = (
        ("search", "context", "structural") if structural else ("search", "context")
    )
    results: tuple[type, ...] = (SearchResult, ContextBundle, StructuralResult)[: len(modes)]
    adapters = [TypeAdapter(r).json_schema(ref_template="#/$defs/{model}") for r in results]
    defs: dict[str, Any] = {}
    for schema in adapters:
        defs.update(schema.pop("$defs", {}))
    output = {"type": "object", "anyOf": adapters, "$defs": defs}
    app = FastMCP(
        "oxide",
        instructions="Structural code context for an indexed repository (`oxide index` "
        "builds the index; this server never indexes). oxide_search finds where something "
        "is defined (mode=search), builds a budgeted bundle of the relevant source with "
        "provenance (mode=context)"
        + (
            ", or lists every node an ast-grep pattern matches (mode=structural)"
            if structural
            else ""
        )
        + ".",
        # Unexpected exceptions reach the client as a generic message, never their text.
        mask_error_details=True,
    )
    Mode = Annotated[
        Literal[modes],  # type: ignore[valid-type]
        Field(
            description="search: ranked locations (default). context: a source bundle "
            "within `budget`."
            + (
                " structural: every node matching the ast-grep pattern in `query`, in "
                "path order; needs `language`, and `paths` limits it to those files or "
                "directories."
                if structural
                else ""
            )
        ),
    ]

    limited = "search and structural" if structural else "search"

    def oxide_search(
        query: Query,
        repository: Repo = None,
        mode: Mode = "search",
        limit: Annotated[
            int | None,
            Field(
                ge=0,
                le=MAX_RESULTS,
                description=f"{limited}: at most this many results (default {DEFAULT_LIMIT}).",
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
        language: Annotated[
            Literal["python", "typescript"] | None,
            Field(description="structural: the grammar to match (typescript includes .tsx)."),
        ] = None,
        rule: Rule = None,
    ) -> SearchResult | ToolResult:
        """Search an indexed repository. mode=search (default) finds candidate symbols,
        files and modules: hint seeds first, then BM25 rank; locations (path, byte range,
        digest) and symbol metadata, never source text. mode=context builds a bounded,
        source-backed bundle: relevant declarations and their enclosing headers, each
        under a `### path:first-last` heading, within `budget` units. `paths` and
        `symbols` are hints that seed both."""
        target = resolve(repository)
        if mode != "context" and budget is not None:
            raise _invalid("`budget` applies to mode=context only")
        if mode == "context" and limit is not None:
            raise _invalid("`limit` applies to mode=search and mode=structural only")
        if mode != "structural" and (language is not None or rule is not None):
            raise _invalid("`language` and `rule` apply to mode=structural only")
        if mode == "search":
            count = DEFAULT_LIMIT if limit is None else limit
            return _read(
                target,
                data_dir,
                lambda repo: repo.search(query, limit=count, paths=paths, symbols=symbols),
            )
        if mode == "context":
            size = DEFAULT_BUDGET if budget is None else budget
            bundle = _read(
                target,
                data_dir,
                lambda repo: repo.build_context(query, size, paths=paths, symbols=symbols),
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
        if language is None:
            raise _invalid("mode=structural needs `language`")
        if symbols:
            raise _invalid("`symbols` does not apply to mode=structural")
        if rule is not None and "pattern" in rule:
            raise _invalid("`rule` adds to the `query` pattern; it must not set `pattern`")
        config = {"rule": {"pattern": query, **(rule or {})}}
        count = DEFAULT_LIMIT if limit is None else limit
        found = _read(
            target,
            data_dir,
            lambda repo: repo.structural_search(
                config, language=language, paths=paths, limit=count
            ),
        )
        lines = [
            f"# oxide structural: {len(found.matches)} matches"
            + (" (truncated)" if found.truncated else "")
            + f" in {found.files} {language} files, snapshot {found.snapshot.snapshot_id}"
        ]
        lines += [f"# partial (syntax errors): {p}" for p in found.partial]
        lines += [f"# skipped (not UTF-8): {p}" for p in found.skipped]
        lines += [
            f"{m.source.path}:{m.start_line}"
            + (f"-{m.end_line}" if m.end_line != m.start_line else "")
            + f": {m.preview.strip()}"
            for m in found.matches
        ]
        return ToolResult(
            content="\n".join(lines) + "\n", structured_content=dataclasses.asdict(found)
        )

    tool: Tool = Tool.from_function(oxide_search, annotations=READ_ONLY, output_schema=output)
    if not structural:  # its arguments are not offered either
        hidden = {name: ArgTransform(hide=True) for name in ("language", "rule")}
        tool = Tool.from_tool(tool, transform_args=hidden)
    app.add_tool(tool)
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
