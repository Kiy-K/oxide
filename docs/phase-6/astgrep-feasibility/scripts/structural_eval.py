"""Study B/C: run the preregistered queries over real corpora in the chosen arms.

The file set is exactly what OXIDE captures (`oxide.capture.capture`): Python files for
Python queries, `.ts`/`.mts`/`.cts`/`.tsx` for TypeScript (tsx with the tsx grammar).

    structural_eval.py CORPUS_NAME OUT_DIR [--arms astgrep,treesitter,regex]

Writes OUT_DIR/CORPUS_NAME[.arms].json: per query the match counts per arm, exact-span
agreement between ast-grep and tree-sitter, regex precision/recall against the ast-grep
spans (a regex hit is correct when it lies on a line inside an ast-grep match), and
deterministic samples of every disagreement for the manual audit; per corpus the
timings per arm, peak RSS, files, bytes, invalid-UTF-8 and error-tree counts.
"""

from __future__ import annotations

import hashlib
import json
import resource
import sys
import time
from collections import defaultdict
from pathlib import Path

import arms
from oxide.capture import capture
from queries import QUERIES

CACHE = Path.home() / ".cache/oxide"
# Preregistered corpora: the lexicographically first cached commit of each repository.
CORPORA = {
    "requests": ("contextbench/corpora/psf__requests@0192aac24123", "python"),
    "flask": ("contextbench/corpora/pallets__flask@7ee9ceb71e86", "python"),
    "pytest": ("contextbench/corpora/pytest-dev__pytest@19ad5889353c", "python"),
    "xarray": ("contextbench/corpora/pydata__xarray@1757dffac2fa", "python"),
    "seaborn": ("contextbench/corpora/mwaskom__seaborn@54cab15bdacf", "python"),
    "code-server": ("contextbench-ts/corpora-v2/coder__code-server@5d3c9edce436", "typescript"),
    "darkreader": ("contextbench-ts/corpora-v2/darkreader__darkreader@991883df4d59", "typescript"),
    "vue-core": ("contextbench-ts/corpora-v2/vuejs__core@0275dd329d24", "typescript"),
    "mui": ("contextbench-ts/corpora-v2/mui__material-ui@03b616a1e015", "typescript"),
    "vscode": ("contextbench-ts/corpora-v2/microsoft__vscode@1a599e2d1792", "typescript"),
}
ALL_ARMS = {"astgrep", "treesitter", "regex"}
SAMPLE = 10


def _order(item: tuple[str, int]) -> str:
    return hashlib.sha256(f"{item[0]}:{item[1]}".encode()).hexdigest()


def _sample(items: list[tuple[str, int]], lines: dict[str, list[str]]) -> list[dict[str, object]]:
    picked = sorted(items, key=_order)[:SAMPLE]
    return [{"path": p, "line": n + 1, "text": lines[p][n][:200]} for p, n in picked]


