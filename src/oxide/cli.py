"""``oxide`` command line: a thin presentation layer over the public API.

Human output by default; ``--json`` prints the typed result. OXIDE errors print
``error[<code>]: <message>`` to stderr and exit 2; malformed command lines print
argparse usage and also exit 2. Exit 1 with a traceback is a bug.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from typing import Any

from oxide.api import open_repository
from oxide.models import OxideError
from oxide.storage.ladybug import prepare_fts


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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="oxide", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("prepare", help="fetch and verify the pinned LadybugDB FTS extension")
    for name, text in (
        ("index", "capture and index a repository"),
        ("search", "show entry-point candidates for a query"),
        ("context", "build a bounded context bundle for a query"),
    ):
        command = commands.add_parser(name, help=text)
        command.add_argument("path")
        command.add_argument("--data-dir", help="data location (default: OXIDE_DATA_DIR or XDG)")
        command.add_argument("--json", action="store_true", help="print the typed result as JSON")
        if name != "index":
            command.add_argument("query")
            command.add_argument("--symbol", action="append", default=[], help="symbol hint")
            command.add_argument("--file", action="append", default=[], help="path hint")
        if name == "search":
            command.add_argument("--limit", type=int, default=20)
        if name == "context":
            command.add_argument("--budget", type=int, default=2048)
    args = parser.parse_args(argv)
    try:
        if args.command == "prepare":
            print(prepare_fts())
            return 0
        with open_repository(args.path, data_dir=args.data_dir) as repo:
            result: Any
            if args.command == "index":
                result = repo.index()
                text = (
                    f"{result.status}: {result.files} files, {result.entities} entities, "
                    f"{result.relations} relations in {result.seconds:.2f}s; "
                    f"lexical {result.lexical}"
                )
            elif args.command == "search":
                result = repo.search(
                    args.query, limit=args.limit, paths=args.file, symbols=args.symbol
                )
                lines = [
                    f"{e.lexical_rank or '-':>3} {e.lexical_score or 0:8.3f} {e.entity.key}"
                    for e in result.entries
                ]
                status = f"lexical {result.lexical.state}: {result.lexical.detail}"
                text = "\n".join([*lines, status])
            else:
                result = repo.build_context(
                    args.query, args.budget, paths=args.file, symbols=args.symbol
                )
                print(f"{result.used}/{result.budget} {result.units}", file=sys.stderr)
                text = result.payload
            output = json.dumps(_plain(result), indent=2, ensure_ascii=False) if args.json else text
            print(output.rstrip("\n"))
    except OxideError as error:
        print(f"error[{error.code}]: {error}", file=sys.stderr)
        return 2
    return 0
