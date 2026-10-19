"""Experiment-only outline contract shared by arms B and C (preregistration.md § Contract).

An outline is a list of entries: top-level items (imports included) and their direct
members, each with a byte range in the captured file, 1-based first/last lines, a kind
and a label. B fills it from the pinned ``ast-grep outline`` CLI run over the captured
snapshot bytes on stdin; C from OXIDE's already-indexed entities and relations, with no
parse. One renderer prints both, so the arms differ only in what was extracted.
"""

from __future__ import annotations

import bisect
import hashlib
import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DATA = Path(
    os.environ.get(
        "OXIDE_OUTLINE_DATA", Path.home() / "Projects/oxide-eval-data/oxide-outline-eval"
    )
)
AST_GREP = DATA / "tool/bin/ast-grep"
AST_GREP_VERSION = "ast-grep 0.51.0"  # PyPI ast-grep-cli==0.51.0, manylinux_2_28_x86_64 wheel
AST_GREP_SHA256 = "bd9e202969472ab6bd1e3fa0b498ba5fc87deef0810666ee5311940befbd1e24"
COMMAND = ["outline", "--stdin", "--items", "all", "--json=compact", "--color", "never"]
LANGS = {
    ".py": "python",
    ".ts": "typescript",
    ".mts": "typescript",
    ".cts": "typescript",
    ".tsx": "tsx",
    ".js": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".jsx": "javascript",  # ast-grep has no separate jsx language; its JS grammar has JSX
}
MAX_LABEL = 120  # characters of a label kept in the rendering, same for both arms


@dataclass(frozen=True, slots=True)
class Entry:
    role: str  # "import", "item" or "member"
    kind: str
    name: str
    label: str
    start: int  # byte range [start, end) in the captured file
    end: int
    first: int  # 1-based lines
    last: int
    members: tuple[Entry, ...] = ()


class Lines:
    def __init__(self, data: bytes) -> None:
        self.data = data
        self.breaks = [i for i, b in enumerate(data) if b == 10]

    def line(self, byte: int) -> int:
        return bisect.bisect_left(self.breaks, byte) + 1

    def span(self, start: int, end: int) -> tuple[int, int]:
        return self.line(start), self.line(max(start, end - 1))

    def count(self) -> int:
        return len(self.breaks) + (0 if self.data.endswith(b"\n") or not self.data else 1)

    def text(self, n: int) -> str:
        lo = self.breaks[n - 2] + 1 if n > 1 else 0
        hi = self.breaks[n - 1] if n - 1 < len(self.breaks) else len(self.data)
        return self.data[lo:hi].decode("utf-8", "replace")


def _label(text: str, name: str) -> str:
    first = text.strip().splitlines()[0].strip() if text.strip() else ""
    label = first or name
    return label if len(label) <= MAX_LABEL else label[: MAX_LABEL - 1] + "…"


def verify_tool() -> str:
    found = hashlib.sha256(AST_GREP.read_bytes()).hexdigest()
    if found != AST_GREP_SHA256:
        raise SystemExit(f"{AST_GREP}: sha256 {found}, expected {AST_GREP_SHA256}")
    version = subprocess.run([AST_GREP, "--version"], capture_output=True, text=True).stdout
    if version.strip() != AST_GREP_VERSION:
        raise SystemExit(f"{AST_GREP}: {version.strip()!r}, expected {AST_GREP_VERSION!r}")
    return version.strip()


def astgrep_raw(data: bytes, lang: str) -> tuple[bytes, int]:
    done = subprocess.run(
        [AST_GREP, *COMMAND, "--lang", lang], input=data, capture_output=True, timeout=60
    )
    return done.stdout, done.returncode


def astgrep(data: bytes, path: str) -> tuple[list[Entry] | None, str | None]:
    """Arm B: (entries, None), or (None, failure reason)."""
    lang = LANGS.get(Path(path).suffix.lower())
    if lang is None:
        return None, "no ast-grep language for this extension"
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return None, "not UTF-8"
    try:
        raw, code = astgrep_raw(data, lang)
    except subprocess.TimeoutExpired:
        return None, "timeout"
    if code != 0:
        return None, f"exit {code}"
    files = json.loads(raw)
    lines = Lines(data)

    def entry(node: dict[str, Any], role: str) -> Entry:
        r = node["range"]["byteOffset"]
        start, end = r["start"], r["end"]
        first, last = lines.span(start, end)
        return Entry(
            role,
            node["symbolType"],
            node["name"],
            _label(node.get("signature") or "", node["name"]),
            start,
            end,
            first,
            last,
            tuple(entry(m, "member") for m in node.get("members") or ()),
        )

    items = files[0]["items"] if files else []
    out = [entry(i, "import" if i.get("isImport") else "item") for i in items]
    return sorted(out, key=lambda e: (e.start, e.end, e.name)), None


