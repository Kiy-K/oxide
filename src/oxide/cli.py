"""``oxide`` command line: a thin presentation layer over the public API.

``oxide mcp`` serves the same operations to MCP clients (stdio, or ``--http``).
Human output by default; ``--json`` prints the typed result. OXIDE errors print
``error[<code>]: <message>`` to stderr and exit 2; malformed command lines print
argparse usage and also exit 2. Exit 1 with a traceback is a bug.
"""

from __future__ import annotations

import argparse
import dataclasses
import difflib
import io
import json
import re
import sys
from importlib.metadata import PackageNotFoundError, version
from typing import Any, NoReturn

from oxide.api import MAX_DEPTH, MAX_LIMIT, open_repository, repository_status, setup
from oxide.cli_nav import inspect_text, outline_text, refs_text
from oxide.install import CLIENTS, install
from oxide.models import Impact, IndexReport, JudgeStatus, NotIndexed, OxideError, advice


def _plain(item: Any) -> Any:
    if dataclasses.is_dataclass(item) and not isinstance(item, type):
        out = {f.name: _plain(getattr(item, f.name)) for f in dataclasses.fields(item)}
        if hasattr(item, "payload"):
            out["payload"] = item.payload
        return out
    if isinstance(item, tuple | list):
        return [_plain(i) for i in item]
    if isinstance(item, dict):
        return {k: _plain(v) for k, v in item.items()}
    return item


def _status(path: str, data_dir: str | None, as_json: bool, verbose: bool) -> int:
    try:
        result = repository_status(path, data_dir=data_dir)
    except NotIndexed as error:
        hint = advice(error, path, data_dir)
        output: dict[str, Any] = {"status": "unindexed", "hint": hint}
        print(json.dumps(output) if as_json else f"unindexed: {hint}")
        return 0
    actions = []
    if any(file.coverage == "partial" for file in result.files):
        actions.append("Fix reported parse errors and re-index.")
    if result.skipped:
        actions.append("Check skipped files and re-index.")
    if result.case_collisions:
        actions.append("Rename conflicting files and re-index.")
    if result.lexical_unavailable:
        actions.append("Resolve the lexical error, then re-index.")
    output = {"status": "indexed", **_plain(result), "actions": actions}
    if as_json:
        print(json.dumps(output))
        return 0
    files = result.files
    counts = {
        state: sum(f.coverage == state for f in files)
        for state in ("complete", "partial", "unsupported")
    }
    coverage = ", ".join(f"{count} {state}" for state, count in counts.items())
    print("Index: published")
    print("Freshness: working tree not checked")
    print(
        f"{len(files)} files ({coverage}); {result.entities} entities; {result.relations} relations"
    )
    print("To refresh, run oxide index with the same repository and data location.")
    diagnostics = sum(f.coverage == "complete" and bool(f.diagnostics) for f in files)
    warnings = []
    if counts["partial"]:
        warnings.append(f"Partial parses: {counts['partial']} files")
    if counts["unsupported"]:
        warnings.append(f"Unsupported: {counts['unsupported']} files (file-level only)")
    if diagnostics:
        warnings.append(f"Other files with diagnostics: {diagnostics}")
    if result.skipped:
        warnings.append(f"Skipped: {len(result.skipped)} files")
    if result.case_collisions:
        warnings.append(f"Case collisions: {len(result.case_collisions)} groups")
    if result.lexical_unavailable:
        warnings.append(f"Lexical unavailable: {result.lexical_unavailable}")
    if warnings:
        print("Warnings:")
        for warning in warnings:
            print(f"  {warning}")
        for action in actions:
            print(f"  {action}")
        if not verbose and (
            counts["partial"]
            or counts["unsupported"]
            or diagnostics
            or result.skipped
            or result.case_collisions
        ):
            print("  Details: rerun this command with --verbose")
    if verbose:
        print(f"Snapshot: {result.snapshot.snapshot_id}")
        print(f"Repository: {result.snapshot.repo_id}; derivation: {result.snapshot.derivation_id}")
        for item in files:
            if item.coverage != "complete" or item.diagnostics:
                print(
                    f"{item.path}: {item.coverage}"
                    + (f"; {'; '.join(item.diagnostics)}" if item.diagnostics else "")
                )
        for skipped in result.skipped:
            print(f"skipped {skipped.path}: {skipped.reason}")
        for group in result.case_collisions:
            print(f"case collision: {' = '.join(group)}")
    return 0


