"""Adaptive Graph Context (AGC) prototype: the text two experimental tools return.

Isolated from production: nothing in `src/oxide` imports this, and it reads OXIDE only
through `oxide.open_repository` (plus `View._query` once, for the entity list).

- `graph(symbol, direction, depth)`: callers or callees the index resolved, each with
  the source line that links it, then every remaining code occurrence of the name that
  no edge explains (callers), or the calls in its body the index could not resolve
  (callees). It never claims the first list is complete.
- `tiers(level, target)`: architecture -> module -> symbol -> source, one level a call.
"""

from __future__ import annotations

import ast
import bisect
import io
import re
import subprocess
import textwrap
import time
import tokenize
from collections.abc import Callable
from pathlib import Path
from typing import Any

import oxide
from oxide.models import OwnershipConflict

RESIDUAL_CAP = 40
WITNESS_CAP = 2
SOURCE_CAP = 250
SUMMARIZED = {".", "tests", "testing", "test", "doc", "docs", "examples", "example", "bench",
              "benchmarks", "scripts", "extra"}  # fmt: skip
NOTE = (
    "The graph lists edges the index resolved; it is not exhaustive. Decorators, names "
    "passed as values, method calls on receivers of unknown type and pytest fixtures may "
    "show up only under 'not explained'. That list is a text match: some entries may be "
    "other things with the same name."
)


