"""Extraction stage (preregistration.md § Stage 1): B and C outlines as structural facts.

    extract.py conformance OUT.json          both arms against conformance/expected.json
    extract.py corpus NAME ROOT OUT.json     both arms over every parsed source of ROOT

Indexes into a private data directory under outline.DATA. B reads the captured bytes
through ``View.source``, never the worktree. Per corpus: B-vs-C entry matching with a
cause class for every disagreement, import line agreement, provenance (range inside
the file, name inside the range), determinism (two runs, byte-identical), wall time
per file and rendering size against the whole file and arm A's file view.
"""

from __future__ import annotations

import json
import resource
import shutil
import statistics
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import outline  # noqa: E402

import oxide  # noqa: E402
from oxide.core.packing import units  # noqa: E402
from oxide.models import SourceRef  # noqa: E402

HERE = Path(__file__).resolve().parents[1]
SAMPLES = 8  # disagreement examples kept per cause class


def flat(entries, parent=None):
    for e in entries or ():
        yield e, parent
        yield from ((m, e.name) for m in e.members)


def indexed(root: Path):
    data = Path(tempfile.mkdtemp(prefix="extract-", dir=outline.DATA))
    repo = oxide.open_repository(root, data_dir=data)
    report = repo.index()
    return repo, data, report.seconds


def files_of(view):
    for path, info in sorted(view.files.items()):
        if info.coverage != "unsupported" and Path(path).suffix.lower() in outline.LANGS:
            yield path, info


def conformance(out: Path) -> None:
    expected = json.loads((HERE / "conformance/expected.json").read_text())["files"]
    repo, data, _ = indexed(HERE / "conformance/src")
    report: dict = {}
    try:
        view = repo._view()
        for path, want in expected.items():
            info = view.files[path]
            raw = view.source(SourceRef(path, 0, info.size, info.digest))
            arms = {"B": outline.astgrep(raw, path), "C": outline.native(view, path)}
            report[path] = {}
            for arm, (entries, failure) in arms.items():
                got = list(flat(entries))
                used: set[int] = set()

                def find(role, names, line, parent):
                    for i, (e, p) in enumerate(got):
                        ok_role = e.role == role
                        ok_name = role == "import" or e.name in names
                        ok_parent = role != "member" or p in parent
                        if ok_role and ok_name and ok_parent and e.first <= line <= e.last:
                            if i not in used:
                                used.add(i)
                                return True
                    return False

                found, missing = 0, []
                required = 0
                for role, names, line, need, *members in want:
                    hit = find(role, names, line, None)
                    if need:
                        required += 1
                        found += hit
                        if not hit:
                            missing.append(f"{role} {names[0]} L{line}")
                    for mrole, mnames, mline, mneed in members[0] if members else []:
                        mhit = find(mrole, mnames, mline, names)
                        if mneed:
                            required += 1
                            found += mhit
                            if not mhit:
                                missing.append(f"{mrole} {names[0]}.{mnames[0]} L{mline}")
                extra = [
                    f"{e.role} {e.kind} {e.name} L{e.first}-{e.last}"
                    for i, (e, _) in enumerate(got)
                    if i not in used
                ]
                report[path][arm] = {
                    "failure": failure,
                    "required": required,
                    "found": found,
                    "missing": missing,
                    "unexpected": extra,
                    "rendering": outline.render(path, outline.Lines(raw).count(), entries),
                }
    finally:
        repo.close()
        shutil.rmtree(data, ignore_errors=True)
    totals = {
        arm: {
            "required": sum(f[arm]["required"] for f in report.values()),
            "found": sum(f[arm]["found"] for f in report.values()),
            "unexpected": sum(len(f[arm]["unexpected"]) for f in report.values()),
        }
        for arm in ("B", "C")
    }
    out.write_text(
        json.dumps({"totals": totals, "files": report}, indent=1, ensure_ascii=False) + "\n"
    )
    print(json.dumps(totals))


def compare(b, c) -> tuple[Counter, dict]:
    """Match B's and C's declarations (imports apart) by name and overlapping range."""
    classes: Counter = Counter()
    examples: dict = {}
    bs = [(e, p) for e, p in flat(b) if e.role != "import"]
    cs = [(e, p) for e, p in flat(c) if e.role != "import"]
    taken: set[int] = set()

    def note(cls: str, text: str) -> None:
        classes[cls] += 1
        examples.setdefault(cls, [])
        if len(examples[cls]) < SAMPLES:
            examples[cls].append(text)

    for e, p in bs:
        options = [
            (abs(x.start - e.start), i)
            for i, (x, _) in enumerate(cs)
            if i not in taken and x.name == e.name and x.start < e.end and e.start < x.end
        ]
        if not options:
            note(f"B only: {e.role} {e.kind}", f"{e.name} L{e.first}-{e.last} in {p}")
            continue
        _, i = min(options)
        taken.add(i)
        x, q = cs[i]
        if x.role != e.role:
            note(f"hierarchy: B {e.role}, C {x.role}", f"{e.name} L{e.first}")
        if (x.start, x.end) == (e.start, e.end):
            classes["matched, same bytes"] += 1
        elif x.end == e.end or (x.first, x.last) == (e.first, e.last):
            note("matched, start differs", f"{e.name} B L{e.first} C L{x.first}")
        else:
            note("matched, range differs", f"{e.name} B L{e.first}-{e.last} C L{x.first}-{x.last}")
    for i, (x, q) in enumerate(cs):
        if i not in taken:
            note(f"C only: {x.role} {x.kind}", f"{x.name} L{x.first}-{x.last} in {q}")
    return classes, examples