def _paths(paths: tuple[str, ...], shown: int = 5) -> str:
    more = f", +{len(paths) - shown} more" if len(paths) > shown else ""
    return ", ".join(paths[:shown]) + more


def _index_summary(report: IndexReport) -> str:
    lines = [
        f"{report.status}: {report.files} files, {report.entities} entities, "
        f"{report.relations} relations in {report.seconds:.2f}s; lexical {report.lexical}"
    ]
    if report.status == "built":
        since = "since the last index" if report.previous else "(no readable previous index)"
        changes = [
            f"{len(paths)} {name}" + (f" ({_paths(paths)})" if paths else "")
            for name, paths in (
                ("added", report.added),
                ("modified", report.modified),
                ("deleted", report.deleted),
            )
        ]
        lines.append(f"changes {since}: " + "; ".join(changes))
        lines.append(f"sources: {report.parsed} parsed, {report.reused} reused")
    if report.partial:
        lines.append(f"partial parses: {len(report.partial)} ({_paths(report.partial)})")
        lines.append(
            "hint: inspect parse diagnostics with oxide status --verbose for the same repository "
            "and data location"
        )
    if report.skipped:
        lines.append(
            f"skipped: {len(report.skipped)} "
            f"({_paths(tuple(f'{s.path} [{s.reason}]' for s in report.skipped))})"
        )
        lines.append(
            "hint: review skipped files with oxide status --verbose for the same repository "
            "and data location"
        )
    if report.unsupported:
        lines.append(f"unsupported (file-level only): {report.unsupported} files")
    if report.case_collisions:
        groups = tuple(" = ".join(group) for group in report.case_collisions)
        lines.append(f"case collisions: {_paths(groups)}")
        lines.append("hint: rename conflicting files, then re-index")
    return "\n".join(lines)


def _judge_summary(status: JudgeStatus) -> str:
    if status.enabled:
        state = "enabled: build_context sends this repository's source to it"
    elif status.authorized:
        state = "disabled: the stored authorization is for another manifest; re-run --enable"
    else:
        state = "disabled: nothing is sent"
    key = "set" if status.credential_set else "not set (an enabled judge falls back)"
    return "\n".join(
        [
            f"judge {status.provider}: {state}",
            f"endpoint: {status.endpoint}",
            f"manifest: {status.manifest}",
            f"credential: {status.credential} {key}",
            f"sends: {status.discloses}",
            "To change: run oxide judge with the same repository and data location, "
            "adding --enable or --disable",
        ]
    )


def _impact_summary(impact: Impact, depth: int, limit: int) -> str:
    stop = impact.stopped_by or "none"
    lines = [
        f"{impact.direction} of {impact.root.key}: {len(impact.hops)} reached; "
        f"stopped by {stop}; unresolved call sites {impact.unresolved}"
    ]
    if impact.roots != (impact.root.key,):
        lines.append(f"roots: {_paths(impact.roots)}")
    for hop in impact.hops:
        proven = "-" if hop.proven_depth is None else str(hop.proven_depth)
        cyclic = "  (cyclic)" if hop.cyclic else ""
        lines.append(f"{hop.depth:>3} {proven:>3} {hop.entity.key}  via {hop.via}{cyclic}")
    hints = {
        "depth": (
            f"hint: --depth is already at its maximum ({MAX_DEPTH})"
            if depth == MAX_DEPTH
            else "hint: consider raising --depth"
        ),
        "limit": (
            f"hint: --limit is already at its maximum ({MAX_LIMIT})"
            if limit == MAX_LIMIT
            else "hint: consider raising --limit"
        ),
        "edges": "hint: traversal hit an internal edge bound",
    }
    if impact.stopped_by in hints:
        lines.append(hints[impact.stopped_by])
    return "\n".join(lines)


