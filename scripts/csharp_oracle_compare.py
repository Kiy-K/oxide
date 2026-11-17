"""Compare OXIDE's resolved C# edges with the Roslyn oracle (scripts/csharp_oracle).

Usage: dotnet scripts/csharp_oracle/... <root> > edges.jsonl
       uv run python scripts/csharp_oracle_compare.py <root> edges.jsonl

A resolved ``calls``/``references`` edge *agrees* when the oracle binds the same site
(file, start) to a declaration inside one of the edge's target entities; it *disagrees*
when the oracle binds that site elsewhere, and is *unbound* when the oracle binds it to
nothing in the tree (external or unresolved there). Recall is the share of oracle edges
OXIDE resolved at the same site.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

from oxide.capture import capture
from oxide.core.indexing import derive


def utf16_column(text: str, col: int) -> int:
    """Python string index for Roslyn's UTF-16 code-unit column (astral chars count 2)."""
    units = 0
    for index, char in enumerate(text):
        if units >= col:
            return index
        units += 2 if ord(char) > 0xFFFF else 1
    return len(text)


def main(root: Path, edges: Path) -> None:
    cap = capture(root)
    texts = {f.path: f.data for f in cap.files if f.path.endswith(".cs")}
    offsets: dict[str, list[int]] = {}

    def byte(path: str, line: int, col: int) -> int:
        if path not in offsets:
            starts, at = [], 0
            for piece in texts[path].splitlines(keepends=True):
                starts.append(at)
                at += len(piece)
            offsets[path] = starts
        text = texts[path][offsets[path][line] :].decode("utf-8", "replace")
        return offsets[path][line] + len(text[: utf16_column(text, col)].encode("utf-8"))

    oracle: dict[tuple[str, str, int], list[tuple[str, int]]] = {}
    for raw in edges.read_text().splitlines():
        e = json.loads(raw)
        f = e["from"]
        to = [t for t in e["to"] if t["file"] in texts]
        if f["file"] in texts and to:
            site = (e["kind"], f["file"], byte(f["file"], f["line"], f["col"]))
            oracle[site] = [(t["file"], byte(t["file"], t["line"], t["col"])) for t in to]
    result = derive(cap, "oracle")
    entities = {e.key: e for e in result.entities}
    tally: Counter[str] = Counter()
    seen: set[tuple[str, str, int]] = set()
    bad: list[str] = []
    for e in result.edges:
        if e.kind not in ("calls", "references") or e.evidence is None:
            continue
        if e.resolution != "resolved":
            continue
        kind = "call" if e.kind == "calls" else "ref"
        ev = e.evidence
        if ev.path not in texts or not ev.path.endswith(".cs"):
            continue
        site = (kind, ev.path, ev.start)
        seen.add(site)
        want = oracle.get(site)
        if want is None:
            tally[f"{kind} unbound"] += 1
            continue
        hit = any(
            (s := entities[t].source) is not None
            and any(s.path == p and s.start <= at < s.end for p, at in want)
            for t in e.targets
            if t in entities
        )
        tally[f"{kind} {'agree' if hit else 'disagree'}"] += 1
        if not hit and len(bad) < 25:
            bad.append(f"{ev.path}@{ev.start} {e.source} -> {e.targets} oracle {want}")
    for kind in ("call", "ref"):
        found = [s for s in oracle if s[0] == kind]
        tally[f"{kind} oracle-total"] = len(found)
        tally[f"{kind} oracle-resolved-by-oxide"] = sum(s in seen for s in found)
    for k, v in sorted(tally.items()):
        print(f"{k}: {v}")
    print("\n".join(bad))


if __name__ == "__main__":
    main(Path(sys.argv[1]).resolve(), Path(sys.argv[2]))
