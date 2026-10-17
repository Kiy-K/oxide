"""ContextBench-ts: TypeScript context quality on the ContextBench-py ruler.

``docs/phase-6/typescript-v1/README.md`` is the protocol, with v2's amendment (each
corpus also holds the commit's ``tsconfig*.json``). Scoring is ``contextbench.py``'s
(entry, routing and packing metrics, loss stages, gold audit rules) with three
TypeScript parts: the corpus is the tracked TypeScript sources, module-level lines come
from the grammar instead of ``ast``, and every span lost before packing gets a
structural cause (router bounds, unresolved imports an oracle can follow, partial
parses, no path). Gold comes from the fixture benchmark files and ContextBench's human
annotations, never from OXIDE's output. Every run indexes into a fresh private data
location::

    uv run python tests/python/contextbench_ts.py fetch            # pinned corpora -> cache
    uv run python tests/python/contextbench_ts.py run [--out DIR]  # evaluate every task
    uv run --with pyarrow python tests/python/contextbench_ts.py tasks PARQUET  # once
    uv run --with pyarrow python tests/python/contextbench_ts.py audit PARQUET  # once
"""

from __future__ import annotations

import argparse
import io
import json
import os
import posixpath
import re
import resource
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from collections import Counter
from pathlib import Path
from typing import Any

import contextbench as cb
import tree_sitter_typescript
from tree_sitter import Language, Node, Parser

import oxide
from oxide.core import routing
from oxide.models import IndexReport, SourceRef
from oxide.storage import ladybug
from oxide.storage.ladybug import View

BASELINE = cb.PHASE6 / "typescript-v3"
PROTOCOL = cb.PHASE6 / "typescript-v1"  # tasks, gold audit and scorer are v1's
TASKS = PROTOCOL / "tasks.json"
AUDIT = PROTOCOL / "gold-audit.json"
SCORER = "contextbench-ts-scorer-v1"
TASKS_VERSION = "contextbench-ts-tasks-v1"
AUDIT_VERSION = "contextbench-ts-gold-audit-v1"
SOURCES = (".ts", ".tsx", ".mts", ".cts")
CONFIGS = re.compile(r"tsconfig[^/]*\.json")  # the project files TypeScript reads (v2)
# The cost rule of the protocol (TypeScript files at every base commit, from git trees).
MAX_TS_FILES = 5000
# Repositories (by URL: ContextBench names mui both ``mui/material`` and
# ``mui/material-ui``) with TypeScript gold within the cost rule (protocol § Tasks).
ELIGIBLE = (
    "mui/material-ui",
    "vuejs/core",
    "microsoft/vscode",
    "coder/code-server",
    "darkreader/darkreader",
)
HELDOUT = ("mui/material-ui", "vuejs/core")  # the two with the most tasks
CACHE = cb.CACHE.parent / "contextbench-ts"
# v1's corpora (sources only, ``corpora/``) reproduce v1 at its commit.
CORPORA = CACHE / "corpora-v2"
DEFAULT_BUFFER_POOL = ladybug.BUFFER_POOL
# The smallest power of two that indexed the largest corpus (vscode, 554k relations).
EVAL_BUFFER_POOL = 512 << 20
FIXTURE = "fixture:ts_repo"
# The documented symbol rules (docs/typescript.md § Identity and kinds), written
# against the grammar independently of oxide.core.typescript.
DECLARATIONS = {
    "function_declaration",
    "generator_function_declaration",
    "function_signature",
    "class_declaration",
    "abstract_class_declaration",
    "interface_declaration",
    "enum_declaration",
    "type_alias_declaration",
    "internal_module",
    "module",
}
VARIABLES = {"lexical_declaration", "variable_declaration"}
FUNCTIONS = {"arrow_function", "function_expression", "generator_function"}
WRAPPERS = {"export_statement", "ambient_declaration"}
MEMBERS = {
    "method_definition",
    "method_signature",
    "abstract_method_signature",
    "public_field_definition",
}
CAUSES = ("partial-file", "router-bound", "unresolved-import", "no-path")
_GRAMMARS = {
    "typescript": Language(tree_sitter_typescript.language_typescript()),
    "tsx": Language(tree_sitter_typescript.language_tsx()),
}


