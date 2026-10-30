"""GCC call-graph oracle for the C slice's call edges (docs/c.md, ../README.md).

Compiles every ``.c`` file under ``SRC`` with ``gcc -fcallgraph-info`` (each
translation unit as the build would) and reads each real call: its site
(file:line:byte column), the callee name, and the callee's file when it is static.
Every oxide ``calls`` edge from those units whose evidence starts at such a site is
judged: a resolved edge agrees when its target is a function definition of that name
in the file that defines it (the static's own file, else the file that defines it
externally). GCC reports a macro's expansion at its use site, so edges to macros are
counted, not judged. A site inside a macro argument may carry another column: the one
same-line gcc call of a target's name stands in for it.

usage: uv run python docs/phase-6/c-v1/oracle/oracle.py ROOT SRC OUT.json [CFLAGS...]
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
_NODE = re.compile(r'node: \{ title: "([^"]*)" label: "[^\\"]*\\n([^"]*?):\d+:\d+')


def main(root: Path, src: Path, out: Path, flags: list[str]) -> None:
    calls: dict[tuple[str, int, int], tuple[str, str | None]] = {}
    defined: dict[str, set[str]] = collections.defaultdict(set)  # external name -> files
    with tempfile.TemporaryDirectory() as tmp:
        for unit in sorted(src.glob("*.c")):
            ci = Path(tmp) / f"{unit.stem}.ci"
            cmd = ["gcc", "-c", "-O0", "-fcallgraph-info", "-fdiagnostics-column-unit=byte"]
            cmd += [*flags, str(unit.resolve()), "-o", str(Path(tmp) / f"{unit.stem}.o")]
            subprocess.run(cmd, check=True, cwd=tmp)
            text = ci.read_text()

            def rel(path: str) -> str:  # system headers stay absolute
                full = (Path(tmp) / path).resolve()
                return full.relative_to(root).as_posix() if full.is_relative_to(root) else str(full)

            # A node's label is the declaration gcc kept: the definition when there is one.
            at = {title: rel(where) for title, where in _NODE.findall(text)}
            symbols = subprocess.run(["nm", f"{unit.stem}.o"], cwd=tmp, capture_output=True)
            for line in symbols.stdout.decode().splitlines():  # external text definitions
                if line.split()[-2:-1] == ["T"] and line.split()[-1] in at:
                    defined[line.split()[-1]].add(at[line.split()[-1]])
            for target, label in _EDGE.findall(text):
                path, line, col = label.rsplit(":", 2)
                site = (rel(path), int(line), int(col))
                static = ":" in target  # prefixed by its unit; defined where its node says
                calls[site] = (target.rpartition(":")[2], at.get(target) if static else None)
    derived = derive(capture(root), "oracle")
    data = {f.path: f.data for f in capture(root).files}
    entities = {e.key: e for e in derived.entities}
    lines: dict[tuple[str, int], list[tuple[str, int, int]]] = collections.defaultdict(list)
    for known in calls:
        lines[known[:2]].append(known)
    tally: collections.Counter[str] = collections.Counter()
    disagreements: list[dict[str, object]] = []
    unseen: list[dict[str, object]] = []  # resolved, but no call in this build
    missed: list[dict[str, object]] = []  # gcc binds a captured definition; oxide does not
    for edge in derived.edges:
        ev = edge.evidence
        if edge.kind != "calls" or ev is None or not ev.path.endswith(".c"):
            continue
        if Path(ev.path).parent != Path(src.relative_to(root)):  # the compiled units only
            continue
        before = data[ev.path][: ev.start]
        site = (ev.path, before.count(b"\n") + 1, ev.start - (before.rfind(b"\n") + 1) + 1)
        targets = [entities[t] for t in edge.targets]
        if targets and all(t.kind == "macro" for t in targets):  # gcc sees the expansion
            tally[f"{edge.resolution} to a macro, not judged"] += 1
            continue
        if site not in calls:  # in a macro argument gcc reports another column: same line
            names = {t.name for t in targets}
            same = [at for at in lines.get(site[:2], []) if calls[at][0] in names]
            site = same[0] if len(same) == 1 else site
        if site not in calls:
            tally[f"{edge.resolution}, no gcc call"] += 1
            if edge.resolution == "resolved":
                unseen.append({"site": site, "oxide": edge.targets[0]})
            continue
        name, owner = calls[site]
        files = {owner} if owner else defined.get(name, set())
        if edge.resolution != "resolved":
            local = len(files) == 1
            tally[f"{edge.resolution}, gcc {'resolved' if local else 'external'}"] += 1
            if local:
                missed.append(
                    {"site": site, "oxide": edge.resolution, "gcc": [name, sorted(files)]}
                )
            continue
        (target,) = targets
        assert target.source is not None
        good = target.kind == "function" and target.name == name and {target.source.path} == files
        tally["resolved, agree" if good else "resolved, disagree"] += 1
        if not good:
            disagreements.append({"site": site, "oxide": target.key, "gcc": [name, sorted(files)]})
    report = {"gcc_calls": len(calls), "tally": dict(sorted(tally.items()))}
    full = {**report, "disagreements": disagreements, "missed": missed, "unseen": unseen}
    out.write_text(json.dumps(full, indent=1) + "\n")
    print(json.dumps(report, indent=1))


if __name__ == "__main__":
    main(Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve(), Path(sys.argv[3]), sys.argv[4:])
