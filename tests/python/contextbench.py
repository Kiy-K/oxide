"""ContextBench-py: the Python engine's context-quality harness and frozen baseline.

``docs/phase-6/baseline-v4/README.md`` and the READMEs it amends are the protocol: tasks,
gold, its audit, metrics and the gate rules ``test_contextbench.py`` enforces. Gold
comes from the fixture benchmark files (mapped to lines with ``ast``) and from
ContextBench's human annotations, never from OXIDE's output. The annotations are kept
as built in ``baseline-v1/tasks.json``; ``baseline-v2/gold-audit.json`` records every
span's integrity verdict, with provenance, and is applied at load. Every run indexes
into a fresh private data location::

    uv run python tests/python/contextbench.py fetch            # pinned corpora -> cache
    uv run python tests/python/contextbench.py run [--out DIR]  # evaluate every task
    uv run --with pyarrow python tests/python/contextbench.py tasks PARQUET  # once
    uv run --with pyarrow python tests/python/contextbench.py audit PARQUET  # once
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
import time
import warnings
from collections import Counter
from collections.abc import Iterable, Mapping
from importlib.metadata import version
from pathlib import Path
from typing import Any

import oxide
from oxide.core import packing, retrieval, routing
from oxide.models import SourceRef

REPO = Path(__file__).resolve().parents[2]
PHASE6 = REPO / "docs" / "phase-6"
BASELINE = PHASE6 / "baseline-v10"
# The original annotations, as built for baseline-v1; never edited.
TASKS = PHASE6 / "baseline-v1" / "tasks.json"
AUDIT = PHASE6 / "baseline-v2" / "gold-audit.json"
SCORER = "contextbench-py-scorer-v2"
TASKS_VERSION = "contextbench-py-tasks-v1"
AUDIT_VERSION = "contextbench-py-gold-audit-v1"
BUDGETS = (256, 1024, 4096)
TOLERANCE = 0.02
GATED = (
    "entry_recall@5",
    "entry_recall@20",
    "entry_mrr",
    "routed_coverage",
    *(f"{m}@{b}" for m in ("span_recall", "line_recall") for b in BUDGETS),
)
# Where an incomplete span was lost, most upstream first (protocol § Loss stages).
STAGES = ("entry", "routing", "unsupported", "selection", "budget")
VERIFIED = ("ast", "exact", "contained", "path")
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
    """Gold content compared without indentation or blank lines (ContextBench dedents).
    Only builds the v1 ``sha256`` of tasks.json; the audit compares ``code_lines``."""
    return "\n".join(line.strip() for line in text.splitlines() if line.strip())


def code_lines(text: str) -> list[str]:
    """Stripped non-blank lines, split on ``\\n`` only, as OXIDE numbers lines
    (``str.splitlines`` also splits on form feeds and Unicode separators)."""
    return [line.strip() for line in text.split("\n") if line.strip()]


def load(name: str, directory: Path = BASELINE) -> Any:
    return json.loads((directory / name).read_text())


def load_tasks() -> dict[str, Any]:
    """The original tasks with the audit's verdict on every gold span: ``class``,
    ``verified``, and for a recorded path correction the corrected ``file`` (the
    annotated one stays as ``annotated_file``). Fixture gold is ``ast``-derived."""
    tasks: dict[str, Any] = json.loads(TASKS.read_text())
    audit = json.loads(AUDIT.read_text())["spans"]
    for task in tasks["tasks"]:
        verdicts = audit.get(task["id"]) or [{"class": "ast"} for _ in task["gold"]]
        for gold, verdict in zip(task["gold"], verdicts, strict=True):
            if verdict["class"] != "ast":
                annotated = (verdict["file"], verdict["start"], verdict["end"])
                assert annotated == (gold["file"], gold["start"], gold["end"]), task["id"]
            gold["class"] = verdict["class"]
            gold["verified"] = verdict["class"] in VERIFIED
            if "corrected_file" in verdict:
                gold["annotated_file"], gold["file"] = gold["file"], verdict["corrected_file"]
    return tasks


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


def _parquet(parquet: Path) -> list[dict[str, Any]]:
    if _sha(parquet.read_bytes()) != PARQUET_SHA256:
        raise SystemExit(f"{parquet} is not ContextBench full.parquet {PARQUET_SHA256}")
    rows: list[dict[str, Any]] = (
        importlib.import_module("pyarrow.parquet").read_table(parquet).to_pylist()
    )
    return rows


def build_tasks(parquet: Path) -> dict[str, Any]:
    rows = _parquet(parquet)
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


# --- gold audit: every annotated span checked against its own content, once ---


def _ordered(needle: list[str], haystack: list[str]) -> bool:
    """``needle`` is a subsequence of ``haystack`` (order kept, gaps allowed)."""
    rest = iter(haystack)
    return all(line in rest for line in needle)


def _found(lines: list[str], content: list[str]) -> list[tuple[int, int]]:
    """1-based line ranges where ``content`` occurs verbatim (blank lines aside)."""
    code = [(n, line.strip()) for n, line in enumerate(lines, 1) if line.strip()]
    texts = [text for _, text in code]
    size = len(content)
    return [
        (code[i][0], code[i + size - 1][0])
        for i in range(len(code) - size + 1)
        if texts[i : i + size] == content
    ]


def _file_lines(path: Path) -> list[str]:
    return path.read_bytes().decode("utf-8", "replace").split("\n")


def _patched(root: Path, path: str, patch: str) -> list[str] | None:
    """``path`` after the task's reference patch, or None when it does not apply."""
    with tempfile.TemporaryDirectory(prefix="oxide-audit-") as tmp:
        target = Path(tmp) / path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(root / path, target)
        done = subprocess.run(
            ["git", "apply", f"--include={path}", "-"],
            cwd=tmp,
            input=patch.encode(),
            capture_output=True,
        )
        return None if done.returncode else _file_lines(target)


