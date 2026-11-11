"""``oxide`` command line: a thin presentation layer over the public API.

``oxide mcp`` serves the same operations to MCP clients (stdio, or ``--http``).
Human output by default; ``--json`` prints the typed result. OXIDE errors print
``error[<code>]: <message>`` to stderr and exit 2; malformed command lines print
argparse usage and also exit 2. Exit 1 with a traceback is a bug.
"""

from __future__ import annotations

import argparse
import dataclasses
import io
import json
import sys
from typing import Any

from oxide.api import open_repository, prepare
from oxide.cli_nav import inspect_text, outline_text, refs_text
from oxide.models import Impact, IndexReport, JudgeStatus, OxideError, advice


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
    if report.skipped:
        lines.append(
            f"skipped: {len(report.skipped)} "
            f"({_paths(tuple(f'{s.path} [{s.reason}]' for s in report.skipped))})"
        )
    if report.unsupported:
        lines.append(f"unsupported (file-level only): {report.unsupported} files")
    if report.case_collisions:
        groups = tuple(" = ".join(group) for group in report.case_collisions)
        lines.append(f"case collisions: {_paths(groups)}")
    return "\n".join(lines)


def _judge_summary(status: JudgeStatus, path: str) -> str:
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
            f"enable: oxide judge {path} --enable; disable: oxide judge {path} --disable",
        ]
    )


def _impact_summary(impact: Impact) -> str:
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
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):  # Windows pipes default to the ANSI code page
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(prog="oxide", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("prepare", help="fetch and verify the pinned LadybugDB FTS extension")
    serve = commands.add_parser("mcp", help="serve the oxide_search tool over MCP")
    serve.add_argument(
        "--repo", action="append", default=[], help="serve only this repository (repeatable)"
    )
    serve.add_argument("--data-dir", help="data location (default: OXIDE_DATA_DIR or XDG)")
    serve.add_argument("--http", action="store_true", help="stateless Streamable HTTP, not stdio")
    serve.add_argument("--host", default="127.0.0.1", choices=("127.0.0.1", "localhost", "::1"))
    serve.add_argument("--port", type=int, default=8765)
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
        command.add_argument("path")
        command.add_argument("--data-dir", help="data location (default: OXIDE_DATA_DIR or XDG)")
        command.add_argument("--json", action="store_true", help="print the typed result as JSON")
        if name not in ("index", "judge", "impact", "outline", "inspect", "refs"):
            command.add_argument("query")
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
            command.add_argument("target", nargs="?", help="indexed file or directory")
            command.add_argument("--limit", type=int, default=500, help="declarations (0-1000)")
        if name in ("inspect", "refs"):
            command.add_argument("symbol", help="name, Outer.inner, path:line or entity key")
            command.add_argument("--limit", type=int, default=20 if name == "inspect" else 50)
        if name == "impact":
            command.add_argument(
                "key", help="entity key from oxide_search, e.g. symbol:pkg/core.py#low"
            )
            command.add_argument(
                "--direction", choices=("upstream", "downstream"), default="upstream"
            )
            command.add_argument("--depth", type=int, default=3, help="hops to follow (0-8)")
            command.add_argument(
                "--limit", type=int, default=200, help="entities to return (0-1000)"
            )
    args = parser.parse_args(argv)
    try:
        if args.command == "prepare":
            print(prepare())
            return 0
        if args.command == "mcp":
            # Imported here: the MCP stack is only loaded by the server.
            from oxide.mcp_server import serve as serve_mcp

            serve_mcp(args.repo, args.data_dir, args.http, args.host, args.port)
            return 0
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
                text = _judge_summary(result, args.path)
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
                text = _impact_summary(result)
            else:
                result = repo.build_context(
                    args.query, args.budget, paths=args.file, symbols=args.symbol
                )
                print(f"{result.used}/{result.budget} {result.units}", file=sys.stderr)
                if "judge" in result.versions:
                    print(f"judged by {result.versions['judge']}", file=sys.stderr)
                for line in result.degradations:
                    if line.startswith("judge "):
                        print(line, file=sys.stderr)
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