# Long options per subcommand: abbreviations are unambiguous only within that set
# (argparse's default), so ``oxide impact … --di upstream`` must not collide with
# judge's ``--disable``.
_COMMAND_OPTIONS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "install": (("--client",), ()),
    "mcp": (("--data-dir", "--host", "--port", "--repo"), ("--http",)),
    "index": (("--data-dir",), ("--json",)),
    "status": (("--data-dir",), ("--json", "--verbose")),
    "judge": (("--data-dir",), ("--json", "--enable", "--disable")),
    "search": (("--data-dir", "--limit", "--file", "--symbol"), ("--json",)),
    "context": (("--data-dir", "--budget", "--file", "--symbol"), ("--json",)),
    "impact": (("--data-dir", "--direction", "--depth", "--limit"), ("--json",)),
    "outline": (("--data-dir", "--limit"), ("--json",)),
    "inspect": (("--data-dir", "--limit"), ("--json",)),
    "refs": (("--data-dir", "--limit"), ("--json",)),
}


def _option_takes_value(option: str, command: str) -> bool:
    """Whether ``option`` (possibly abbreviated) takes a following argument for ``command``."""
    if "=" in option:
        return False
    valued, flags = _COMMAND_OPTIONS.get(command, ((), ()))
    if option in valued:
        return True
    if option in flags:
        return False
    value_hits = [name for name in valued if name.startswith(option)]
    flag_hits = [name for name in flags if name.startswith(option)]
    return len(value_hits) == 1 and not flag_hits


def _front_positionals(argv: list[str]) -> list[str]:
    """Put bare tokens that follow options back with the other positionals.

    Python 3.12 ``argparse`` rejects ``cmd PATH --data-dir D target``; 3.13 accepts it.
    Rewrite to ``cmd PATH target --data-dir D`` so both accept the same command lines.
    """
    if len(argv) < 2 or argv[0] in ("setup", "mcp") or argv[0].startswith("-"):
        return argv
    cmd, leading, trailing, options = argv[0], [], [], []
    seen_option, i = False, 0
    rest = argv[1:]
    while i < len(rest):
        tok = rest[i]
        if tok == "--":
            options.extend(rest[i:])
            break
        if tok.startswith("-"):
            seen_option = True
            options.append(tok)
            if _option_takes_value(tok, cmd) and i + 1 < len(rest):
                i += 1
                options.append(rest[i])
        elif seen_option:
            trailing.append(tok)
        else:
            leading.append(tok)
        i += 1
    return [cmd, *leading, *trailing, *options]


_EPILOG = """\
examples:
  oxide setup                            fetch the lexical search extension (once per machine)
  oxide install                          register oxide mcp with your MCP clients (once)
  oxide index                            index the repository in the current directory
  oxide search . "where are retries"     list entry points for a query
  oxide context . "fix retry bug"        build a bounded bundle of relevant source

`oxide <command> --help` lists a command's options. `--json` prints the typed result.
Errors print `error[<code>]: <message>` to stderr and exit 2.
Docs:   https://github.com/Kiy-K/oxide/blob/main/docs/public-api.md
Issues: https://github.com/Kiy-K/oxide/issues
"""


class _Parser(argparse.ArgumentParser):
    """Adds a "Did you mean" line to a mistyped command (Python 3.14 does it itself)."""

    commands: tuple[str, ...] = ()

    def error(self, message: str) -> NoReturn:
        typo = re.search(r"argument command: invalid choice: '([^']*)'", message)
        close = difflib.get_close_matches(typo[1], self.commands, n=1) if typo else []
        if close and "maybe you meant" not in message:
            message += f"\nDid you mean '{close[0]}'?"
        super().error(message)


