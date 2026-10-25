"""Part 1: re-read the graph-navigation study (5ed725d) from its frozen results.

    python3 reassess.py      # stdlib + rg; writes results/reassess.json

Nothing of the original study is re-scored or changed. This script decomposes its
completeness signal, classifies every Q1 miss by the syntax of the jedi site that
impact did not reach, and classifies every grep "false" claim by the syntax of the
grep hit, to bound how many of them are oracle misses.
"""

import io
import json
import math
import re
import subprocess
import sys
import tokenize
from collections import Counter
from pathlib import Path

NAV = Path(__file__).resolve().parents[2] / "graph-navigation"
sys.path.insert(0, str(NAV / "scripts"))
from common import DATA, Enclosing, checkout  # noqa: E402

RESULTS = Path(__file__).resolve().parents[1] / "results"
rows = json.loads((NAV / "results/rows.json").read_text())
truth = {t["key"]: t for t in json.loads((NAV / "results/truth.json").read_text())}
audit = {(a["key"], a["q"]): a for a in json.loads((NAV / "results/audit.json").read_text())}
# README § Audit: the 3 redirect claims are confirmed jedi misses, so impact is right there.
REDIRECT = "symbol:src/flask/helpers.py#redirect"
CONFIRMED = set(audit[(REDIRECT, "q1")]["I_false"])


def wilson(k: int, n: int) -> list[float]:
    p, z = k / n, 1.96
    centre, half = p + z * z / (2 * n), z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return [
        round((centre - half) / (1 + z * z / n), 3),
        round((centre + half) / (1 + z * z / n), 3),
    ]


def rate(hits: int, n: int) -> dict:
    return {"k": hits, "n": n, "rate": round(hits / n, 3), "ci95": wilson(hits, n)}


# --- 1. the completeness signal, decomposed ---------------------------------------------
def claims(r: dict) -> tuple[set[str], set[str]]:
    """Impact's Q1 claims and grep's Q1 owners, rebuilt from the audit."""
    a, q1 = audit[(r["key"], "q1")], set(truth[r["key"]]["q1"])
    assert r["G"]["q1"]["r"] == 1.0  # grep owners hold all of truth, so = truth | G_false
    return (q1 - set(a["I_missed"])) | set(a["I_false"]), q1 | set(a["G_false"])


signals = {
    "preregistered: unresolved > 0 or stopped_by": lambda r: (
        r["I"]["unresolved"] > 0 or r["I"]["stopped_by"] is not None
    ),
    "unresolved > 0": lambda r: r["I"]["unresolved"] > 0,
    "stopped_by set": lambda r: r["I"]["stopped_by"] is not None,
    "unresolved > 0 or method root": lambda r: r["I"]["unresolved"] > 0 or r["kind"] == "method",
    "residual: a grep owner no hop explains": lambda r: bool(claims(r)[1] - claims(r)[0]),
}
incomplete = [r for r in rows if r["I"]["q1"]["r"] < 1]
complete = [r for r in rows if r["I"]["q1"]["r"] == 1]
assert len(incomplete) == 11 and REDIRECT in {r["key"] for r in complete}
report: dict = {"signal": {}}
for label, fires in signals.items():
    report["signal"][label] = {
        "sensitivity": rate(sum(bool(fires(r)) for r in incomplete), len(incomplete)),
        "specificity": rate(sum(not fires(r) for r in complete), len(complete)),
    }
residual = {r["key"]: len(claims(r)[1] - claims(r)[0]) for r in rows}
report["residual_entities"] = {
    "incomplete": sorted(residual[r["key"]] for r in incomplete),
    "complete": sorted(residual[r["key"]] for r in complete),
}
report["stopped_by_depth_on_complete"] = [r["key"] for r in complete if r["I"]["stopped_by"]]


# --- 2. what impact misses: the syntax of each unreached jedi site ----------------------
def line_at(root: Path, path: str, offset: int) -> tuple[str, int, int]:
    data = (root / path).read_bytes()
    start = data.rfind(b"\n", 0, offset) + 1
    end = data.find(b"\n", offset)
    text = data[start : end if end >= 0 else None].decode("utf-8", "replace")
    return text, len(data[start:offset].decode("utf-8", "replace")), data.count(b"\n", 0, offset)


