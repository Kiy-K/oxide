"""ContextBench-py: the Python engine's context-quality harness and frozen baseline.

``docs/phase-6/baseline-v1/README.md`` is the protocol: tasks, gold, metrics and the
gate rules ``test_contextbench.py`` enforces. Gold comes from the fixture benchmark
files (mapped to lines with ``ast``) and from ContextBench's human annotations, never
from OXIDE's output. Every run indexes into a fresh private data location::

    uv run python tests/python/contextbench.py fetch            # pinned corpora -> cache
    uv run python tests/python/contextbench.py run [--out DIR]  # evaluate every task
    uv run --with pyarrow python tests/python/contextbench.py tasks PARQUET  # once
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import importlib
import io
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from collections import Counter
from collections.abc import Iterable
from importlib.metadata import version
from pathlib import Path
from typing import Any

import oxide
from oxide.core import packing, retrieval, routing
from oxide.models import SourceRef

REPO = Path(__file__).resolve().parents[2]
BASELINE = REPO / "docs" / "phase-6" / "baseline-v1"
SCORER = "contextbench-py-scorer-v1"
TASKS_VERSION = "contextbench-py-tasks-v1"
BUDGETS = (256, 1024, 4096)
TOLERANCE = 0.02
GATED = (
    "entry_recall@5",
    "entry_recall@20",
    "entry_mrr",
    "routed_coverage",
    *(f"{m}@{b}" for m in ("span_recall", "line_recall") for b in BUDGETS),
)
# The cost rule of the protocol, checked again on every fetch.
MAX_PY_FILES = 250
ELIGIBLE = (
    "psf/requests",
    "pallets/flask",
    "mwaskom/seaborn",
    "pydata/xarray",
    "pytest-dev/pytest",
)
HELDOUT = ("pydata/xarray", "pytest-dev/pytest")
PARQUET_SHA256 = "2f56535bdc73eb8a68bf4ebb49789d8e9cd4f219ea60df6290b85278aee61ca8"
Span = tuple[str, int, int]


def cache_dir() -> Path:
    xdg = os.environ.get("XDG_CACHE_HOME", "")
    base = Path(xdg) if os.path.isabs(xdg) else Path.home() / ".cache"
    return base / "oxide" / "contextbench"


# Resolved at import, before tests redirect XDG locations (like the FTS extension).
CACHE = cache_dir()


def _sha(data: bytes | str) -> str:
    return hashlib.sha256(data.encode() if isinstance(data, str) else data).hexdigest()


def normalized(text: str) -> str:
    """Gold content compared without indentation or blank lines (ContextBench dedents)."""
    return "\n".join(line.strip() for line in text.splitlines() if line.strip())


def load(directory: Path = BASELINE, name: str = "tasks.json") -> Any:
    return json.loads((directory / name).read_text())


# --- tasks: built once from the fixture benchmarks and the ContextBench parquet ---


def _ast_span(root: Path, gold: str) -> Span:
    path, dotted = gold.split("#")
    body = ast.parse((root / path).read_bytes()).body
    node: ast.stmt | None = None
    for name in dotted.split("."):
        found = [
            n
            for n in body
            if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
            and n.name == name
        ]
        if len(found) != 1:
            raise ValueError(f"{gold}: {len(found)} declarations named {name!r}")
        node = found[0]
        body = node.body
    assert node is not None and node.end_lineno is not None
    return path, node.lineno, node.end_lineno


def build_tasks(parquet: Path) -> dict[str, Any]:
    if _sha(parquet.read_bytes()) != PARQUET_SHA256:
        raise SystemExit(f"{parquet} is not ContextBench full.parquet {PARQUET_SHA256}")
    tasks: list[dict[str, Any]] = []
    corpora: dict[str, dict[str, str]] = {"fixture:py_repo": {"path": "fixtures/py_repo"}}
    fixture = REPO / "fixtures" / "py_repo"
    for source in ("benchmark.json", "structural_benchmark.json"):
        for query in json.loads((REPO / "fixtures" / source).read_text())["queries"]:
            if query["repo"] != "py":
                continue
            spans = [_ast_span(fixture, gold) for gold in query["relevant"]]
            tasks.append(
                {
                    "id": query["id"],
                    "split": "dev",
                    "corpus": "fixture:py_repo",
                    "query": query["text"],
                    "symbols": [query["anchor_symbol"]] if "anchor_symbol" in query else [],
                    "source": f"fixtures/{source}#{query['id']}",
                    "gold": [
                        {"file": f, "start": s, "end": e, "symbol": g}
                        for (f, s, e), g in zip(spans, query["relevant"], strict=True)
                    ],
                }
            )
    rows = importlib.import_module("pyarrow.parquet").read_table(parquet).to_pylist()
    for row in sorted(rows, key=lambda r: str(r["original_inst_id"])):
        if row["language"] != "python" or row["repo"] not in ELIGIBLE:
            continue
        name = f"{row['repo'].replace('/', '__')}@{row['base_commit'][:12]}"
        corpora[name] = {"url": f"https://github.com/{row['repo']}", "commit": row["base_commit"]}
        tasks.append(
            {
                "id": row["original_inst_id"],
                "split": "heldout" if row["repo"] in HELDOUT else "dev",
                "corpus": name,
                "query": row["problem_statement"],
                "symbols": [],
                "source": f"ContextBench full.parquet#{row['instance_id']}",
                "gold": [
                    {
                        "file": g["file"],
                        "start": g["start_line"],
                        "end": g["end_line"],
                        "sha256": _sha(normalized(g["content"])),
                    }
                    for g in json.loads(row["gold_context"])
                ],
            }
        )
    return {
        "version": TASKS_VERSION,
        "sources": {
            "fixture": "fixtures/benchmark.json, fixtures/structural_benchmark.json (repo py)",
            "contextbench": f"EuniAI/ContextBench data/full.parquet sha256 {PARQUET_SHA256}",
        },
        "corpora": dict(sorted(corpora.items())),
        "tasks": tasks,
    }


# --- corpora: pinned commits fetched explicitly, never during evaluation ---


def corpus_root(name: str, corpus: dict[str, str]) -> Path:
    return REPO / corpus["path"] if "path" in corpus else CACHE / "corpora" / name


def _git(*args: str | Path) -> bytes:
    return subprocess.run(["git", *map(str, args)], capture_output=True, check=True).stdout


def fetch(corpora: dict[str, dict[str, str]]) -> None:
    for name, corpus in corpora.items():
        target = corpus_root(name, corpus)
        if "url" not in corpus or target.is_dir():
            continue
        bare = CACHE / "git" / (name.split("@")[0] + ".git")
        if not bare.is_dir():
            _git("init", "-q", "--bare", bare)
        print(f"fetching {name}", file=sys.stderr)
        _git("-C", bare, "fetch", "-q", "--depth", "1", corpus["url"], corpus["commit"])
        archive = _git("-C", bare, "archive", "--format=tar", corpus["commit"], "--", "*.py")
        partial = target.with_name(target.name + ".partial")
        shutil.rmtree(partial, ignore_errors=True)
        with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
            tar.extractall(partial, filter="data")
        if (count := len(list(partial.rglob("*.py")))) > MAX_PY_FILES:
            raise SystemExit(f"{name}: {count} *.py files, more than the protocol's {MAX_PY_FILES}")
        os.replace(partial, target)


def identity(root: Path) -> dict[str, Any]:
    """The corpus independent of OXIDE: its ``*.py`` files and their digests."""
    files = sorted(p for p in root.rglob("*.py") if p.is_file())
    lines = [f"{p.relative_to(root).as_posix()}\0{_sha(p.read_bytes())}" for p in files]
    return {"py_files": len(files), "tree_sha256": _sha("\n".join(lines))}


# --- scoring ---


class Lines:
    """Line ranges of byte ranges, read from the corpus itself."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.data: dict[str, bytes] = {}

    def file(self, path: str) -> bytes:
        if path not in self.data:
            self.data[path] = (self.root / path).read_bytes()
        return self.data[path]

    def span(self, ref: SourceRef) -> Span:
        data = self.file(ref.path)
        first = data.count(b"\n", 0, ref.start) + 1
        return ref.path, first, data.count(b"\n", 0, max(ref.start, ref.end - 1)) + 1

    def text(self, span: Span) -> str:
        path, start, end = span
        try:
            lines = self.file(path).decode("utf-8", "replace").splitlines()
        except OSError:
            return ""
        return "\n".join(lines[start - 1 : end])