def is_source(path: str) -> bool:
    return path.endswith(SOURCES)


def is_config(path: str) -> bool:
    return CONFIGS.fullmatch(posixpath.basename(path)) is not None


def repository(url: str) -> str:
    return url.removeprefix("https://github.com/").removesuffix(".git")


def corpus_root(name: str, corpus: dict[str, str]) -> Path:
    return cb.REPO / corpus["path"] if "path" in corpus else CORPORA / name


# --- the grammar's view of declarations (independent of OXIDE's derivation) ---


def tree(data: bytes, path: str) -> Node:
    parser = Parser(_GRAMMARS["tsx" if path.endswith(".tsx") else "typescript"])
    return parser.parse(data).root_node


def _text(node: Node | None) -> str:
    return node.text.decode() if node is not None and node.text is not None else ""


def _outer(node: Node) -> Node:
    """The statement a declaration's range starts at: its export/declare wrappers."""
    while node.parent is not None and node.parent.type in WRAPPERS:
        node = node.parent
    return node


def _exported(node: Node) -> bool:
    outer = _outer(node)
    parent = outer.parent
    return outer.type == "export_statement" and parent is not None and parent.type == "program"


def _function(node: Node) -> bool:
    return getattr(node.child_by_field_name("value"), "type", "") in FUNCTIONS


def _member(node: Node) -> bool:
    """A member OXIDE can key: a plain name (not computed, string or ``#private``), and
    a field only with a function value."""
    named = getattr(node.child_by_field_name("name"), "type", "") == "property_identifier"
    return named and (node.type != "public_field_definition" or _function(node))


def declarations(root: Node) -> list[tuple[str, Node]]:
    """(name, range node) of every outermost declaration: what OXIDE makes a symbol
    whose parent is the file. A declaration inside an expression (a test callback)
    is still outermost; one inside another declaration is not. Methods outside a
    class (object literals, type literals) count: OXIDE makes them symbols, although
    docs/typescript.md says it does not (measured, protocol § Scorer validation)."""
    found: list[tuple[str, Node]] = []
    pending = list(reversed(root.children))
    while pending:
        node = pending.pop()
        if node.type in DECLARATIONS:
            found.append((_text(node.child_by_field_name("name")), _outer(node)))
            continue
        if node.type in MEMBERS and _member(node):
            found.append((_text(node.child_by_field_name("name")), node))
            continue
        if node.type in VARIABLES:
            declarators = [c for c in node.named_children if c.type == "variable_declarator"]
            chosen = [
                d
                for d in declarators
                if getattr(d.child_by_field_name("name"), "type", "") == "identifier"
                and (_exported(node) or _function(d))
            ]
            for d in chosen:
                whole = _outer(node) if len(declarators) == 1 else d
                found.append((_text(d.child_by_field_name("name")), whole))
            if chosen:
                pending.extend(reversed([d for d in declarators if d not in chosen]))
                continue
        value = node.child_by_field_name("value") if node.type == "export_statement" else None
        if value is not None and value.type in FUNCTIONS | {"class"}:
            found.append(("<default>", node))
            continue
        pending.extend(reversed(node.children))
    return found


def module_lines(data: bytes, path: str) -> set[int]:
    """Lines outside every outermost declaration: the module-level evidence."""
    inside: set[int] = set()
    for _, node in declarations(tree(data, path)):
        inside.update(range(node.start_point[0] + 1, node.end_point[0] + 2))
    return set(range(1, data.count(b"\n") + 2)) - inside


def gold_span(root: Path, gold: str) -> cb.Span:
    """``path#A.b`` as lines, by the grammar; ``path#path:__module__`` is the file."""
    path, dotted = gold.split("#")
    data = (root / path).read_bytes()
    if dotted.endswith(":__module__"):
        return path, 1, data.rstrip(b"\n").count(b"\n") + 1
    head, *rest = dotted.split(".")
    found = [n for name, n in declarations(tree(data, path)) if name == head]
    for name in rest:
        if len(found) != 1:
            break
        pending, members = [found[0]], []
        while pending:  # the class body under its export wrapper
            node = pending.pop()
            if node.type == "class_body":
                members = node.named_children
                break
            pending.extend(node.named_children)
        found = [
            m
            for m in members
            if m.type in MEMBERS and _member(m) and _text(m.child_by_field_name("name")) == name
        ]
    if len(found) != 1:
        raise ValueError(f"{gold}: {len(found)} declarations")
    return path, found[0].start_point[0] + 1, found[0].end_point[0] + 1