def lines_of(entries) -> set[int]:
    return {n for e in entries or () if e.role == "import" for n in range(e.first, e.last + 1)}


def provenance(entries, raw: bytes) -> Counter:
    c: Counter = Counter()
    for e, _ in flat(entries):
        c["entries"] += 1
        c["inside"] += 0 <= e.start <= e.end <= len(raw)
        if e.role != "import" and e.name and not e.name.startswith("<") and "." not in e.name:
            c["named"] += 1
            c["name in range"] += e.name.encode() in raw[e.start : e.end]
    return c


def corpus(name: str, root: Path, out: Path) -> None:
    outline.verify_tool()
    repo, data, index_s = indexed(root)
    totals: Counter = Counter()
    classes: Counter = Counter()
    examples: dict = {}
    prov = {"B": Counter(), "C": Counter()}
    failures: Counter = Counter()
    seconds = {"B": [], "C": []}
    size = {"B": [], "C": [], "A": [], "file": []}
    nondeterministic = {"B": [], "C": []}
    imports = Counter()
    try:
        view = repo._view()
        for path, info in files_of(view):
            raw = view.source(SourceRef(path, 0, info.size, info.digest))
            totals["files"] += 1
            began = time.perf_counter()
            b, bfail = outline.astgrep(raw, path)
            seconds["B"].append(time.perf_counter() - began)
            began = time.perf_counter()
            c, cfail = outline.native(view, path)
            seconds["C"].append(time.perf_counter() - began)
            n = outline.Lines(raw).count()
            if outline.render(path, n, b) != outline.render(path, n, outline.astgrep(raw, path)[0]):
                nondeterministic["B"].append(path)
            if outline.render(path, n, c) != outline.render(path, n, outline.native(view, path)[0]):
                nondeterministic["C"].append(path)
            if bfail or cfail:
                failures[f"B: {bfail}" if bfail else f"C: {cfail}"] += 1
                continue
            totals["compared"] += 1
            totals["partial"] += info.coverage == "partial"
            got, ex = compare(b, c)
            classes.update(got)
            for k, v in ex.items():
                room = SAMPLES - len(examples.setdefault(k, []))
                examples[k] += [f"{path}: {t}" for t in v[:room]]
            bl, cl = lines_of(b), lines_of(c)
            imports["B lines"] += len(bl)
            imports["C lines"] += len(cl)
            imports["both"] += len(bl & cl)
            prov["B"].update(provenance(b, raw))
            prov["C"].update(provenance(c, raw))
            size["file"].append(units(raw.decode("utf-8", "replace")))
            size["A"].append(units(outline.header_view(view, path)))
            size["B"].append(units(outline.render(path, n, b)))
            size["C"].append(units(outline.render(path, n, c)))
    finally:
        repo.close()
        shutil.rmtree(data, ignore_errors=True)

    def q(values, p):
        return round(statistics.quantiles(values, n=100)[p - 1], 6) if len(values) > 1 else None

    report = {
        "corpus": name,
        "tool": outline.AST_GREP_VERSION,
        "totals": dict(totals),
        "failures": dict(failures),
        "classes": dict(classes.most_common()),
        "examples": examples,
        "imports": dict(imports),
        "provenance": {k: dict(v) for k, v in prov.items()},
        "nondeterministic": nondeterministic,
        "seconds": {
            k: {"sum": round(sum(v), 3), "p50": q(v, 50), "p95": q(v, 95)}
            for k, v in seconds.items()
        },
        "units": {k: {"sum": sum(v), "p50": q(v, 50)} for k, v in size.items()},
        "index_seconds": round(index_s, 3),
        # every child is an ast-grep run: the largest resident set any of them reached
        "astgrep_max_rss_mb": resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss // 1024,
    }
    out.write_text(json.dumps(report, indent=1, ensure_ascii=False) + "\n")
    print(json.dumps({k: report[k] for k in ("corpus", "totals", "failures")}))


if __name__ == "__main__":
    if sys.argv[1] == "conformance":
        conformance(Path(sys.argv[2]))
    elif sys.argv[1] == "corpus":
        corpus(sys.argv[2], Path(sys.argv[3]), Path(sys.argv[4]))
    else:
        raise SystemExit(__doc__)