def _version() -> str:
    try:
        return version("oxide-agent")
    except PackageNotFoundError:  # run from a source tree that is not installed
        return "unknown"


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):  # Windows pipes default to the ANSI code page
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8")
    parser = _Parser(
        prog="oxide",
        description="Structural code context for coding agents and humans: index a "
        "repository, then search it or build a bounded, source-backed context bundle.",
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("-V", "--version", action="version", version=f"oxide {_version()}")
    commands = parser.add_subparsers(dest="command", metavar="command")
    commands.add_parser("setup", help="fetch and verify the pinned LadybugDB FTS extension")
    install_parser = commands.add_parser(
        "install", help="register oxide mcp with your MCP clients, user-wide"
    )
    install_parser.add_argument(
        "--client",
        action="append",
        default=[],
        choices=CLIENTS,
        help="client to configure (repeatable; default: every client detected)",
    )
    serve = commands.add_parser("mcp", help="serve the oxide_search tool over MCP")
    serve.add_argument(
        "--repo", action="append", default=[], help="serve only this repository (repeatable)"
    )
    serve.add_argument("--data-dir", help="data location (default: OXIDE_DATA_DIR or XDG)")
    serve.add_argument("--http", action="store_true", help="stateless Streamable HTTP, not stdio")
    serve.add_argument("--host", default="127.0.0.1", choices=("127.0.0.1", "localhost", "::1"))
    serve.add_argument("--port", type=int, default=8765)
    status_parser = commands.add_parser("status", help="show the last published index status")
    status_parser.add_argument("path", nargs="?", default=".")
    status_parser.add_argument("--data-dir", help="data location (default: OXIDE_DATA_DIR or XDG)")
    status_parser.add_argument("--json", action="store_true", help="print the typed result as JSON")
    status_parser.add_argument("-v", "--verbose", action="store_true", help="show file details")
    for name, text in (
        ("index", "capture and index a repository"),
        ("judge", "show, enable or disable the optional JEV judge (sends source)"),
        ("search", "show entry-point candidates for a query"),
        ("context", "build a bounded context bundle for a query"),
        ("impact", "what depends on an entity (callers, importers, tests) or what it depends on"),
        ("outline", "declarations of an indexed file or directory, with lines"),
        ("inspect", "resolve a name, path or path:line to its definition and relations"),
        ("refs", "callers, references and imports of a name, labelled by evidence"),
    ):
        command = commands.add_parser(name, help=text)
        if name == "index":
            command.add_argument(
                "path", nargs="?", default=".", help="repository root (default: .)"
            )
        else:
            command.add_argument("path")
        # Positionals before options so a flags-last line parses on every supported Python.
        if name not in ("index", "judge", "impact", "outline", "inspect", "refs"):
            command.add_argument("query")
        if name == "outline":
            command.add_argument("target", nargs="?", help="indexed file or directory")
        if name in ("inspect", "refs"):
            command.add_argument("symbol", help="name, Outer.inner, path:line or entity key")
        if name == "impact":
            command.add_argument(
                "key", help="entity key from oxide_search, e.g. symbol:pkg/core.py#low"
            )
        command.add_argument("--data-dir", help="data location (default: OXIDE_DATA_DIR or XDG)")
        command.add_argument("--json", action="store_true", help="print the typed result as JSON")
        if name not in ("index", "judge", "impact", "outline", "inspect", "refs"):
            command.add_argument("--symbol", action="append", default=[], help="symbol hint")
            command.add_argument("--file", action="append", default=[], help="path hint")
        if name == "search":
            command.add_argument("--limit", type=int, default=20)
        if name == "context":
            command.add_argument("--budget", type=int, default=2048)
        if name == "judge":
            switch = command.add_mutually_exclusive_group()
            switch.add_argument("--enable", action="store_true", help="authorize sending")
            switch.add_argument("--disable", action="store_true", help="revoke it")
        if name == "outline":
            command.add_argument("--limit", type=int, default=500, help="declarations (0-1000)")
        if name in ("inspect", "refs"):
            command.add_argument("--limit", type=int, default=20 if name == "inspect" else 50)
        if name == "impact":
            command.add_argument(
                "--direction", choices=("upstream", "downstream"), default="upstream"
            )
            command.add_argument("--depth", type=int, default=3, help="hops to follow (0-8)")
            command.add_argument(
                "--limit", type=int, default=200, help="entities to return (0-1000)"
            )
    parser.commands = tuple(commands.choices)
    args = parser.parse_args(_front_positionals(list(argv) if argv is not None else sys.argv[1:]))
    if args.command is None:
        parser.print_help()
        return 0
    try:
        if args.command == "setup":
            print(setup())
            return 0
        if args.command == "install":
            return install(args.client)
        if args.command == "mcp":
            # Imported here: the MCP stack is only loaded by the server.
            from oxide.mcp_server import serve as serve_mcp

            serve_mcp(args.repo, args.data_dir, args.http, args.host, args.port)
            return 0
        if args.command == "status":
            return _status(args.path, args.data_dir, args.json, args.verbose)
        # Reads never leave an empty store behind.
        code = 0
        create = args.command == "index" or getattr(args, "enable", False)
        # Navigation reads attach to a running owner (e.g. `oxide mcp`) instead of failing.
        shared = args.command in ("outline", "inspect", "refs")
        with open_repository(
            args.path, data_dir=args.data_dir, create=create, shared=shared
        ) as repo:
            if repo.read_only:
                print(
                    "oxide: another process owns the store; reading its latest snapshot",
                    file=sys.stderr,
                )
            result: Any
            if args.command == "index":
                result = repo.index(progress=lambda line: print(f"oxide: {line}", file=sys.stderr))
                text = _index_summary(result)
            elif args.command == "judge":
                enable = True if args.enable else False if args.disable else None
                result = repo.judge(enable)
                text = _judge_summary(result)
            elif args.command == "search":
                result = repo.search(
                    args.query, limit=args.limit, paths=args.file, symbols=args.symbol
                )
                lines = [
                    f"{e.lexical_rank or '-':>3} {e.lexical_score or 0:8.3f} {e.entity.key}"
                    for e in result.entries
                ]
                hints = [f"hint: {h.kind} {h.hint}: {h.issue}" for h in result.hints]
                status = f"lexical {result.lexical.state}: {result.lexical.detail}"
                if result.lexical.truncated:
                    status += "\nhint: lexical results truncated; consider raising --limit"
                blast = [f"blast {root.line()}" for root in result.blast]
                text = "\n".join([*lines, *hints, status, *blast])
            elif args.command == "outline":
                result = repo.outline(args.target, limit=args.limit)
                text = outline_text(result)
            elif args.command == "inspect":
                result = repo.inspect(args.symbol, limit=args.limit)
                text = inspect_text(result)
                code = 0 if result.status == "resolved" else 1
            elif args.command == "refs":
                result = repo.refs(args.symbol, limit=args.limit)
                text = refs_text(result)
                code = 0 if result.status == "resolved" else 1
            elif args.command == "impact":
                result = repo.impact(
                    args.key, direction=args.direction, depth=args.depth, limit=args.limit
                )
                text = _impact_summary(result, args.depth, args.limit)
            else:
                result = repo.build_context(
                    args.query, args.budget, paths=args.file, symbols=args.symbol
                )
                print(f"{result.used}/{result.budget} {result.units}", file=sys.stderr)
                if "judge" in result.versions:
                    print(f"judged by {result.versions['judge']}", file=sys.stderr)
                warned: set[str] = set()
                for line in result.degradations:
                    if line.startswith("judge "):
                        print(line, file=sys.stderr)
                    elif not args.json and line not in warned:
                        print(f"warning: {line}", file=sys.stderr)
                        warned.add(line)
                for issue in result.hints:
                    print(f"hint: {issue.kind} {issue.hint}: {issue.issue}", file=sys.stderr)
                for root in result.blast:
                    print(f"blast {root.line()}", file=sys.stderr)
                text = result.payload
            output = json.dumps(_plain(result), indent=2, ensure_ascii=False) if args.json else text
            print(output.rstrip("\n"))
            return code
    except OxideError as error:
        print(f"error[{error.code}]: {error}", file=sys.stderr)
        repository = getattr(args, "path", None)
        data_dir = getattr(args, "data_dir", None)
        if repository is not None and (hint := advice(error, repository, data_dir)):
            print(f"hint: {hint}", file=sys.stderr)
        return 2
    return 0
