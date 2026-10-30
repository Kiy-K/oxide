"""G++ call-graph oracle for the C++ slice's call edges (docs/cpp.md, ../README.md).

Compiles each unit matching UNIT globs with ``g++ -O0 -fcallgraph-info`` (as the build
would, with FLAGS) and reads every direct call: its site (file and line; GCC's column is
an outer call's ``(`` for calls in its arguments, a temporary's ``)``), the callee's
mangled name, and the definition GCC kept for it. A callee is defined where some unit that
defines it (``nm``: T, W, t or w) places its node. Every oxide ``calls`` edge is matched
to the GCC callees of the same name (``c++filt``, without scope and template
arguments) on the line of its ``(`` and judged: a resolved edge agrees when its target is a function
or method whose source holds such a callee's definition line in its file, for each
such callee that a compiled unit defines (a template's call sites bind once per
instantiation); a resolved
prototype agrees with the declaration GCC kept when no compiled unit defines it there. GCC reports a
macro's expansion at its use site, so edges to macros are counted, not judged. Units
that fail to compile are listed and skipped.

usage: uv run python docs/phase-6/cpp-v1/oracle/oracle.py ROOT OUT.json UNIT... -- FLAGS...
"""

from __future__ import annotations

import collections
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from oxide.capture import capture
from oxide.core.indexing import derive

_EDGE = re.compile(r'edge: \{ sourcename: "[^"]*" targetname: "([^"]*)" label: "([^"]*)" \}')
_NODE = re.compile(r'node: \{ title: "([^"]*)" label: "(?:[^"\\]|\\.)*?\\n([^"]*?):(\d+):\d+"')


def _name(demangled: str) -> str:
    """The unqualified function name of ``c++filt -p`` output, template arguments and
    ABI tags dropped: ``f`` of ``a::B<int>::f<char>[abi:cxx11]``."""
    depth, start = 0, 0
    for n, ch in enumerate(demangled):
        depth += (ch in "<([") - (ch in ">)]")
        if depth == 0 and demangled.startswith("::", n):
            start = n + 2
    last = demangled[start:]
    cut = [n for n in (last.find("<"), last.find("[")) if n > 0 and not last.startswith("operator")]
    return last[: min(cut)] if cut else last