class Agc:
    def __init__(self, root: str | Path, data_dir: str | Path) -> None:
        self.root, self.data_dir = Path(root).resolve(), Path(data_dir)
        rows = self._read(
            lambda repo: repo._view()._query(
                "MATCH (e:Entity) WHERE e.category IN ['file', 'symbol'] RETURN e.key, "
                "e.category, e.kind, e.name, e.path, e.start_byte, e.end_byte, e.signature, "
                "e.test"
            )
        )
        self.info: dict[str, tuple[Any, ...]] = {r[0]: tuple(r) for r in rows}
        self.symbols = sorted(r[0] for r in rows if r[1] == "symbol")
        self.files = sorted(r[4] for r in rows if r[1] == "file")
        self.newlines: dict[str, list[int]] = {}

    # --- plumbing ---------------------------------------------------------------------
    def _read(self, read: Callable[[Any], Any]) -> Any:
        """Open, read, close: another run's server may own the store between calls."""
        for _ in range(100):
            try:
                with oxide.open_repository(self.root, data_dir=self.data_dir, create=False) as r:
                    return read(r)
            except OwnershipConflict:
                time.sleep(0.1)
        raise OwnershipConflict(f"{self.root}: store stayed busy")

    def data(self, path: str) -> bytes:
        return (self.root / path).read_bytes()

    def line(self, path: str, offset: int) -> int:
        if path not in self.newlines:
            self.newlines[path] = [i for i, b in enumerate(self.data(path)) if b == 10]
        return bisect.bisect_left(self.newlines[path], offset) + 1

    def text(self, path: str, line: int) -> str:
        rows = self.data(path).decode("utf-8", "replace").split("\n")
        return rows[line - 1].strip()[:120] if 0 < line <= len(rows) else ""

    def span(self, key: str) -> tuple[int, int]:
        path, start, end = self.info[key][4], self.info[key][5], self.info[key][6]
        return self.line(path, start), self.line(path, max(start, end - 1))

    def where(self, key: str) -> str:
        if self.info[key][1] == "file":
            return self.info[key][4]
        return "{}:{}-{}".format(self.info[key][4], *self.span(key))

    @staticmethod
    def qual(key: str) -> str:
        return key.split("#", 1)[1] if "#" in key else key

    def label(self, key: str) -> str:
        kind, test = self.info[key][2], self.info[key][8]
        return f"{self.qual(key)} ({kind}{', test' if test else ''})"

    # --- symbol resolution ------------------------------------------------------------
    def resolve(self, symbol: str) -> list[str]:
        """Keys a name, dotted suffix, `path:line` or key names; non-test ones first."""
        s = symbol.strip().strip("`").removesuffix("()").replace("::", ".")
        if s in self.info:
            return [s]
        if m := re.fullmatch(r"(.+\.py):(\d+)", s):
            path, line = m.group(1).removeprefix("./"), int(m.group(2))
            inside = [k for k in self.symbols if self.info[k][4] == path
                      and self.span(k)[0] <= line <= self.span(k)[1]]  # fmt: skip
            return sorted(inside, key=lambda k: self.info[k][6] - self.info[k][5])[:1]
        dotted = s.replace("/", ".").removesuffix(".py")
        found = []
        for key in self.symbols:
            qual = self.qual(key)
            module = self.info[key][4].removeprefix("src/").removesuffix(".py")
            full = f"{module.removesuffix('/__init__').replace('/', '.')}.{qual}"
            if qual == dotted or full == dotted or full.endswith("." + dotted):
                found.append(key)
        return sorted(found, key=lambda k: (bool(self.info[k][8]), k))

    def pick(self, symbol: str) -> tuple[str | None, str]:
        keys = self.resolve(symbol)
        if not keys:
            return None, f"No symbol matches {symbol!r}. Try a qualified name or path:line."
        if len(keys) > 1:
            listed = "\n".join(f"  {k}  {self.where(k)}" for k in keys[:20])
            return None, f"{symbol!r} is ambiguous; call again with one of these keys:\n{listed}"
        return keys[0], ""

    # --- graph tool -------------------------------------------------------------------
    def graph(self, symbol: str, direction: str = "callers", depth: int = 1) -> str:
        key, problem = self.pick(symbol)
        if key is None:
            return problem
        depth, up = max(1, min(int(depth), 3)), direction != "callees"
        impact, rels = self._read(lambda r: _walk(r, key, up, depth))
        more = f"; more lie beyond depth {depth}" if impact.stopped_by == "depth" else ""
        if impact.stopped_by not in (None, "depth"):
            more = f"; walk cut by its {impact.stopped_by} bound"
        out = [
            f"{self.qual(key)}  {self.where(key)}  {(self.info[key][7] or '').split(chr(10))[0]}",
            f"{'callers' if up else 'callees'} the index resolved (depth <= {depth}): "
            f"{len(impact.hops)}{more}",
        ]
        witnessed: set[tuple[str, int]] = set()
        for hop in impact.hops:
            k, mid = hop.entity.key, hop.via.split(":", 2)[2]
            sure = "resolved" if hop.proven_depth is not None else "likely"
            chain = "" if hop.depth == 1 else f"  via {self.qual(mid)}"
            sites = self._witness(rels, k, mid, up)
            witnessed |= sites
            at = "; ".join(f"{p}:{n}: {self.text(p, n)}" for p, n in sorted(sites)[:WITNESS_CAP])
            name = self.label(k) if k in self.info else f"{hop.entity.name} (module)"
            if self.info.get(k, ("", "symbol"))[1] == "file":
                name = f"{self.info[k][4]} (module level)"
            fallback = self.where(k) if k in self.info else ""
            out.append(f"  [{hop.depth}, {sure}] {name}{chain}\n      at {at or fallback}")
        out += (
            self._residual(key, impact.roots, witnessed)
            if up
            else self._unresolved(rels, impact.roots)
        )
        out.append(NOTE)
        return "\n".join(out)

    def _witness(self, rels: dict[str, list[Any]], hop: str, mid: str, up: bool) -> set:
        """Source lines of the edges between `hop` and the entity it was reached from."""
        source, target = (hop, mid) if up else (mid, hop)
        return {
            (r.evidence.path, self.line(r.evidence.path, r.evidence.start))
            for r in rels.get(source, [])
            if r.source == source and r.target == target and r.kind != "contains"
            and r.evidence is not None
        }  # fmt: skip

    def _residual(self, key: str, roots: tuple[str, ...], witnessed: set) -> list[str]:
        name = self.info[key][3]
        hits = subprocess.run(
            ["rg", "-nw", "--column", "--no-heading", "-t", "py", "-F", name, "."],
            cwd=self.root, capture_output=True, text=True, check=False,
        ).stdout.splitlines()  # fmt: skip
        defined = [(self.info[r][4], *self.span(r)) for r in roots if r in self.info
                   and self.info[r][1] == "symbol"]  # fmt: skip
        listed, other = self._listed(key), {}
        rows: list[tuple[str, int, str]] = []
        names: dict[str, dict[int, set[int]]] = {}
        for hit in hits:
            path, line_s, col_s, text = hit.split(":", 3)
            path, line, col = path.removeprefix("./"), int(line_s), int(col_s) - 1
            if (path, line) in witnessed or path not in self.files:
                continue
            if path not in names:
                names[path] = code_names(self.data(path).decode("utf-8", "replace"))
            char = len(text.encode()[:col].decode("utf-8", "replace"))
            if char not in names[path].get(line, set()):
                continue  # a comment or a string
            kind = site_kind(text, char, name)
            if kind == "definition" and any(p == path and a <= line <= b for p, a, b in defined):
                continue  # the target's own `def` (its span starts at a decorator)
            if kind in listed:
                rows.append((path, line, f"  {path}:{line}: [{kind}] {text.strip()[:110]}"))
            else:
                other[kind] = other.get(kind, 0) + 1
        rows.sort()
        out = [f"not explained by any edge above: {len(rows)} code occurrences of `{name}`"]
        out += [row for _, _, row in rows[:RESIDUAL_CAP]]
        if len(rows) > RESIDUAL_CAP:
            out.append(f"  ... {len(rows) - RESIDUAL_CAP} more")
        if other:
            kinds = ", ".join(f"{k} {n}" for k, n in sorted(other.items()))
            out.append(f"  not listed, kinds that rarely depend on a {self.info[key][2]}: {kinds}")
        return out

    def _listed(self, key: str) -> set[str]:
        """Occurrence kinds worth an agent's look for this target."""
        if self.info[key][2] == "method":
            return {"attribute call", "definition", "decorator"}
        path, start = self.info[key][4], self.info[key][5]
        before = self.data(path)[max(0, start - 200) : start].decode("utf-8", "replace")
        fixture = "fixture" in "".join(before.splitlines()[-3:])  # its decorator lines
        kinds = {"call", "value", "decorator", "import", "attribute call", "attribute"}
        return kinds | {"definition"} | ({"parameter"} if fixture else set())

    def _unresolved(self, rels: dict[str, list[Any]], roots: tuple[str, ...]) -> list[str]:
        rows = sorted(
            {
                (r.evidence.path, self.line(r.evidence.path, r.evidence.start), r.name)
                for k in roots
                for r in rels.get(k, [])
                if r.source == k and r.target is None and r.kind == "calls" and r.evidence
            }
        )
        head = f"calls in its body the index could not resolve: {len(rows)}"
        return [head, *(f"  {p}:{n}: {name}(...)" for p, n, name in rows[:RESIDUAL_CAP])]

    # --- tiers tool -------------------------------------------------------------------
    def tiers(self, level: str, target: str = "") -> str:
        levels = {"architecture": lambda _: self.architecture(), "module": self.module,
                  "symbol": self.symbol, "source": self.source}  # fmt: skip
        if level not in levels:
            return "level must be architecture, module, symbol or source"
        return levels[level](target)

    def _members(self, path: str) -> list[str]:
        return sorted((k for k in self.symbols if self.info[k][4] == path),
                      key=lambda k: self.info[k][5])  # fmt: skip

    def architecture(self) -> str:
        groups: dict[str, list[str]] = {}
        for path in self.files:
            if path.endswith(".py"):
                groups.setdefault(path.rsplit("/", 1)[0] if "/" in path else ".", []).append(path)
        detailed, summary = {}, {}
        for folder, paths in groups.items():
            top = folder.split("/", 1)[0]
            if f"{folder}/__init__.py" in paths and top not in SUMMARIZED:
                detailed[folder] = paths
            else:  # scripts, tests, docs, examples: one line per top-level directory
                files, lines = summary.get(top, (0, 0))
                size = sum(self.data(q).count(b"\n") for q in paths)
                summary[top] = (files + len(paths), lines + size)
        out = ["Python packages (file: lines, top-level symbols, module docstring):"]
        for folder, paths in sorted(detailed.items()):
            out.append(f"{folder}/")
            for q in paths:
                tops = [k for k in self._members(q) if "." not in self.qual(k)]
                doc = first_doc(self.data(q))
                out.append(f"  {q.rsplit('/', 1)[-1]}: {self.data(q).count(b'\n')} lines, "
                           f"{len(tops)} symbols" + (f" — {doc}" if doc else ""))  # fmt: skip
        out.append("Other Python files, by top-level directory:")
        out += [f"  {top}/: {n} files, {size} lines" for top, (n, size) in sorted(summary.items())]
        out.append("Next: tiers(level='module', target=<file path>) for a file's outline.")
        return "\n".join(out)

    def module(self, target: str) -> str:
        path = target.strip().removeprefix("./")
        if path not in self.files:
            dotted = [f for f in self.files if f.removeprefix("src/").removesuffix(".py")
                      .removesuffix("/__init__").replace("/", ".") == path]  # fmt: skip
            if not dotted:
                return f"No file {target!r}. Paths are repository-relative, e.g. src/pkg/mod.py."
            path = dotted[0]
        out = [f"{path}  {self.data(path).count(b'\n')} lines"]
        for k in self._members(path):
            indent = "  " * (1 + self.qual(k).count("."))
            sig = (self.info[k][7] or self.info[k][3]).split("\n")[0]
            out.append("{}L{}-{}  {}".format(indent, *self.span(k), sig))
        out.append("Next: tiers(level='symbol', target=<name>) for a signature and docstring.")
        return "\n".join(out)

    def symbol(self, target: str) -> str:
        keys = self.resolve(target)
        if not keys:
            return f"No symbol matches {target!r}."
        out = []
        for k in keys[:5]:
            out.append(f"{self.qual(k)}  {self.where(k)}  ({self.info[k][2]})")
            out.append(f"  {(self.info[k][7] or '').split(chr(10))[0]}")
            if doc := docstring(self.data(self.info[k][4]), self.info[k][5], self.info[k][6]):
                out += [f"  | {d}" for d in doc.splitlines()[:12]]
            members = [m for m in self.symbols if m.startswith(k + ".")
                       and "." not in m[len(k) + 1 :]]  # fmt: skip
            if members:
                out.append("  members: " + ", ".join(m.rsplit(".", 1)[1] for m in members))
        if len(keys) > 5:
            out.append(f"... {len(keys) - 5} more matches; qualify the name.")
        out.append("Next: tiers(level='source', target=<name or path:start-end>) for the code.")
        return "\n".join(out)

    def source(self, target: str) -> str:
        if m := re.fullmatch(r"(.+\.py):(\d+)-(\d+)", target.strip()):
            path, first, last = m.group(1).removeprefix("./"), int(m.group(2)), int(m.group(3))
            if path not in self.files:
                return f"No file {path!r}."
        else:
            key, problem = self.pick(target)
            if key is None:
                return problem
            path, (first, last) = self.info[key][4], self.span(key)
        rows = self.data(path).decode("utf-8", "replace").split("\n")
        last = min(last, len(rows), first + SOURCE_CAP - 1)
        body = "\n".join(f"{n}\t{rows[n - 1]}" for n in range(first, last + 1))
        cut = f"\n(cut at {SOURCE_CAP} lines)" if last == first + SOURCE_CAP - 1 else ""
        return f"{path}:{first}-{last}\n{body}{cut}"