def overlaps(a: Span, b: Span) -> bool:
    return a[0] == b[0] and a[1] <= b[2] and b[1] <= a[2]


def _mean(values: Iterable[float]) -> float:
    items = list(values)
    return round(sum(items) / len(items), 6) if items else 0.0


def score(repo: oxide.Repository, lines: Lines, task: dict[str, Any]) -> dict[str, Any]:
    query, symbols = task["query"], task["symbols"]
    gold: list[Span] = [(g["file"], g["start"], g["end"]) for g in task["gold"]]
    verified = [
        "sha256" not in g or _sha(normalized(lines.text(span))) == g["sha256"]
        for g, span in zip(task["gold"], gold, strict=True)
    ]
    gold_lines = {(f, n) for f, s, e in gold for n in range(s, e + 1)}
    gold_files = {f for f, _, _ in gold}

    found = repo.search(query, symbols=symbols)
    # Positions count every entry, modules (which have no source) included.
    entries = [
        (position, e.entity.category, lines.span(e.entity.source))
        for position, e in enumerate(found.entries, 1)
        if e.entity.source is not None
    ]
    ranks: list[int | None] = []
    for span in gold:
        hits = [i for i, c, r in entries if c == "symbol" and overlaps(r, span)]
        ranks.append(hits[0] if hits else None)
    first = min((r for r in ranks if r is not None), default=None)

    view = repo._view()
    route = routing.route(view, [e.entity.key for e in found.entries])
    known = view.entities(c.key for c in route.candidates)
    routed = {
        k: lines.span(e.source)
        for k, e in known.items()
        if e.category == "symbol" and e.source is not None
    }
    reached = [any(overlaps(r, span) for r in routed.values()) for span in gold]

    metrics: dict[str, float] = {
        **{f"entry_recall@{k}": _mean(r is not None and r <= k for r in ranks) for k in (1, 5, 20)},
        "entry_mrr": round(1 / first, 6) if first else 0.0,
        "entry_file_recall": _mean(f in {r[0] for _, _, r in entries} for f in gold_files),
        "routed_coverage": _mean(reached),
        "gold_verified": _mean(verified),
    }
    status: list[dict[str, str]] = [{} for _ in gold]
    lost: list[dict[str, list[str]]] = [{} for _ in gold]
    bundles: dict[str, Any] = {}
    compat: dict[str, str] = {
        "entries": _sha(json.dumps([e.entity.key for e in found.entries])),
        "routed": _sha(json.dumps(sorted((c.key, c.depth) for c in route.candidates))),
    }
    versions: dict[str, str] = {}
    for budget in BUDGETS:
        bundle = repo.build_context(query, budget, symbols=symbols)
        deterministic = bundle == repo.build_context(query, budget, symbols=symbols)
        versions = {**bundle.versions, "units": bundle.units}
        items = [((i.source.path, *i.lines), i) for i in bundle.items]
        covered = {(f, n) for (f, s, e), _ in items for n in range(s, e + 1)}
        omitted = {o.key: f"{o.stage}: {o.reason}" for o in bundle.omissions}
        header = {e.key for _, i in items for e in i.entities if not e.complete}
        for n, span in enumerate(gold):
            inside = [(span[0], line) in covered for line in range(span[1], span[2] + 1)]
            state = "complete" if all(inside) else "partial" if any(inside) else "missed"
            status[n][str(budget)] = state
            if state != "complete":
                keys = sorted(k for k, r in routed.items() if overlaps(r, span))
                why = {
                    "header view" if k in header else omitted.get(k, "graph limit") for k in keys
                }
                lost[n][str(budget)] = sorted(why) or ["unreached"]
        complete = [s[str(budget)] == "complete" for s in status]
        gold_units = sum(i.units for span, i in items if any(overlaps(span, g) for g in gold))
        metrics |= {
            f"span_recall@{budget}": _mean(complete),
            f"line_recall@{budget}": round(len(gold_lines & covered) / len(gold_lines), 6),
            f"file_recall@{budget}": _mean(f in {s[0] for s, _ in items} for f in gold_files),
            f"utilization@{budget}": round(bundle.used / budget, 6),
            f"gold_unit_share@{budget}": round(gold_units / bundle.used, 6) if bundle.used else 0.0,
        }
        reasons = Counter(re.sub(r"\d+(\.\d+)?", "N", r) for r in omitted.values())
        bundles[str(budget)] = {
            "used": bundle.used,
            "within_budget": bundle.used <= budget,
            "deterministic": deterministic,
            "items": len(bundle.items),
            "header_entities": len(header),
            "omissions": dict(sorted(reasons.items())),
            "degradations": list(bundle.degradations),
        }
        compat[f"payload@{budget}"] = _sha(bundle.payload)
    return {
        "task": task["id"],
        "split": task["split"],
        "corpus": task["corpus"],
        "metrics": metrics,
        "gold": [
            {
                "file": span[0],
                "lines": [span[1], span[2]],
                "verified": ok,
                "entry_rank": rank,
                "routed": hit,
                "packed": state,
                "lost": why,
            }
            for span, ok, rank, hit, state, why in zip(
                gold, verified, ranks, reached, status, lost, strict=True
            )
        ],
        "route": {
            "root_fallback": route.root_fallback,
            "regions": route.regions,
            "candidates": len(route.candidates),
            "deferred": route.deferred,
            "fanout_truncations": route.fanout_truncations,
            "stopped_by": route.stopped_by,
        },
        "bundles": bundles,
        "versions": versions,
        "compat": compat,
    }