def site_kind(text: str, col: int, name: str) -> str:
    before, after = text[:col], text[col + len(name) :]
    stripped = before.lstrip()
    if stripped.startswith(("import ", "from ")):
        return "import"
    if re.fullmatch(r"@[\w.]*", stripped):
        return "decorator"
    if re.match(r"\s*(?:async\s+)?def\s", text) and "(" in before:
        return "parameter (fixture or default)"
    if before.endswith("."):
        return "attribute call" if after.lstrip().startswith("(") else "attribute value"
    if after.lstrip().startswith("("):
        return "bare-name call"
    return "bare-name value"


misses: Counter = Counter()
miss_by_kind: dict[str, Counter] = {"function": Counter(), "method": Counter()}
examples: dict[str, list[str]] = {}
for r in rows:
    t, a = truth[r["key"]], audit[(r["key"], "q1")]
    root, name, missed = checkout(r["corpus"]), t["name"], set(a["I_missed"])
    for path, offset, owner in t["sites"]:
        if owner not in missed:
            continue
        text, col, line = line_at(root, path, offset)
        kind = site_kind(text, col, name)
        misses[kind] += 1
        miss_by_kind[r["kind"]][kind] += 1
        examples.setdefault(kind, []).append(f"{path}:{line + 1}: {text.strip()[:100]}")
report["missed_sites"] = {
    "total": sum(misses.values()),
    "by_syntax": dict(misses.most_common()),
    "by_root_kind": {k: dict(v) for k, v in miss_by_kind.items()},
    "examples": {k: v[:4] for k, v in examples.items()},
}


# --- 3. grep's "false" claims: oracle misses or true false positives? --------------------
def code_columns(source: str) -> dict[int, set[int]]:
    """line → start columns of NAME tokens; comments and strings hold none."""
    out: dict[int, set[int]] = {}
    try:
        for tok in tokenize.generate_tokens(io.StringIO(source).readline):
            if tok.type == tokenize.NAME:
                out.setdefault(tok.start[0], set()).add(tok.start[1])
    except (tokenize.TokenError, IndentationError, SyntaxError):
        pass
    return out


grep_false: Counter = Counter()
grep_examples: dict[str, list[str]] = {}
for r in rows:
    t, a = truth[r["key"]], audit[(r["key"], "q1")]
    false_owners = set(a["G_false"]) - (CONFIRMED if r["key"] == REDIRECT else set())
    if not false_owners:
        continue
    root, name = checkout(r["corpus"]), t["name"]
    enclosing = Enclosing(json.loads((DATA / f"entities-{r['corpus']}.json").read_text()))
    hits = subprocess.run(
        ["rg", "-nw", "--column", "--no-heading", "-t", "py", "-F", name, "."],
        cwd=root, capture_output=True, text=True, check=False,
    ).stdout.splitlines()  # fmt: skip
    cache: dict[str, tuple[list[int], dict[int, set[int]]]] = {}
    for hit in hits:
        path, line_s, col_s, text = hit.split(":", 3)
        path, line, col = path.removeprefix("./"), int(line_s), int(col_s) - 1  # col: bytes
        if path not in cache:
            data = (root / path).read_bytes()
            starts = [0] + [i + 1 for i, b in enumerate(data) if b == 10]
            cache[path] = (starts, code_columns(data.decode("utf-8", "replace")))
        starts, names = cache[path]
        owner = enclosing.at(path, starts[line - 1] + col)
        if owner not in false_owners:
            continue
        char_col = len(text.encode()[:col].decode("utf-8", "replace"))
        if char_col not in names.get(line, set()):
            kind = "comment or string"
        elif re.match(rf"\s*(?:async\s+)?(?:def|class)\s+{re.escape(name)}\b", text):
            kind = "another definition of the name"
        else:
            kind = site_kind(text, char_col, name)
        grep_false[kind] += 1
        grep_examples.setdefault(kind, []).append(
            f"{r['corpus']} {path}:{line}: {text.strip()[:90]}"
        )
report["grep_false_sites"] = {
    "total": sum(grep_false.values()),
    "by_syntax": dict(grep_false.most_common()),
    "examples": {k: v[:6] for k, v in grep_examples.items()},
}
RESULTS.mkdir(exist_ok=True)
(RESULTS / "reassess.json").write_text(json.dumps(report, indent=1) + "\n")
print(json.dumps(report, indent=1))
