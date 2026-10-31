"""Bounded C++ ContextBench diagnostic (docs/phase-6/cpp-v1), without running repository code.

rows PARQUET OUT: the C++ tasks of the SHA-checked ContextBench full.parquet selected
  by the rule fixed before scoring (``select``).
fetch ROWS DIR: a ``git archive`` projection of C/C++ sources and build files per base commit.
context ROWS CORPORA OUT: audit gold, then score with the unchanged ContextBench scorer.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import resource
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests/python"))
import contextbench as cb
import contextbench_ts as ts
import eval_javascript as ej
import tree_sitter_cpp
from tree_sitter import Language, Node, Parser

import oxide

PER_REPOSITORY = 3
MAX_FILES = 7500
LANGUAGE = Language(tree_sitter_cpp.language())
# docs/cpp.md's C++ extensions, plus .h (C or C++ by content) and .c.
SOURCES = (
    ".cc", ".cpp", ".cxx", ".c++", ".hh", ".hpp", ".hxx", ".h++", ".ipp", ".tpp", ".inl",
    ".h", ".c",
)  # fmt: skip
BUILD_FILES = ("CMakeLists.txt", "meson.build", "Makefile", "BUILD", "BUILD.bazel")
DECLARATIONS = (
    "function_definition",
    "template_declaration",
    "class_specifier",
    "struct_specifier",
    "union_specifier",
    "enum_specifier",
)
CONTAINERS = ("namespace_definition", "linkage_specification")


def select(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    """The first three C++ rows per repository by original instance id whose gold names
    a C/C++ source file. Fixed before any scoring; no other filter."""
    chosen: list[dict[str, object]] = []
    taken: Counter[str] = Counter()
    for row in sorted(rows, key=lambda r: str(r["original_inst_id"])):
        gold = json.loads(str(row["gold_context"]))
        if row["language"] != "cpp" or not any(g["file"].endswith(SOURCES) for g in gold):
            continue
        if taken[str(row["repo"])] < PER_REPOSITORY:
            taken[str(row["repo"])] += 1
            chosen.append(row)
    return chosen


def keep(name: str) -> bool:
    return name.endswith(SOURCES) or Path(name).name in BUILD_FILES


def module_lines(data: bytes) -> set[int]:
    """Lines outside the outermost function, template and class/struct/union/enum
    definitions (those inside namespaces and ``extern "C"`` blocks included): the scorer's
    own view through the grammar, independent of OXIDE's derivation. Macros stay opaque."""
    root = Parser(LANGUAGE).parse(data).root_node
    inside: set[int] = set()
    pending: list[Node] = list(root.named_children)
    while pending:
        node = pending.pop()
        if node.type in DECLARATIONS:
            inside.update(range(node.start_point[0] + 1, node.end_point[0] + 2))
        elif node.type in CONTAINERS:
            body = node.child_by_field_name("body")
            pending.extend(body.named_children if body is not None else [])
    return set(range(1, data.count(b"\n") + 2)) - inside


class Lines(cb.Lines):
    def module(self, path: str) -> set[int]:
        if not path.endswith(SOURCES):
            return super().module(path)
        if path not in self.modules:
            try:
                self.modules[path] = module_lines(self.file(path))
            except OSError:
                self.modules[path] = set()
        return self.modules[path]


def corpus(row: dict[str, object]) -> str:
    return str(row["repo"]).replace("/", "__") + "@" + str(row["base_commit"])[:12]


def context(rows_path: Path, corpora: Path, out: Path) -> None:
    rows = json.loads(rows_path.read_text())
    results, audits, identities, times = [], {}, {}, {}
    for row in rows:
        root = corpora / corpus(row)
        files = sum(1 for p in root.rglob("*") if p.is_file())
        assert files <= MAX_FILES, f"{root}: {files} files"
        gold, verdicts = [], []
        for g in json.loads(row["gold_context"]):
            if not g["file"].endswith(SOURCES):
                continue
            content, numbered = ts.unnumbered(g["content"], g["start_line"])
            start, end = g["start_line"], g["end_line"]
            verdict = cb.classify(root, g["file"], start, end, content, row["patch"])
            verdicts.append(verdict | {"numbered": numbered})
            gold.append(
                {
                    "file": verdict.get("corrected_file", g["file"]),
                    "start": start,
                    "end": end,
                    "sha256": cb._sha(cb.normalized(g["content"])),
                    "class": verdict["class"],
                    "verified": verdict["class"] in cb.VERIFIED,
                }
            )
        task = {
            "id": row["original_inst_id"],
            "split": "heldout",
            "corpus": corpus(row),
            "query": row["problem_statement"],
            "symbols": [],
            "gold": gold,
        }
        audits[task["id"]] = verdicts
        with (
            tempfile.TemporaryDirectory(prefix="oxide-contextbench-cpp-") as temporary,
            oxide.open_repository(root, data_dir=Path(temporary)) as repo,
        ):
            report = repo.index()
            started = time.perf_counter()
            result = cb.score(repo, Lines(root), task)
            assert all(
                b["within_budget"] and b["deterministic"] for b in result["bundles"].values()
            )
            results.append(result)
            times[task["id"]] = {
                "index_s": report.seconds,
                "score_s": time.perf_counter() - started,
            }
            identities[task["corpus"]] = {
                "snapshot": report.snapshot.snapshot_id,
                "commit": row["base_commit"],
                "files": report.files,
                "entities": report.entities,
                "relations": report.relations,
                "partial": list(report.partial),
            }
    ej.save(out / "results.json", results)
    ej.save(out / "metrics.json", cb.aggregate(results))
    ej.save(out / "gold-audit.json", audits)
    ej.save(
        out / "manifest.json",
        {
            "source_sha256": cb.PARQUET_SHA256,
            "rows_sha256": hashlib.sha256(rows_path.read_bytes()).hexdigest(),
            "selection": [r["original_inst_id"] for r in rows],
            "corpora": identities,
            "budgets": cb.BUDGETS,
            "scorer": "contextbench-py-scorer with C++ module projection v1",
        },
    )
    ej.save(
        out / "performance.json",
        {"tasks": times, "peak_rss_mib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024},
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="mode", required=True)
    for mode, names in (
        ("rows", ("parquet", "out")),
        ("fetch", ("rows", "directory")),
        ("context", ("rows", "corpora", "out")),
    ):
        mode_parser = sub.add_parser(mode)
        for name in names:
            mode_parser.add_argument(name, type=Path)
    args = parser.parse_args()
    if args.mode == "rows":
        ej.save(args.out, select(cb._parquet(args.parquet)))
    elif args.mode == "fetch":
        ej.fetch(args.rows, args.directory, keep)
    else:
        context(args.rows, args.corpora, args.out)