def classify(
    root: Path, path: str, start: int, end: int, content: str, patch: str
) -> dict[str, Any]:
    """The integrity verdict of one annotated span at the pinned corpus, judged by its
    own annotated ``content`` (``...`` lines are the annotators' elision marks). First
    matching rule wins (protocol § Gold audit); a span is never moved to new lines."""
    record: dict[str, Any] = {"file": path, "start": start, "end": end}
    verbatim = code_lines(content)
    wanted = [line for line in verbatim if line != "..."]  # elisions only gap a range
    if not wanted:
        return record | {"class": "empty", "detail": "no annotated content to check"}
    if not (root / path).is_file():
        parts = path.split("/")
        hits = [
            suffix
            for suffix in ("/".join(parts[i:]) for i in range(1, len(parts)))
            if (root / suffix).is_file()
            and _ordered(wanted, code_lines("\n".join(_file_lines(root / suffix)[start - 1 : end])))
        ]
        if len(hits) == 1:
            detail = f"{path} is not in the corpus; {hits[0]} holds the content at these lines"
            return record | {"class": "path", "corrected_file": hits[0], "detail": detail}
        return record | {"class": "unresolved", "detail": f"{path} is not in the corpus"}
    lines = _file_lines(root / path)
    window = code_lines("\n".join(lines[start - 1 : end]))
    if window == verbatim:
        return record | {"class": "exact"}
    if _ordered(wanted, window):
        detail = f"{len(window) - len(wanted)} of {len(window)} range lines not in the content"
        return record | {"class": "contained", "detail": detail}
    if found := _found(lines, verbatim):
        where = ", ".join(f"{a}-{b}" for a, b in found)
        return record | {"class": "range", "detail": f"content verbatim at lines {where}"}
    after = _patched(root, path, patch)
    if after is not None and (
        _found(after, verbatim) or _ordered(wanted, code_lines("\n".join(after[start - 1 : end])))
    ):
        return record | {"class": "revision", "detail": "content is the file after the fix"}
    present = set(code_lines("\n".join(lines)))
    if absent := [line for line in wanted if line not in present]:
        detail = f"{len(absent)} of {len(wanted)} content lines are not in the file"
        return record | {"class": "drift", "detail": detail}
    return record | {"class": "unresolved", "detail": "content lines present, not in this order"}