def _walk(repo: Any, key: str, up: bool, depth: int) -> tuple[Any, dict[str, list[Any]]]:
    impact = repo.impact(key, direction="upstream" if up else "downstream", depth=depth)
    keys = {h.entity.key for h in impact.hops} | set(impact.roots)
    return impact, {k: repo.relations(k) for k in keys}


def code_names(source: str) -> dict[int, set[int]]:
    """line -> start columns of NAME tokens; comments and strings hold none."""
    out: dict[int, set[int]] = {}
    try:
        for tok in tokenize.generate_tokens(io.StringIO(source).readline):
            if tok.type == tokenize.NAME:
                out.setdefault(tok.start[0], set()).add(tok.start[1])
    except (tokenize.TokenError, IndentationError, SyntaxError):
        pass
    return out


def site_kind(text: str, col: int, name: str) -> str:
    before, after = text[:col], text[col + len(name) :]
    stripped = before.lstrip()
    if stripped.startswith(("import ", "from ")):
        return "import"
    if re.fullmatch(r"@[\w.]*", stripped):
        return "decorator"
    if re.match(r"\s*(?:async\s+)?(?:def|class)\s+$", before):
        return "definition"
    if re.match(r"\s*(?:async\s+)?def\s", text) and "(" in before:
        return "parameter"
    if before.endswith("."):
        return "attribute call" if after.lstrip().startswith("(") else "attribute"
    return "call" if after.lstrip().startswith("(") else "value"


def docstring(data: bytes, start: int, end: int) -> str | None:
    """The docstring of the definition at [start, end), indented as in its file."""
    pad = start - (data.rfind(b"\n", 0, start) + 1)
    try:
        tree = ast.parse(textwrap.dedent(" " * pad + data[start:end].decode("utf-8", "replace")))
    except SyntaxError:
        return None
    node = tree.body[0] if tree.body else None
    if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
        return ast.get_docstring(node)
    return None


def first_doc(data: bytes) -> str:
    try:
        doc = ast.get_docstring(ast.parse(data))
    except (SyntaxError, ValueError):
        return ""
    return doc.strip().split("\n")[0][:90] if doc else ""