class Lines(cb.Lines):
    """ContextBench-py's line facts, with TypeScript module-level lines."""

    def module(self, path: str) -> set[int]:
        if path not in self.modules:
            try:
                self.modules[path] = module_lines(self.file(path), path)
            except OSError:
                self.modules[path] = set()
        return self.modules[path]


# --- tasks and gold audit: built once ---


def build_tasks(parquet: Path) -> dict[str, Any]:
    rows = cb._parquet(parquet)
    fixture = cb.REPO / "fixtures" / "ts_repo"
    tasks: list[dict[str, Any]] = []
    corpora: dict[str, dict[str, str]] = {FIXTURE: {"path": "fixtures/ts_repo"}}
    for source in ("benchmark.json", "structural_benchmark.json"):
        for query in json.loads((cb.REPO / "fixtures" / source).read_text())["queries"]:
            if query["repo"] != "ts":
                continue
            spans = [gold_span(fixture, gold) for gold in query["relevant"]]
            tasks.append(
                {
                    "id": query["id"],
                    "split": "dev",
                    "corpus": FIXTURE,
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
        repo = repository(row["repo_url"])
        gold = json.loads(row["gold_context"])
        if row["language"] != "typescript" or repo not in ELIGIBLE:
            continue
        if not any(is_source(g["file"]) for g in gold):
            continue
        name = f"{repo.replace('/', '__')}@{row['base_commit'][:12]}"
        corpora[name] = {"url": f"https://github.com/{repo}", "commit": row["base_commit"]}
        tasks.append(
            {
                "id": row["original_inst_id"],
                "split": "heldout" if repo in HELDOUT else "dev",
                "corpus": name,
                "query": row["problem_statement"],
                "symbols": [],
                "source": f"ContextBench full.parquet#{row['instance_id']}",
                "gold": [
                    {
                        "file": g["file"],
                        "start": g["start_line"],
                        "end": g["end_line"],
                        "sha256": cb._sha(cb.normalized(g["content"])),
                    }
                    for g in gold
                ],
            }
        )
    return {
        "version": TASKS_VERSION,
        "sources": {
            "fixture": "fixtures/benchmark.json, fixtures/structural_benchmark.json (repo ts)",
            "contextbench": f"EuniAI/ContextBench data/full.parquet sha256 {cb.PARQUET_SHA256}",
        },
        "corpora": dict(sorted(corpora.items())),
        "tasks": tasks,
    }


def unnumbered(content: str, start: int) -> tuple[str, bool]:
    """Annotated content in ``cat -n`` form (``12\tcode``, some Multi-SWE-bench tasks),
    without its numbers, when every line carries one and they count up from the span's
    first line; otherwise the content unchanged. A numbered span is flagged."""
    lines = content.rstrip("\n").split("\n")
    numbers, texts = [], []
    for line in lines:
        head, tab, text = line.partition("\t")
        # An empty last line can lose its tab to trailing-whitespace trimming.
        if not ((tab or line == lines[-1]) and head.strip().isdigit()):
            return content, False
        numbers.append(int(head))
        texts.append(text)
    if numbers != list(range(start, start + len(numbers))):
        return content, False
    return "\n".join(texts), True


def build_audit(parquet: Path, tasks: dict[str, Any]) -> dict[str, Any]:
    """ContextBench-py's rules on every span; one outside the TypeScript corpus is
    ``outside`` (JSON, JavaScript, Markdown: OXIDE cannot see it here)."""
    rows = {r["instance_id"]: r for r in cb._parquet(parquet)}
    spans: dict[str, list[dict[str, Any]]] = {}
    for task in tasks["tasks"]:
        if not task["source"].startswith("ContextBench"):
            continue
        row = rows[task["source"].split("#", 1)[1]]
        root = corpus_root(task["corpus"], tasks["corpora"][task["corpus"]])
        verdicts = []
        for gold, g in zip(task["gold"], json.loads(row["gold_context"]), strict=True):
            path, start, end = g["file"], g["start_line"], g["end_line"]
            original = (path, start, end, cb._sha(cb.normalized(g["content"])))
            assert original == (gold["file"], gold["start"], gold["end"], gold["sha256"])
            if is_source(path):
                content, numbered = unnumbered(g["content"], start)
                verdict = cb.classify(root, path, start, end, content, row["patch"])
                verdicts.append(verdict | ({"numbered": True} if numbered else {}))
            else:
                record = {"file": path, "start": start, "end": end, "class": "outside"}
                verdicts.append(record | {"detail": "not a TypeScript source"})
        spans[task["id"]] = verdicts
    return {
        "version": AUDIT_VERSION,
        "tasks": f"docs/phase-6/typescript-v1/tasks.json sha256 {cb._sha(TASKS.read_bytes())}",
        "source": f"EuniAI/ContextBench data/full.parquet sha256 {cb.PARQUET_SHA256}",
        "verified": list(cb.VERIFIED),
        "spans": spans,
    }


def load_tasks() -> dict[str, Any]:
    """The tasks with the audit applied as ContextBench-py does. ``outside`` spans are
    moved to the task's ``outside`` list and never scored."""
    tasks: dict[str, Any] = json.loads(TASKS.read_text())
    audit = json.loads(AUDIT.read_text())["spans"]
    for task in tasks["tasks"]:
        verdicts = audit.get(task["id"]) or [{"class": "ast"} for _ in task["gold"]]
        gold, outside = [], []
        for g, verdict in zip(task["gold"], verdicts, strict=True):
            if verdict["class"] != "ast":
                annotated = (verdict["file"], verdict["start"], verdict["end"])
                assert annotated == (g["file"], g["start"], g["end"]), task["id"]
            if verdict["class"] == "outside":
                outside.append({"file": g["file"], "lines": [g["start"], g["end"]]})
                continue
            g["class"] = verdict["class"]
            g["verified"] = verdict["class"] in cb.VERIFIED
            if "corrected_file" in verdict:
                g["annotated_file"], g["file"] = g["file"], verdict["corrected_file"]
            gold.append(g)
        task["gold"], task["outside"] = gold, outside
    return tasks


# --- corpora: the TypeScript sources of pinned commits, fetched explicitly ---


def _git(*args: str | Path, data: bytes | None = None) -> bytes:
    command = ["git", *map(str, args)]
    return subprocess.run(command, input=data, capture_output=True, check=True).stdout


def fetch(corpora: dict[str, dict[str, str]]) -> None:
    """Trees without blobs, then only the TypeScript, ``tsconfig*.json`` and
    ``package.json`` blobs (a partial clone: a full mui checkout is ~40,000 files)."""
    for name, corpus in corpora.items():
        target = corpus_root(name, corpus)
        if "url" not in corpus or target.is_dir():
            continue
        bare = CACHE / "git" / (name.split("@")[0] + ".git")
        if not bare.is_dir():
            _git("init", "-q", "--bare", bare)
        print(f"fetching {name}", file=sys.stderr)
        url, commit = corpus["url"], corpus["commit"]
        _git("-C", bare, "fetch", "-q", "--depth", "1", "--filter=blob:none", url, commit)
        wanted: list[tuple[str, str]] = []
        for entry in _git("-C", bare, "ls-tree", "-r", "-z", commit).decode().split("\0"):
            if not entry:
                continue
            meta, path = entry.split("\t", 1)
            _, kind, oid = meta.split()
            if kind == "blob" and (
                is_source(path) or is_config(path) or posixpath.basename(path) == "package.json"
            ):
                wanted.append((oid, path))
        if (count := sum(is_source(p) for _, p in wanted)) > MAX_TS_FILES:
            raise SystemExit(
                f"{name}: {count} TypeScript files, over the protocol's {MAX_TS_FILES}"
            )
        oids = "".join(f"{oid}\n" for oid, _ in wanted).encode()
        _git(
            "-C", bare, "fetch", "-q", "--no-tags", "--filter=blob:none", "--stdin", url, data=oids
        )
        # git archive fails on a pathspec that matches nothing.
        patterns = [f"*{s}" for s in SOURCES if any(p.endswith(s) for _, p in wanted)]
        patterns += [f":(literal){p}" for _, p in wanted if is_config(p)]  # only fetched blobs
        archive = _git("-C", bare, "archive", "--format=tar", commit, "--", *patterns)
        partial = target.with_name(target.name + ".partial")
        shutil.rmtree(partial, ignore_errors=True)
        with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
            kept = [m for m in tar if m.isfile() and (is_source(m.name) or is_config(m.name))]
            tar.extractall(partial, members=kept, filter="data")
        # Workspace packages (name -> directory), kept beside the corpus, not in it.
        found: dict[str, str] = {}
        for oid, path in sorted(wanted, key=lambda w: w[1]):
            if posixpath.basename(path) != "package.json":
                continue
            try:
                manifest = json.loads(_git("-C", bare, "cat-file", "blob", oid))
            except ValueError:
                continue
            if isinstance(manifest, dict) and isinstance(manifest.get("name"), str):
                found.setdefault(manifest["name"], posixpath.dirname(path))
        text = json.dumps(dict(sorted(found.items())), indent=1) + "\n"
        target.with_name(target.name + ".packages.json").write_text(text)
        os.replace(partial, target)


def identity(root: Path) -> dict[str, Any]:
    """The corpus independent of OXIDE: its TypeScript and ``tsconfig*.json`` files
    and their digests (a v1 corpus has no config, so its identity is v1's)."""
    files = sorted(
        p for p in root.rglob("*") if p.is_file() and (is_source(p.name) or is_config(p.name))
    )
    lines = [f"{p.relative_to(root).as_posix()}\0{cb._sha(p.read_bytes())}" for p in files]
    configs = sum(is_config(p.name) for p in files)
    return {
        "ts_files": len(files) - configs,
        "configs": configs,
        "tree_sha256": cb._sha("\n".join(lines)),
    }


def workspace(name: str, corpus: dict[str, str]) -> dict[str, str]:
    if "path" in corpus:
        return {}
    root = corpus_root(name, corpus)
    found: dict[str, str] = json.loads(root.with_name(root.name + ".packages.json").read_text())
    return found


# --- attribution: why a span OXIDE never reached was not reached ---


class Oracle:
    """Where an unresolved import specifier probably points, by path rules OXIDE does
    not apply: a relative module whose name could not be followed (through packages,
    an unexported name), a workspace package (``package.json`` names) or a path alias
    (the one module whose path ends in the specifier, as ``baseUrl`` resolves it). A
    diagnostic counterfactual, inferred, never gold."""

    def __init__(self, files: list[str], packages: dict[str, str]) -> None:
        self.files = set(files)
        self.packages = packages
        self.suffixes: dict[str, set[str]] = {}
        for path in files:
            for base in self._stems(path):
                parts = base.split("/")
                for i in range(len(parts)):
                    self.suffixes.setdefault("/".join(parts[i:]), set()).add(path)

    @staticmethod
    def _stems(path: str) -> list[str]:
        stem = path
        for suffix in (".d.ts", ".d.mts", ".d.cts", *SOURCES):
            if stem.endswith(suffix):
                stem = stem.removesuffix(suffix)
                break
        return [stem, stem.removesuffix("/index")] if stem.endswith("/index") else [stem]

    def _module(self, base: str) -> set[str]:
        for extension in (".js", ".jsx", ".mjs", ".cjs", *SOURCES):
            if base.endswith(extension):
                base = base.removesuffix(extension)
                break
        candidates = {f"{b}{s}" for b in (base, f"{base}/index") for s in (*SOURCES, ".d.ts")}
        return candidates & self.files

    def resolve(self, importer: str, specifier: str) -> tuple[str, set[str]]:
        """(class, files): ``relative``, ``workspace``, ``alias`` or ``external``."""
        if specifier in (".", "..") or specifier.startswith(("./", "../")):
            joined = posixpath.normpath(posixpath.join(posixpath.dirname(importer), specifier))
            return "relative", self._module(joined)
        parts = specifier.split("/")
        size = 2 if specifier.startswith("@") else 1
        package, sub = "/".join(parts[:size]), "/".join(parts[size:])
        if package in self.packages:
            home = self.packages[package]
            bases = [f"{home}/{sub}", f"{home}/src/{sub}"] if sub else [f"{home}/src", home]
            found: set[str] = set()
            for base in bases:
                found |= self._module(posixpath.normpath(base))
            return "workspace", found
        aliased = self.suffixes.get(specifier, set()) if "/" in specifier else set()
        if len(aliased) == 1:
            return "alias", aliased
        return "external", set()


def specifier_of(shown: str) -> str:
    """An unresolved import is shown as ``<specifier>`` or ``<specifier>:<name>``."""
    head, _, name = shown.rpartition(":")
    return head if head and name.isidentifier() and not head.endswith(":") else shown


def import_facts(view: View, oracle: Oracle) -> tuple[dict[str, Any], dict[str, list[str]]]:
    """Corpus counts of name relations by kind and resolution (an ambiguous relation
    counted once), unresolved imports by oracle class, and the oracle's file <-> file
    edges."""
    kinds = "['imports', 'calls', 'references']"
    rows = view._query(
        f"MATCH (a:Entity)-[r:Rel]->(:Entity) WHERE r.kind IN {kinds} "
        "RETURN DISTINCT r.kind, a.key, r.site, r.resolution"
    )
    counts: dict[str, Counter[str]] = {k: Counter() for k in ("imports", "calls", "references")}
    for kind, _, _, resolution in rows:
        counts[kind][resolution] += 1
    for kind, n in view._query(
        f"MATCH ()-[r:Opaque]->(:Unresolved) WHERE r.kind IN {kinds} RETURN r.kind, count(r)"
    ):
        counts[kind]["unresolved"] = n
    opaque = view._query(
        "MATCH (a:Entity)-[r:Opaque]->(:Unresolved) WHERE r.kind = 'imports' "
        "RETURN a.key, r.name ORDER BY a.key, r.site"
    )
    edges: dict[str, set[str]] = {}
    classes: Counter[str] = Counter()
    for source, shown in opaque:
        importer = source.removeprefix("file:")
        kind, targets = oracle.resolve(importer, specifier_of(shown))
        classes[kind if kind == "external" else f"{kind}:{'found' if targets else 'none'}"] += 1
        for target in targets - {importer}:
            edges.setdefault(source, set()).add(f"file:{target}")
            edges.setdefault(f"file:{target}", set()).add(source)
    stats = {
        "resolution": {k: dict(sorted(c.items())) for k, c in counts.items()},
        "unresolved_imports": dict(sorted(classes.items())),
    }
    return stats, {k: sorted(v) for k, v in edges.items()}


def distances(
    view: View, starts: list[str], extra: dict[str, list[str]], depth: int
) -> dict[str, int]:
    """Hops from ``starts`` over the router's edges (containment both ways but never
    the repository root, every followed kind both ways) plus ``extra``, unbounded."""
    seen = dict.fromkeys(starts, 0)
    layer = list(seen)
    for level in range(1, depth + 1):
        adjacency = view.adjacency(layer)
        following: list[str] = []
        for key in layer:
            out, into = adjacency[key]
            near = [o for kind, o in [*out, *into] if kind == "contains" or kind in routing.FOLLOW]
            for other in [*near, *extra.get(key, [])]:
                if other != "repo" and other not in seen:
                    seen[other] = level
                    following.append(other)
        layer = following
    return seen


def byte_range(view: View, path: str, start: int, end: int) -> tuple[int, int]:
    """Byte offsets of lines ``start..end`` of the captured ``path``."""
    info = view.files[path]
    data = view.source(SourceRef(path, 0, info.size, info.digest))
    offsets = [0, *(i + 1 for i, b in enumerate(data) if b == 10), len(data) + 1]
    first = offsets[min(start, len(offsets) - 1) - 1]
    return first, max(first, offsets[min(end, len(offsets) - 1)] - 1)


def attribute(
    view: View, result: dict[str, Any], entries: list[str], extra: dict[str, list[str]]
) -> None:
    """Adds ``cause`` to every span lost at ``entry`` or ``routing`` at the largest
    budget, first matching rule: ``partial-file`` (its file parsed partially),
    ``router-bound`` (within the router's depth of an entry point when no bound
    applies), ``unresolved-import`` (within it only with the oracle's edges),
    ``no-path`` (nothing structural near any entry point)."""
    top = str(cb.BUDGETS[-1])
    lost = [
        g for g in result["gold"] if g["lost"].get(top, "").split(":")[0] in ("entry", "routing")
    ]
    if not lost:
        return
    plain = distances(view, entries, {}, routing.MAX_DEPTH)
    helped = distances(view, entries, extra, routing.MAX_DEPTH)
    for g in lost:
        path, (start, end) = g["file"], g["lines"]
        info = view.files.get(path)
        units = {f"file:{path}"}
        if info is not None and (
            inner := view.innermost(path, *byte_range(view, path, start, end))
        ):
            units.add(inner)
        if info is not None and info.coverage == "partial":
            g["cause"] = "partial-file"
        elif units & plain.keys():
            g["cause"] = "router-bound"
        elif units & helped.keys():
            g["cause"] = "unresolved-import"
        else:
            g["cause"] = "no-path"


# --- evaluation ---


def _indexed(root: Path, store: Path) -> tuple[oxide.Repository, IndexReport, str | None]:
    """The corpus indexed with the product's buffer pool. When LadybugDB's pool
    overflows (a finding, recorded as ``default_index_failure``), indexed again with
    ``EVAL_BUFFER_POOL``, which changes no query result, so quality stays measurable."""
    repo = oxide.open_repository(root, data_dir=store / "default")
    try:
        return repo, repo.index(), None
    except RuntimeError as error:
        repo.close()
        if "buffer pool is full" not in str(error):
            raise
        ladybug.BUFFER_POOL = EVAL_BUFFER_POOL
        repo = oxide.open_repository(root, data_dir=store / "override")
        return repo, repo.index(), str(error).splitlines()[0]


def evaluate(tasks: dict[str, Any], data: Path) -> dict[str, Any]:
    """Every task, grouped by corpus. Each corpus indexes into a fresh directory under
    ``data``, removed after it (a vscode store is hundreds of MB). ``performance``
    holds wall times and memory, which are not reproducible and never compared."""
    results: list[dict[str, Any]] = []
    corpora: dict[str, Any] = {}
    performance: dict[str, Any] = {}
    for name, corpus in tasks["corpora"].items():
        root = corpus_root(name, corpus)
        if not root.is_dir():
            raise FileNotFoundError(f"corpus {name} missing at {root}: run `fetch`")
        print(f"evaluating {name}", file=sys.stderr)
        store = Path(tempfile.mkdtemp(prefix="corpus-", dir=data))
        try:
            repo, report, failure = _indexed(root, store)
            with repo:
                view = repo._view()
                oracle = Oracle(list(view.files), workspace(name, corpus))
                stats, extra = import_facts(view, oracle)
                corpora[name] = {
                    **{k: v for k, v in corpus.items() if k != "path"},
                    **identity(root),
                    "snapshot": report.snapshot.snapshot_id,
                    "derivation": report.snapshot.derivation_id,
                    "entities": report.entities,
                    "relations": report.relations,
                    "partial_files": len(report.partial),
                    "default_index_failure": failure,
                    **stats,
                }
                seconds: dict[str, float] = {}
                lines = Lines(root)
                for task in (t for t in tasks["tasks"] if t["corpus"] == name):
                    began = time.perf_counter()
                    result = cb.score(repo, lines, task)
                    seconds[task["id"]] = round(time.perf_counter() - began, 3)
                    found = repo.search(task["query"], symbols=task["symbols"])
                    attribute(view, result, [e.entity.key for e in found.entries], extra)
                    result["outside"] = task["outside"]
                    results.append(result)
            performance[name] = {
                "index_s": round(report.seconds, 3),
                "score_s": seconds,  # search, route, 3 budgets x 2 builds
                "max_rss_mb": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // 1024,
            }
        finally:
            ladybug.BUFFER_POOL = DEFAULT_BUFFER_POOL
            shutil.rmtree(store, ignore_errors=True)
    order = {t["id"]: n for n, t in enumerate(tasks["tasks"])}
    results.sort(key=lambda r: order[r["task"]])
    return {"corpora": corpora, "results": results, "performance": performance}


def aggregate(results: list[dict[str, Any]]) -> dict[str, Any]:
    """ContextBench-py's aggregates, plus per split the cause of every verified span lost
    before packing at the largest budget, the gold left outside the corpus, and the
    gated metrics per repository."""
    out = cb.aggregate(results)
    for split in ("dev", "heldout", "all"):
        chosen = [r for r in results if split in ("all", r["split"])]
        causes = Counter(
            g["cause"] for r in chosen for g in r["gold"] if "cause" in g and g["verified"]
        )
        outside = [o for r in chosen for o in r["outside"]]
        out[split]["lost_before_packing"] = {c: causes.get(c, 0) for c in CAUSES}
        out[split]["outside"] = {
            "spans": len(outside),
            "tasks": sum(bool(r["outside"]) for r in chosen),
            "by_extension": dict(
                sorted(Counter(posixpath.splitext(o["file"])[1] for o in outside).items())
            ),
        }
        out[split]["by_repository"] = {
            repo: {
                "tasks": len(rs),
                **{
                    m: cb._mean(r["verified"]["metrics"][m] for r in rs if r["verified"])
                    for m in cb.GATED
                },
            }
            for repo in sorted({r["corpus"].split("@")[0] for r in chosen})
            if (rs := [r for r in chosen if r["corpus"].split("@")[0] == repo])
        }
    return out


def manifest(evaluated: dict[str, Any]) -> dict[str, Any]:
    base = cb.manifest(evaluated)
    return {
        **base,
        "baseline": "contextbench-ts-v3",
        "protocol": "docs/phase-6/typescript-v1/README.md, amended by typescript-v2 and v3",
        "scorer": SCORER,
        "tasks": f"{TASKS_VERSION} sha256 {cb._sha(TASKS.read_bytes())}",
        "audit": f"{AUDIT_VERSION} sha256 {cb._sha(AUDIT.read_bytes())}",
        "max_ts_files": MAX_TS_FILES,
        "commands": {
            "fetch": "uv run python tests/python/contextbench_ts.py fetch",
            "run": "uv run python tests/python/contextbench_ts.py run --out DIR",
            "gate": "mise run contextbench-ts",
            "tasks": "uv run --with pyarrow python tests/python/contextbench_ts.py tasks PARQUET",
            "audit": "uv run --with pyarrow python tests/python/contextbench_ts.py audit PARQUET",
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
    run.add_argument("--only", help="evaluate only the corpora whose name starts with this")
    args = parser.parse_args(argv)
    if args.command == "tasks":
        PROTOCOL.mkdir(parents=True, exist_ok=True)
        TASKS.write_text(json.dumps(build_tasks(args.parquet), indent=1, ensure_ascii=False) + "\n")
        return 0
    if args.command == "audit":
        audit = build_audit(args.parquet, json.loads(TASKS.read_text()))
        AUDIT.write_text(json.dumps(audit, indent=1, ensure_ascii=False) + "\n")
        return 0
    if args.command == "fetch":
        fetch(json.loads(TASKS.read_text())["corpora"])
        return 0
    tasks = load_tasks()
    if args.only:
        tasks["corpora"] = {k: v for k, v in tasks["corpora"].items() if k.startswith(args.only)}
        tasks["tasks"] = [t for t in tasks["tasks"] if t["corpus"] in tasks["corpora"]]
    with tempfile.TemporaryDirectory(prefix="oxide-contextbench-ts-") as data:
        evaluated = evaluate(tasks, Path(data))
    metrics = aggregate(evaluated["results"])
    print(json.dumps(metrics, indent=1))
    if args.out:
        args.out.mkdir(parents=True, exist_ok=True)
        for name, value in (
            ("results.json", evaluated["results"]),
            ("metrics.json", metrics),
            ("manifest.json", manifest(evaluated)),
            ("performance.json", evaluated["performance"]),
        ):
            (args.out / name).write_text(json.dumps(value, indent=1, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