def main() -> None:
    name, out_dir = sys.argv[1], Path(sys.argv[2])
    chosen = (
        set(sys.argv[4].split(",")) if len(sys.argv) > 4 and sys.argv[3] == "--arms" else ALL_ARMS
    )
    rel, lang = CORPORA[name]
    queries = [q for q in QUERIES if q.lang == lang]
    root = CACHE / rel

    started = time.perf_counter()
    snap = capture(root)
    files = [(f.path, f.data, g) for f in snap.files if (g := arms.grammar(f.path))]
    files = [(p, d, g) for p, d, g in files if (g == "python") == (lang == "python")]
    seconds: dict[str, float] = defaultdict(float)
    seconds["capture"] = time.perf_counter() - started

    found: dict[str, dict[str, list[tuple[str, arms.Match]]]] = {
        q.id: defaultdict(list) for q in queries
    }
    lines: dict[str, list[str]] = {}
    stats = {"files": len(files), "bytes": sum(len(d) for _, d, _ in files)}
    stats.update(invalid_utf8=0, error_trees=0, astgrep_errors=0)

    for path, data, grammar in files:
        t = time.perf_counter()
        text, valid = arms.decode(data)
        seconds["decode"] += time.perf_counter() - t
        stats["invalid_utf8"] += not valid
        lines[path] = text.split("\n")
        if "treesitter" in chosen:
            t = time.perf_counter()
            tree = arms.parse(data, grammar)
            seconds["treesitter_parse"] += time.perf_counter() - t
            stats["error_trees"] += tree.root_node.has_error
            for q in queries:
                t = time.perf_counter()
                matches = arms.treesitter(q, data, grammar, tree)
                seconds["treesitter_query"] += time.perf_counter() - t
                found[q.id]["treesitter"] += [(path, m) for m in matches]
        if "astgrep" in chosen:
            t = time.perf_counter()
            try:
                sg = arms.astgrep_root(text, grammar)
            except Exception:  # recorded, never silently dropped
                stats["astgrep_errors"] += 1
                continue
            table = arms.byte_offsets(text)
            seconds["astgrep_parse"] += time.perf_counter() - t
            for q in queries:
                t = time.perf_counter()
                matches = arms.astgrep(q, data, grammar, sg, table)
                seconds["astgrep_query"] += time.perf_counter() - t
                found[q.id]["astgrep"] += [(path, m) for m in matches]
        if "regex" in chosen:
            for q in queries:
                if q.regex is None:
                    continue
                t = time.perf_counter()
                matches = arms.regex(q, data, text)
                seconds["regex"] += time.perf_counter() - t
                found[q.id]["regex"] += [(path, m) for m in matches]

    per_query = {}
    for q in queries:
        f = found[q.id]
        row: dict[str, object] = {k: len(v) for k, v in f.items()}
        if {"astgrep", "treesitter"} <= chosen:
            a = {(p, m.start, m.end) for p, m in f["astgrep"]}
            t2 = {(p, m.start, m.end) for p, m in f["treesitter"]}
            only_a = sorted({(p, m.line) for p, m in f["astgrep"] if (p, m.start, m.end) not in t2})
            only_t = sorted(
                {(p, m.line) for p, m in f["treesitter"] if (p, m.start, m.end) not in a}
            )
            row.update(
                agree=len(a & t2),
                jaccard=len(a & t2) / len(a | t2) if a | t2 else 1.0,
                astgrep_only_count=len(only_a),
                treesitter_only_count=len(only_t),
                astgrep_only=_sample(only_a, lines),
                treesitter_only=_sample(only_t, lines),
            )
        if {"astgrep", "regex"} <= chosen and q.regex is not None:
            spans: dict[str, list[tuple[int, int]]] = defaultdict(list)
            for p, m in f["astgrep"]:
                spans[p].append((m.line, m.end_line))
            hits = sorted({(p, m.line) for p, m in f["regex"]})
            good = {h for h in hits if any(s <= h[1] <= e for s, e in spans[h[0]])}
            hit_lines: dict[str, set[int]] = defaultdict(set)
            for p, n in hits:
                hit_lines[p].add(n)
            covered = sum(
                any(m.line <= n <= m.end_line for n in hit_lines[p]) for p, m in f["astgrep"]
            )
            missed = sorted(
                {
                    (p, m.line)
                    for p, m in f["astgrep"]
                    if not any(m.line <= n <= m.end_line for n in hit_lines[p])
                }
            )
            false = sorted(set(hits) - good)
            row.update(
                regex_hits=len(hits),
                regex_precision=len(good) / len(hits) if hits else None,
                regex_recall=covered / len(f["astgrep"]) if f["astgrep"] else None,
                regex_false_count=len(false),
                regex_missed_count=len(missed),
                regex_false=_sample(false, lines),
                regex_missed=_sample(missed, lines),
            )
        per_query[q.id] = row

    report = {
        "corpus": name,
        "root": str(root),
        "lang": lang,
        "arms": sorted(chosen),
        "queries": per_query,
        "stats": stats,
        "seconds": {k: round(v, 3) for k, v in seconds.items()},
        "peak_rss_mib": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1),
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    suffix = "" if chosen == ALL_ARMS else "." + "+".join(sorted(chosen))
    (out_dir / f"{name}{suffix}.json").write_text(json.dumps(report, indent=1) + "\n")
    print(name, report["seconds"], report["peak_rss_mib"], file=sys.stderr)


if __name__ == "__main__":
    main()