def main(root: Path, out: Path, units: list[str], flags: list[str]) -> None:
    calls: dict[tuple[str, int], set[str]] = collections.defaultdict(set)
    defined: dict[str, set[tuple[str, int]]] = collections.defaultdict(set)
    declared: dict[str, set[tuple[str, int]]] = collections.defaultdict(set)  # as GCC saw
    failed: list[str] = []
    paths = sorted({p for pattern in units for p in root.glob(pattern)})
    with tempfile.TemporaryDirectory() as tmp:
        for n, unit in enumerate(paths):
            obj = Path(tmp) / f"u{n}.o"
            cmd = ["g++", "-c", "-O0", "-fcallgraph-info", "-fdiagnostics-column-unit=byte"]
            cmd += [*flags, str(unit.resolve()), "-o", str(obj)]
            if subprocess.run(cmd, cwd=root, capture_output=True).returncode:
                failed.append(unit.relative_to(root).as_posix())
                continue
            text = obj.with_suffix(".ci").read_text()

            def rel(path: str) -> str:  # system headers stay absolute
                full = (root / path).resolve()
                return full.relative_to(root).as_posix() if full.is_relative_to(root) else str(full)

            at = {title: (rel(where), int(line)) for title, where, line in _NODE.findall(text)}
            symbols = subprocess.run(["nm", str(obj)], capture_output=True, text=True).stdout
            for line in symbols.splitlines():
                fields = line.split()
                if len(fields) == 3 and fields[1] in "TWtw" and fields[2] in at:
                    defined[fields[2]].add(at[fields[2]])
            for title, where in at.items():
                declared[title.rpartition(":")[2]].add(where)
                if ":" in title.split("_Z", 1)[0]:  # internal: prefixed by its unit
                    defined[title].add(where)
            for target, label in _EDGE.findall(text):
                path, line, _ = label.rsplit(":", 2)
                calls[(rel(path), int(line))].add(target)
    mangled = sorted({t for targets in calls.values() for t in targets})
    plain = [t.rpartition(":")[2] for t in mangled]  # internal titles carry their unit
    demangled = subprocess.run(
        ["c++filt", "-p"], input="\n".join(plain), capture_output=True, text=True
    ).stdout.splitlines()
    names = {t: _name(d) for t, d in zip(mangled, demangled, strict=True)}
    snapshot = capture(root)
    derived = derive(snapshot, "oracle")
    data = {f.path: f.data for f in snapshot.files}
    entities = {e.key: e for e in derived.entities}
    tally: collections.Counter[str] = collections.Counter()
    disagreements: list[dict[str, object]] = []
    missed: list[dict[str, object]] = []  # gcc binds a captured definition; oxide does not
    unseen: list[dict[str, object]] = []  # resolved in a unit; no gcc call of that name
    for edge in derived.edges:
        ev = edge.evidence
        if edge.kind != "calls" or ev is None:
            continue
        source = data[ev.path]
        paren = source.find(b"(", ev.end)
        before = source[:paren]
        site = (ev.path, before.count(b"\n") + 1)
        targets = [entities[t] for t in edge.targets]
        if targets and all(t.kind == "macro" for t in targets):
            tally[f"{edge.resolution} to a macro, not judged"] += 1
            continue
        name = targets[0].name if edge.resolution == "resolved" else edge.name or ""
        name = name.rpartition("::")[2].rpartition("->")[2]
        same = sorted(t for t in calls.get(site, ()) if names[t] == name)
        if not same:
            where = "unit" if any(Path(ev.path).match(u) for u in units) else "header"
            tally[f"{edge.resolution}, no gcc call ({where})"] += 1
            if where == "unit" and edge.resolution == "resolved":
                unseen.append({"site": site, "oxide": edge.targets[0]})
            continue
        bound = [b for t in same if (b := sorted(defined.get(t, ())))]
        if not bound:  # defined in no compiled unit: GCC does not say where
            tally[f"{edge.resolution}, gcc definition not compiled"] += 1
            continue
        local = [b for b in bound if any(not p.startswith("/") for p, _ in b)]
        if edge.resolution != "resolved":
            tally[f"{edge.resolution}, gcc {'captured' if local else 'external'}"] += 1
            if local:
                missed.append({"site": site, "oxide": edge.resolution, "gcc": local})
            continue
        (target,) = targets
        assert target.source is not None
        path, text = target.source.path, data[target.source.path]
        first = text[: target.source.start].count(b"\n") + 1
        last = text[: target.source.end].count(b"\n") + 1
        good = [
            target.kind in ("function", "method")
            and any(p == path and first <= line <= last for p, line in where)
            for where in bound
        ]
        if not any(good) and target.signature and target.signature.endswith(";"):
            seen = [sorted(declared.get(t.rpartition(":")[2], ())) for t in same]
            if any(p == path and first <= line <= last for w in seen for p, line in w):
                tally["resolved to the declaration GCC saw, defined elsewhere"] += 1
                continue
        verdict = "agree" if all(good) else "disagree" if not any(good) else "agree for some"
        tally[f"resolved, {verdict}"] += 1
        if verdict != "agree":
            gcc = [[t, sorted(defined.get(t, ()))] for t in same]
            disagreements.append({"site": site, "oxide": target.key, "gcc": gcc})
    captured = collections.Counter(
        "unit" if any(Path(site[0]).match(u) for u in units) else "header"
        for site, targets in calls.items()
        if not site[0].startswith("/")
        for t in targets
        if any(not p.startswith("/") for p, _ in defined.get(t, ()))
    )
    report = {
        "units": len(paths) - len(failed),
        "failed_units": failed,
        "gcc_call_sites": len(calls),
        "gcc_calls_to_captured_definitions": dict(sorted(captured.items())),
        "tally": dict(sorted(tally.items())),
    }
    full = {**report, "disagreements": disagreements, "missed": missed, "unseen": unseen}
    out.write_text(json.dumps(full, indent=1) + "\n")
    print(json.dumps(report, indent=1))


if __name__ == "__main__":
    args = sys.argv[1:]
    split = args.index("--") if "--" in args else len(args)
    main(Path(args[0]).resolve(), Path(args[1]), args[2:split], args[split + 1 :])