def build_audit(parquet: Path, tasks: dict[str, Any]) -> dict[str, Any]:
    rows = {r["instance_id"]: r for r in _parquet(parquet)}
    spans: dict[str, list[dict[str, Any]]] = {}
    for task in tasks["tasks"]:
        if not task["source"].startswith("ContextBench"):
            continue
        row = rows[task["source"].split("#", 1)[1]]
        root = corpus_root(task["corpus"], tasks["corpora"][task["corpus"]])
        verdicts = []
        for gold, g in zip(task["gold"], json.loads(row["gold_context"]), strict=True):
            path, start, end = g["file"], g["start_line"], g["end_line"]
            original = (path, start, end, _sha(normalized(g["content"])))
            assert original == (gold["file"], gold["start"], gold["end"], gold["sha256"])
            verdicts.append(classify(root, path, start, end, g["content"], row["patch"]))
        spans[task["id"]] = verdicts
    return {
        "version": AUDIT_VERSION,
        "tasks": f"docs/phase-6/baseline-v1/tasks.json sha256 {_sha(TASKS.read_bytes())}",
        "source": f"EuniAI/ContextBench data/full.parquet sha256 {PARQUET_SHA256}",
        "verified": list(VERIFIED),
        "spans": spans,
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
        for attempt in range(3):  # GitHub sometimes refuses a pinned-commit fetch once
            try:
                _git("-C", bare, "fetch", "-q", "--depth", "1", corpus["url"], corpus["commit"])
                break
            except subprocess.CalledProcessError as error:
                print(error.stderr.decode(errors="replace").strip(), file=sys.stderr)
                if attempt == 2:
                    raise
                time.sleep(15)
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


def module_lines(data: bytes) -> set[int]:
    """Lines outside every outermost ``def``/``class`` (decorators included), by
    Python's own ``ast``: where module-level evidence (imports, constants, top-level
    statements) lives. A declaration nested in ``if``/``try`` is still outermost."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SyntaxWarning)
        pending: list[ast.AST] = list(ast.parse(data).body)
    inside: set[int] = set()
    while pending:
        node = pending.pop()
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            first = min([node.lineno, *(d.lineno for d in node.decorator_list)])
            inside.update(range(first, (node.end_lineno or node.lineno) + 1))
        else:
            pending.extend(ast.iter_child_nodes(node))
    return set(range(1, data.count(b"\n") + 2)) - inside


class Lines:
    """Line facts of the corpus itself, independent of OXIDE."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.data: dict[str, bytes] = {}
        self.modules: dict[str, set[int]] = {}

    def file(self, path: str) -> bytes:
        if path not in self.data:
            self.data[path] = (self.root / path).read_bytes()
        return self.data[path]

    def span(self, ref: SourceRef) -> Span:
        data = self.file(ref.path)
        first = data.count(b"\n", 0, ref.start) + 1
        return ref.path, first, data.count(b"\n", 0, max(ref.start, ref.end - 1)) + 1

    def code(self, path: str, start: int, end: int) -> frozenset[int]:
        """The non-blank lines of a span: a whitespace-only line is not evidence, and
        no source view covers the blank lines between two declarations."""
        try:
            lines = self.file(path).split(b"\n")
        except OSError:
            return frozenset(range(start, end + 1))  # a missing file: nothing covers it
        return frozenset(n for n in range(start, min(end, len(lines)) + 1) if lines[n - 1].strip())

    def module(self, path: str) -> set[int]:
        if path not in self.modules:
            try:
                self.modules[path] = module_lines(self.file(path))
            except (OSError, SyntaxError):
                self.modules[path] = set()
        return self.modules[path]


def _mean(values: Iterable[float]) -> float:
    items = list(values)
    return round(sum(items) / len(items), 6) if items else 0.0


def covers(category: str, span: Span, gold: dict[str, Any]) -> bool:
    """A symbol covers a gold span when they share a code line; a file entity covers
    it through its module-level lines (a whole file would cover everything)."""
    path, start, end = span
    if path != gold["file"]:
        return False
    if category == "file":
        return bool(gold["module"])
    return category == "symbol" and any(start <= n <= end for n in gold["lines"])


def score(repo: oxide.Repository, lines: Lines, task: dict[str, Any]) -> dict[str, Any]:
    query, symbols = task["query"], task["symbols"]
    gold = []
    for g in task["gold"]:
        code = lines.code(g["file"], g["start"], g["end"])
        gold.append({**g, "lines": code, "module": code & lines.module(g["file"])})

    found = repo.search(query, symbols=symbols)
    # Positions count every entry, modules (which have no source) included.
    entries = [
        (position, e.entity.category, lines.span(e.entity.source))
        for position, e in enumerate(found.entries, 1)
        if e.entity.source is not None
    ]
    ranks = [next((i for i, c, r in entries if covers(c, r, g)), None) for g in gold]
    entry_files = {r[0] for _, _, r in entries}

    view = repo._view()
    route = routing.route(view, [e.entity.key for e in found.entries])
    known = view.entities(c.key for c in route.candidates)
    routed = {
        k: (e.category, lines.span(e.source))
        for k, e in known.items()
        if e.category in ("symbol", "file") and e.source is not None
    }
    reached = [any(covers(c, r, g) for c, r in routed.values()) for g in gold]

    def unit(g: dict[str, Any], n: int) -> str | None:
        """The routed entity whose view would carry line ``n``: its file's module-level
        view, or the innermost routed symbol containing it."""
        if n in g["module"]:
            key = f"file:{g['file']}"
            return key if key in routed else None
        holders = [
            (r[2] - r[1], k)
            for k, (c, r) in routed.items()
            if c == "symbol" and r[0] == g["file"] and r[1] <= n <= r[2]
        ]
        return min(holders)[1] if holders else None

    def stage(
        g: dict[str, Any], n: int, omitted: Mapping[str, tuple[str, str]], header: set[str]
    ) -> tuple[int, str]:
        """Where line ``n`` was lost: (index into STAGES, detail)."""
        key = unit(g, n)
        if key is None:
            if g["file"] in entry_files:
                return 1, "not routed"
            return 0, "no entry point in its file"
        if key in omitted:
            kind, reason = omitted[key]
            if reason == "container":
                return 2, "no view of a container"
            return (3, reason) if kind == "selection" else (4, f"omitted, {reason}")
        if key in header:
            return 4, "reduced view"
        return 3, "graph node limit"

    bundles: dict[str, Any] = {}
    packed: dict[int, tuple[set[tuple[str, int]], list[tuple[Span, int]], int]] = {}
    status: list[dict[str, str]] = [{} for _ in gold]
    lost: list[dict[str, str]] = [{} for _ in gold]
    compat: dict[str, str] = {
        "entries": _sha(json.dumps([e.entity.key for e in found.entries])),
        "routed": _sha(json.dumps(sorted((c.key, c.depth) for c in route.candidates))),
    }
    versions: dict[str, str] = {}
    for budget in BUDGETS:
        bundle = repo.build_context(query, budget, symbols=symbols)
        deterministic = bundle == repo.build_context(query, budget, symbols=symbols)
        versions = {**bundle.versions, "units": bundle.units}
        items = [((i.source.path, *i.lines), i.units) for i in bundle.items]
        covered = {(f, n) for (f, s, e), _ in items for n in range(s, e + 1)}
        packed[budget] = (covered, items, bundle.used)
        omitted = {o.key: (o.stage, o.reason) for o in bundle.omissions}
        header = {e.key for i in bundle.items for e in i.entities if not e.complete}

        for g, state, why in zip(gold, status, lost, strict=True):
            missing = [n for n in sorted(g["lines"]) if (g["file"], n) not in covered]
            if not missing:
                state[str(budget)] = "complete"
                continue
            state[str(budget)] = "partial" if len(missing) < len(g["lines"]) else "missed"
            first, detail = min(stage(g, n, omitted, header) for n in missing)
            why[str(budget)] = f"{STAGES[first]}: {re.sub(r'\d+(\.\d+)?', 'N', detail)}"
        reasons = Counter(re.sub(r"\d+(\.\d+)?", "N", f"{s}: {r}") for s, r in omitted.values())
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

    def measure(chosen: list[int]) -> dict[str, Any] | None:
        """Metrics over one gold set; every recall's denominator is that set."""
        if not chosen:
            return None
        spans = [gold[i] for i in chosen]
        gold_lines = {(g["file"], n) for g in spans for n in g["lines"]}
        gold_files = {g["file"] for g in spans}
        hits = [ranks[i] for i in chosen]
        first = min((r for r in hits if r is not None), default=None)
        metrics: dict[str, float] = {
            **{
                f"entry_recall@{k}": _mean(r is not None and r <= k for r in hits)
                for k in (1, 5, 20)
            },
            "entry_mrr": round(1 / first, 6) if first else 0.0,
            "entry_file_recall": _mean(f in entry_files for f in gold_files),
            "routed_coverage": _mean(reached[i] for i in chosen),
        }
        for budget, (covered, items, used) in packed.items():
            touching = sum(
                u for (f, s, e), u in items if any((f, n) in gold_lines for n in range(s, e + 1))
            )
            complete = [status[i][str(budget)] == "complete" for i in chosen]
            metrics |= {
                f"span_recall@{budget}": _mean(complete),
                f"line_recall@{budget}": round(len(gold_lines & covered) / len(gold_lines), 6),
                f"file_recall@{budget}": _mean(f in {s[0] for s, _ in items} for f in gold_files),
                f"utilization@{budget}": round(used / budget, 6),
                f"gold_unit_share@{budget}": round(touching / used, 6) if used else 0.0,
            }
        counts = {"spans": len(spans), "lines": len(gold_lines), "files": len(gold_files)}
        return {"counts": counts, "metrics": metrics}

    return {
        "task": task["id"],
        "split": task["split"],
        "corpus": task["corpus"],
        "verified": measure([i for i, g in enumerate(gold) if g["verified"]]),
        "unverified": measure([i for i, g in enumerate(gold) if not g["verified"]]),
        "gold": [
            {
                "file": g["file"],
                "lines": [g["start"], g["end"]],
                "class": g["class"],
                "verified": g["verified"],
                "code_lines": len(g["lines"]),
                "module_lines": len(g["module"]),
                "entry_rank": rank,
                "routed": hit,
                "packed": state,
                "lost": why,
            }
            for g, rank, hit, state, why in zip(gold, ranks, reached, status, lost, strict=True)
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


def aggregate(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Per split and gold set: macro means over the tasks that have such gold, the
    summed denominators, audit classes and where every span stands at each budget."""
    out: dict[str, Any] = {}
    for split in ("dev", "heldout", "all"):
        chosen = [r for r in results if split in ("all", r["split"])]
        out[split] = {}
        for kind in ("verified", "unverified"):
            blocks = [r[kind] for r in chosen if r[kind] is not None]
            spans = [g for r in chosen for g in r["gold"] if g["verified"] == (kind == "verified")]
            if not blocks:
                continue
            stages = {
                str(b): dict(
                    sorted(
                        Counter(
                            g["lost"][str(b)].split(":")[0] if str(b) in g["lost"] else "complete"
                            for g in spans
                        ).items()
                    )
                )
                for b in BUDGETS
            }
            out[split][kind] = {
                "tasks": len(blocks),
                **{k: sum(b["counts"][k] for b in blocks) for k in ("spans", "lines", "files")},
                **{m: _mean(b["metrics"][m] for b in blocks) for m in blocks[0]["metrics"]},
                "classes": dict(sorted(Counter(g["class"] for g in spans).items())),
                "spans_by_stage": stages,
            }
    return out


def manifest(evaluated: dict[str, Any]) -> dict[str, Any]:
    def git(*args: str) -> str:
        command = ["git", "-C", str(REPO), *args]
        return subprocess.run(command, capture_output=True, text=True).stdout.strip()

    head = git("rev-parse", "HEAD") + (" +dirty" if git("status", "--porcelain", "-uno") else "")
    return {
        "baseline": f"contextbench-py-{BASELINE.name.removeprefix('baseline-')}",
        "protocol": f"docs/phase-6/{BASELINE.name}/README.md",
        "scorer": SCORER,
        "tasks": f"{TASKS_VERSION} sha256 {_sha(TASKS.read_bytes())}",
        "audit": f"{AUDIT_VERSION} sha256 {_sha(AUDIT.read_bytes())}",
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
            "packages": {
                p: version(p)
                for p in ("ladybug", "tree-sitter", "tree-sitter-python", "tree-sitter-typescript")
            },
        },
        "commands": {
            "fetch": "uv run python tests/python/contextbench.py fetch",
            "run": "uv run python tests/python/contextbench.py run --out DIR",
            "gate": "uv run pytest tests/python/test_contextbench.py",
            "tasks": "uv run --with pyarrow python tests/python/contextbench.py tasks PARQUET",
            "audit": "uv run --with pyarrow python tests/python/contextbench.py audit PARQUET",
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("tasks", help="write tasks.json").add_argument("parquet", type=Path)
    commands.add_parser("audit", help="write gold-audit.json").add_argument("parquet", type=Path)
    commands.add_parser("fetch", help="fetch the pinned corpora into the cache")
    run = commands.add_parser("run", help="evaluate every task")
    run.add_argument("--out", type=Path, help="write results/metrics/manifest.json here")
    args = parser.parse_args(argv)
    if args.command == "tasks":
        text = json.dumps(build_tasks(args.parquet), indent=1, ensure_ascii=False)
        TASKS.write_text(text + "\n")
        return 0
    if args.command == "audit":
        audit = build_audit(args.parquet, json.loads(TASKS.read_text()))
        AUDIT.write_text(json.dumps(audit, indent=1, ensure_ascii=False) + "\n")
        return 0
    tasks = load_tasks()
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
