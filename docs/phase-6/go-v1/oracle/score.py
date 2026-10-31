"""Score OXIDE's gin Go edges against the go/types uses table (gin-uses.json).

Eval-only. Run from the oxide checkout: uv run --locked python <this> <oracle dir> <gin root>.
"""

import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

from oxide.capture import capture
from oxide.core.indexing import derive

out, root = Path(sys.argv[1]), Path(sys.argv[2])
head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
uses = {
    (r["use"]["file"], r["use"]["offset"]): r["definition"]
    for r in json.loads((out / "gin-uses.json").read_text())
}
snap = capture(root)
data = {f.path: f.data for f in snap.files}
full = derive(snap, "evaluation")
entities = {e.key: e for e in full.entities}

RECEIVER = re.compile(
    rb"func\s*\(\s*(?:\w+\s+)?\*?\s*(\w+)"
)  # receiver-type reference: evidence is the whole method
SELECTED = re.compile(rb"&?\s*((?:\w+\s*\.\s*)*)(\w+)")  # X.Y / X.Y[T] / &X.Y{ -> Y; bare F -> F


def name_offset(path: str, start: int, end: int) -> int:
    text = data[path][start:end]
    m = RECEIVER.match(text) or SELECTED.match(text)
    return start + m.start(m.lastindex) if m else start


def oracle_for(ev):
    return uses.get((ev.path, name_offset(ev.path, ev.start, ev.end)))


def inside(pos, entity) -> bool:
    s = entity.source
    return bool(s and s.path == pos["file"] and s.start <= pos["offset"] < s.end)


def text_of(ev) -> str:
    return data[ev.path][ev.start : ev.end].decode("utf-8", "replace").split("\n")[0]


def line_of(path: str, offset: int) -> str:
    raw = data[path]
    lo = raw.rfind(b"\n", 0, offset) + 1
    hi = raw.find(b"\n", offset)
    return raw[lo : hi if hi >= 0 else None].decode("utf-8", "replace").strip()


def entity_at(pos):
    hits = [e for e in entities.values() if e.category == "symbol" and inside(pos, e)]
    return min(hits, key=lambda e: e.source.end - e.source.start, default=None)  # innermost symbol


go_edges = [
    e
    for e in full.edges
    if e.kind in ("calls", "references")
    and e.evidence is not None
    and e.evidence.path.endswith(".go")
    and e.evidence.path in data
]
rows, ambiguous, missed = [], [], []
for e in go_edges:
    ev, oracle = e.evidence, oracle_for(e.evidence)
    cands = [entities[k] for k in e.targets if k in entities]
    base = {
        "kind": e.kind,
        "file": ev.path,
        "offset": ev.start,
        "text": text_of(ev),
        "targets": list(e.targets),
    }
    if e.resolution == "resolved":
        if oracle is None:
            rows.append(base | {"classification": "no-oracle-use"})
        else:
            agree = any(inside(oracle, c) for c in cands)
            rows.append(
                base
                | {"classification": "agree" if agree else "disagree", "oracle_definition": oracle}
            )
    elif oracle is None:
        continue
    elif e.resolution == "ambiguous" and any(inside(oracle, c) for c in cands):
        ambiguous.append(base | {"oracle_definition": oracle})
    elif e.kind == "calls" and e.resolution == "unresolved" and oracle["file"] in data:
        target = entity_at(oracle)
        # The definition must be the symbol's own name, not a local or field inside it.
        if target is not None and data[oracle["file"]][oracle["offset"] :].startswith(
            target.name.encode()
        ):
            missed.append(base | {"oracle_target": target.key, "target_kind": target.kind})


oracle_files = {f for f, _ in uses}


def no_use_reason(r) -> str:
    if r["file"] not in oracle_files:
        return "file outside the default linux/amd64 package build (build tags, testdata)"
    if r["text"].startswith("func"):
        return "receiver type (no identifier use)"
    if r["kind"] == "calls" and any(
        entities[k].kind not in ("function", "method") for k in r["targets"] if k in entities
    ):
        return "conversion T(x)"
    return "other"


def gap_category(m) -> str:
    if m["target_kind"] == "method":
        return "method calls on anything but the receiver, promoted methods, interface dispatch"
    if m["target_kind"] == "variable":
        return "calls of function values (package-level variables)"
    return f"other ({m['target_kind']})"


counts = Counter(r["classification"] for r in rows)
agree, disagree = counts["agree"], counts["disagree"]
precision = agree / (agree + disagree) if agree + disagree else None
bad = [r for r in rows if r["classification"] == "disagree"]
for r in bad:
    r["source"] = line_of(r["file"], r["offset"])
    d = r["oracle_definition"]
    r["oracle_source"] = line_of(d["file"], d["offset"]) if d["file"] in data else d["file"]
    # Sanity: a definition at an import spec or at the qualifier's own binding means the lookup is still wrong.
    q = r["text"].rsplit(".", 1)[0].lstrip("&").strip() if "." in r["text"] else None
    r["suspect"] = bool(
        q
        and d["file"] == r["file"]
        and (
            r["oracle_source"].startswith(('"', "import"))
            or data[d["file"]][d["offset"] :].startswith(q.encode())
        )
    )
no_use = Counter(no_use_reason(r) for r in rows if r["classification"] == "no-oracle-use")
gaps = Counter(gap_category(m) for m in missed)
sample: dict[str, list] = {}
for m in missed:
    group = sample.setdefault(gap_category(m), [])
    if sum(map(len, sample.values())) < 20 and len(group) < 7:
        group.append(m)

report = {
    "head": head,
    "goos": "linux",
    "goarch": "amd64",
    "lookup": "last selector identifier; receiver type for method-receiver references",
    "counts": dict(counts),
    "precision": precision,
    "no_oracle_use_by_reason": dict(no_use),
    "ambiguous_with_oracle_candidate": ambiguous,
    "unresolved_call_gaps_by_category": dict(gaps),
    "unresolved_call_gap_sample": sample,
    "recall_gap_total": len(missed),
    "edges": rows,
}
(out / "gin-oracle.json").write_text(json.dumps(report, indent=2) + "\n")

lines = [
    f"# gin go/types oracle ({head[:12]}, GOOS=linux GOARCH=amd64), rescored",
    "Lookup: last selector identifier of qualified evidence; receiver type identifier for receiver references.",
    f"Resolved Go edges: agree={agree}, disagree={disagree}, no-oracle-use={counts['no-oracle-use']}. Precision {precision:.4f}.",
    "No-oracle-use: " + "; ".join(f"{k}={v}" for k, v in no_use.most_common()),
    f"Ambiguous edges whose oracle target is a candidate: {len(ambiguous)}.",
    f"Unresolved calls whose oracle target is a gin symbol: {len(missed)} — "
    + "; ".join(f"{k}={v}" for k, v in gaps.most_common()),
    "",
    f"## Disagreements ({len(bad)}; suspect lookups: {sum(r['suspect'] for r in bad)})",
]
lines += [
    f"- {r['file']}:{r['offset']} `{r['source']}` → OXIDE {','.join(r['targets'])}; oracle `{r['oracle_source']}`"
    for r in bad
]
lines += ["", "## Unresolved call gap sample"]
lines += [
    f"- {k}: " + ", ".join(f"`{m['text']}` ({m['file']}) → {m['oracle_target']}" for m in v)
    for k, v in sample.items()
]
(out / "report.md").write_text("\n".join(lines) + "\n")
print("\n".join(lines[:8]))
print(f"report lines: {len(lines)}")
