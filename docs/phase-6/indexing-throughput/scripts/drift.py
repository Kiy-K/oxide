"""How far one file edit reaches: relation renumbering and cross-file invalidation.

    uv run python drift.py REPO [--edits 5]

Derives REPO once, then for each of ``--edits`` evenly spaced source files derives
twice more, once with a function appended to it and once with it deleted (in memory;
the corpus is untouched). Reports per edit:

- ``changed``: relations in only one derivation (multiset difference, ``site`` ignored);
- ``renumbered``: relations in both whose ordinal (today's ``site``) moved;
- ``other_files``: files other than the edited one whose relations changed (evidence
  path, else the source key's path): what per-file invalidation would have to find.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from dataclasses import replace
from typing import Any

from oxide.capture import capture, digest
from oxide.core.indexing import derive, dialect

APPEND = {
    "python": b"\n\ndef _bench_added():\n    return 1\n",
    "go": b"\nfunc benchAdded() int { return 1 }\n",
    "rust": b"\npub fn bench_added() -> i32 { 1 }\n",
}
APPEND_TS = b"\nexport function benchAdded(): number { return 1; }\n"


def owner(edge: Any) -> str:
    if edge.evidence is not None:
        return str(edge.evidence.path)
    key = edge.source.split(":", 1)[-1]
    return key.split("#", 1)[0]


def rows(derived: Any) -> list[tuple[Any, ...]]:
    return [
        (e.kind, e.source, e.targets, e.resolution, e.basis, e.name, e.evidence)
        for e in derived.edges
    ]


def compare(before: Any, after: Any, edited: str) -> dict[str, Any]:
    a, b = rows(before), rows(after)
    ca, cb = Counter(a), Counter(b)
    changed = (ca - cb) + (cb - ca)
    first = {r: i for i, r in reversed(list(enumerate(a)))}
    moved = sum(1 for i, r in enumerate(b) if r in first and ca[r] == 1 and first[r] != i)
    edges = {r: e for e, r in zip(before.edges, a, strict=True)}
    edges |= {r: e for e, r in zip(after.edges, b, strict=True)}
    others = {owner(edges[r]) for r in changed} - {edited}
    return {
        "changed": sum(changed.values()),
        "renumbered": moved,
        "renumbered_share": round(moved / max(len(b), 1), 3),
        "other_files": len(others),
        "entities_changed": len(set(before.entities) ^ set(after.entities)),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("repo")
    parser.add_argument("--edits", type=int, default=5)
    args = parser.parse_args()
    snap = capture(args.repo)
    base = derive(snap, "drift")
    sources = [i for i, f in enumerate(snap.files) if dialect(f.path)]
    picks = [
        sources[(k * len(sources)) // args.edits + len(sources) // (2 * args.edits)]
        for k in range(args.edits)
    ]
    out: list[dict[str, Any]] = []
    for i in picks:
        f = snap.files[i]
        lang = dialect(f.path) or ""
        data = f.data + APPEND.get(lang, APPEND_TS)
        appended = replace(f, data=data, digest=digest(data))
        files = list(snap.files)
        for name, edit in (
            ("append", [*files[:i], appended, *files[i + 1 :]]),
            ("delete", files[:i] + files[i + 1 :]),
        ):
            result = compare(base, derive(replace(snap, files=tuple(edit)), "drift"), f.path)
            out.append({"edit": name, "file": f.path} | result)
            print(json.dumps(out[-1]), flush=True)
    print(json.dumps({"repo": args.repo, "relations": len(base.edges), "edits": out}))


if __name__ == "__main__":
    main()