def native(view: Any, path: str) -> tuple[list[Entry] | None, str | None]:
    """Arm C: rendered from the store's entities and relations; nothing is parsed.

    Items are the file's ``contains`` children, members their ``contains`` children,
    labels the indexed signature (else the name). Imports are the file's ``imports``
    relations, resolved or not: one entry per maximal run of consecutive lines holding
    import evidence, labelled with the imported names (OXIDE keeps no statement range).
    """
    from oxide.models import SourceRef

    info = view.files.get(path)
    if info is None or info.coverage == "unsupported":
        return None, "not parsed by OXIDE"
    key = f"file:{path}"
    data = view.source(SourceRef(path, 0, info.size, info.digest))
    lines = Lines(data)
    tops = [b for kind, b in view.adjacency([key])[key][0] if kind == "contains"]
    below = view.adjacency(tops)
    inner = {t: [b for kind, b in below[t][0] if kind == "contains"] for t in tops}
    known = view.entities([*tops, *(m for ms in inner.values() for m in ms)])

    def entry(k: str, role: str, members: tuple[Entry, ...] = ()) -> Entry | None:
        e = known.get(k)
        if e is None or e.source is None:
            return None
        first, last = lines.span(e.source.start, e.source.end)
        label = _label(e.signature or "", e.name)
        return Entry(
            role, e.kind, e.name, label, e.source.start, e.source.end, first, last, members
        )

    out: list[Entry] = []
    for t in tops:
        members = tuple(
            sorted(
                (m for m in (entry(k, "member") for k in inner[t]) if m is not None),
                key=lambda m: (m.start, m.end, m.name),
            )
        )
        item = entry(t, "item", members)
        if item is not None:
            out.append(item)
    sites: dict[int, list[tuple[int, int, str]]] = {}
    for r in view.relations(key):
        if r.kind == "imports" and r.evidence is not None and r.evidence.path == path:
            name = r.name or (r.target or "").split("#")[-1].split(":", 1)[-1]
            n = lines.line(r.evidence.start)
            sites.setdefault(n, []).append((r.evidence.start, r.evidence.end, name))
    run: list[int] = []
    for n in sorted(sites) + [-1]:
        if run and n != run[-1] + 1:
            found = sorted(s for m in run for s in sites[m])
            names = list(dict.fromkeys(name for *_, name in found))
            label = _label(", ".join(names), "imports")
            start, end = found[0][0], max(e for _, e, _ in found)
            out.append(
                Entry("import", "imports", ",".join(names), label, start, end, run[0], run[-1])
            )
            run = []
        if n > 0:
            run.append(n)
    return sorted(out, key=lambda e: (e.start, e.end, e.name)), None


def header_view(view: Any, path: str) -> str:
    """Arm A's file view as production packs a hinted file: its header view, else all
    of its module-level source (packing.pack, ``outline`` branch)."""
    from oxide.core import packing

    key = f"file:{path}"
    tops = [b for kind, b in view.adjacency([key])[key][0] if kind == "contains"]
    known = view.entities(tops)
    decls = {key: [(e.source.start, e.source.end) for e in known.values() if e.source]}
    entity = view.entities([key])[key]
    packer = packing._Packer(1 << 40, view.source, decls)
    pieces = packing._Packer.view(packer, entity, True, "") or packing._Packer.view(packer, entity, False, "")
    return "".join(i.text for i in packer.render(pieces) or [])


def _range(e: Entry) -> str:
    return f"L{e.first}" if e.first == e.last else f"L{e.first}-{e.last}"


def render(path: str, nlines: int, entries: list[Entry] | None) -> str:
    """The one outline rendering both arms share (navigation prompt and payload)."""
    head = f"### {path} ({nlines} lines) outline\n"
    if entries is None:
        return head + "(outline unavailable)\n"
    rows = []
    for e in entries:
        rows.append(f"{_range(e)} {e.kind}: {e.label}")
        rows += [f"  {_range(m)} {m.kind}: {m.label}" for m in e.members]
    return head + "".join(r + "\n" for r in rows)
