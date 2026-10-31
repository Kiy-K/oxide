"""Targets and jedi truth (preregistration § Targets, § Truth). Run with the jedi venv:

$OXIDE_GRAPHNAV_DATA/../oxide-graph-eval/jedi/venv/bin/python oracle.py
"""

import json
import random
import re
import sys

import jedi
from common import CORPORA, DATA, RESULTS, Enclosing, checkout

SEED = 20261025
PER_STRATUM = 4
SKIP = ("tests/", "testing/", "docs/", "examples/")


class Oracle:
    def __init__(self, corpus: str) -> None:
        self.root = checkout(corpus)
        src = self.root / "src"
        self.project = jedi.Project(
            path=self.root,
            added_sys_path=[str(src)] if src.is_dir() else [],
            load_unsafe_extensions=False,
            smart_sys_path=True,
        )
        self.rows = json.loads((DATA / f"entities-{corpus}.json").read_text())
        self.enclosing = Enclosing(self.rows)
        self.data: dict[str, bytes] = {}
        self.cache: dict[str, tuple[set[str], list[list]]] = {}

    def bytes_of(self, path: str) -> bytes:
        if path not in self.data:
            self.data[path] = (self.root / path).read_bytes()
        return self.data[path]

    def offset(self, path: str, line: int, column: int) -> int:
        data = self.bytes_of(path)
        start = 0
        for _ in range(line - 1):
            start = data.index(b"\n", start) + 1
        text = data[start:].split(b"\n", 1)[0].decode("utf-8", "replace")
        return start + len(text[:column].encode("utf-8"))

    def dependents(self, key: str) -> tuple[set[str], list[list]]:
        """Enclosing entities of every non-definition reference site, and the sites."""
        if key in self.cache:
            return self.cache[key]
        _, _, _, name, path, start, end, _ = self.enclosing.info[key]
        data = self.bytes_of(path)
        found = re.compile(rb"(?:def|class)\s+(" + re.escape(name.encode()) + rb")\b").search(
            data, start, end
        )
        out: tuple[set[str], list[list]] = (set(), [])
        if found is None:
            self.cache[key] = out
            return out
        at = found.start(1)
        line = data.count(b"\n", 0, at) + 1
        column = len(data[data.rfind(b"\n", 0, at) + 1 : at].decode("utf-8", "replace"))
        script = jedi.Script(
            code=data.decode("utf-8", "replace"), path=self.root / path, project=self.project
        )
        try:
            refs = script.get_references(line, column, include_builtins=False)
        except Exception as error:
            print("jedi failed", key, repr(error), file=sys.stderr)
            refs = []
        for ref in refs:
            if ref.module_path is None or ref.line is None or ref.column is None:
                continue
            try:
                rel = str(ref.module_path.relative_to(self.root))
            except ValueError:
                continue
            if not rel.endswith(".py") or rel not in self.enclosing.file:
                continue
            tree = getattr(ref._name, "tree_name", None)
            node = tree.get_definition(import_name_always=True) if tree is not None else None
            is_import = node is not None and node.type in ("import_from", "import_name")
            if ref.is_definition() and not is_import:
                continue
            site = self.offset(rel, ref.line, ref.column)
            owner = self.enclosing.at(rel, site)
            if owner is None or owner == key:
                continue
            out[0].add(owner)
            out[1].append([rel, site, owner])
        self.cache[key] = out
        return out


def candidates(oracle: Oracle) -> list[list]:
    pool = [
        r
        for r in oracle.rows
        if r[1] == "symbol"
        and r[2] in ("function", "method")
        and not r[7]
        and not r[4].startswith(SKIP)
        and not r[3].startswith("__")
        and (r[2] == "method" or "." not in r[0].split("#", 1)[1])
    ]
    random.Random(SEED).shuffle(pool)
    return pool


def main() -> None:
    out = []
    for corpus in CORPORA:
        oracle = Oracle(corpus)
        taken = {"function": 0, "method": 0}
        for row in candidates(oracle):
            key, kind = row[0], row[2]
            if taken[kind] >= PER_STRATUM:
                if all(v >= PER_STRATUM for v in taken.values()):
                    break
                continue
            direct, sites = oracle.dependents(key)
            if len({oracle.enclosing.info[d][4] for d in direct}) < 2:
                continue
            taken[kind] += 1
            two = set(direct)
            for d in sorted(direct):
                if oracle.enclosing.info[d][2] in ("function", "method"):
                    two |= oracle.dependents(d)[0]
            two.discard(key)
            out.append(
                {
                    "corpus": corpus,
                    "key": key,
                    "kind": kind,
                    "name": row[3],
                    "q1": sorted(direct),
                    "q2": sorted(two),
                    "sites": sites,
                }
            )
            print(corpus, kind, key, len(direct), len(two), flush=True)
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "truth.json").write_text(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