def evaluate(tasks: dict[str, Any], data: Path) -> dict[str, Any]:
    """Every task, grouped by corpus; ``data`` must be a fresh private data location."""
    results: list[dict[str, Any]] = []
    corpora: dict[str, Any] = {}
    for name, corpus in tasks["corpora"].items():
        root = corpus_root(name, corpus)
        if not root.is_dir():
            raise FileNotFoundError(f"corpus {name} missing at {root}: run `mise run bootstrap`")
        with oxide.open_repository(root, data_dir=data) as repo:
            report = repo.index()
            corpora[name] = {
                **{k: v for k, v in corpus.items() if k != "path"},
                **identity(root),
                "snapshot": report.snapshot.snapshot_id,
                "derivation": report.snapshot.derivation_id,
                "partial_files": len(report.partial),
            }
            lines = Lines(root)
            results += [score(repo, lines, t) for t in tasks["tasks"] if t["corpus"] == name]
    order = {t["id"]: n for n, t in enumerate(tasks["tasks"])}
    results.sort(key=lambda r: order[r["task"]])
    return {"corpora": corpora, "results": results}


def aggregate(results: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = {}
    for split in ("dev", "heldout", "all"):
        chosen = [r["metrics"] for r in results if split in ("all", r["split"])]
        out[split] = {"tasks": len(chosen)} | {m: _mean(c[m] for c in chosen) for m in chosen[0]}
    return out


def manifest(evaluated: dict[str, Any]) -> dict[str, Any]:
    def git(*args: str) -> str:
        command = ["git", "-C", str(REPO), *args]
        return subprocess.run(command, capture_output=True, text=True).stdout.strip()

    head = git("rev-parse", "HEAD") + (" +dirty" if git("status", "--porcelain", "-uno") else "")
    return {
        "baseline": "contextbench-py-v1",
        "protocol": "docs/phase-6/baseline-v1/README.md",
        "scorer": SCORER,
        "tasks": TASKS_VERSION,
        "oxide_source": head or "unknown",
        "budgets": list(BUDGETS),
        "tolerance": TOLERANCE,
        "gated": list(GATED),
        "corpora": evaluated["corpora"],
        "versions": evaluated["results"][0]["versions"],
        "config": {
            "retrieval": {
                "lexical_limit": retrieval.LEXICAL_LIMIT,
                "symbol_limit": retrieval.SYMBOL_LIMIT,
            },
            "routing": {
                "max_regions": routing.MAX_REGIONS,
                "max_depth": routing.MAX_DEPTH,
                "max_fanout": routing.MAX_FANOUT,
                "max_edges": routing.MAX_EDGES,
                "follow": list(routing.FOLLOW),
            },
            "packing": {
                "graph_nodes": packing.GRAPH_NODES,
                "max_items": packing.MAX_ITEMS,
                "include_floor": packing.INCLUDE_FLOOR,
                "max_header_lines": packing.MAX_HEADER_LINES,
            },
            "decision_provider": "none (heuristic packing.value)",
        },
        "environment": {
            "python": platform.python_version(),
            "platform": f"{sys.platform}-{platform.machine()}",
            "packages": {p: version(p) for p in ("ladybug", "tree-sitter", "tree-sitter-python")},
        },
        "commands": {
            "fetch": "uv run python tests/python/contextbench.py fetch",
            "run": "uv run python tests/python/contextbench.py run --out DIR",
            "gate": "uv run pytest tests/python/test_contextbench.py",
            "tasks": "uv run --with pyarrow python tests/python/contextbench.py tasks PARQUET",
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("tasks", help="write tasks.json").add_argument("parquet", type=Path)
    commands.add_parser("fetch", help="fetch the pinned corpora into the cache")
    run = commands.add_parser("run", help="evaluate every task")
    run.add_argument("--out", type=Path, help="write results/metrics/manifest.json here")
    args = parser.parse_args(argv)
    if args.command == "tasks":
        text = json.dumps(build_tasks(args.parquet), indent=1, ensure_ascii=False)
        (BASELINE / "tasks.json").write_text(text + "\n")
        return 0
    tasks = load()
    if args.command == "fetch":
        fetch(tasks["corpora"])
        return 0
    with tempfile.TemporaryDirectory(prefix="oxide-contextbench-") as data:
        evaluated = evaluate(tasks, Path(data))
    metrics = aggregate(evaluated["results"])
    print(json.dumps(metrics, indent=1))
    if args.out:
        args.out.mkdir(parents=True, exist_ok=True)
        for name, value in (
            ("results.json", evaluated["results"]),
            ("metrics.json", metrics),
            ("manifest.json", manifest(evaluated)),
        ):
            (args.out / name).write_text(json.dumps(value, indent=1, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
