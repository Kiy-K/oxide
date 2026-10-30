"""Score OXIDE's Java calls/references/imports edges on gson against javac attribution.

Usage: uv run --locked python score.py GSON_ROOT GSON_USES_JSON OUT_JSON
GSON_USES_JSON is Oracle.java's output: per file, [utf16_offset, name, element_kind, status, decls].
"""

from __future__ import annotations

import bisect
import collections
import hashlib
import json
import re
import sys
from pathlib import Path

from oxide.capture import capture
from oxide.core.indexing import derive

IDENT = re.compile(r"[A-Za-z_$][\w$]*")
ANGLES = re.compile(r"<[^<>]*>")
PACKAGE = re.compile(rb"^\s*package\s+([\w.]+)\s*;", re.M)
TYPE_KINDS = {"CLASS", "INTERFACE", "ENUM", "RECORD", "ANNOTATION_TYPE"}
KINDS = ("calls", "references", "imports")


class Utf16:
    """Byte offsets of one UTF-8 file to javac's UTF-16 char offsets."""

    def __init__(self, data: bytes) -> None:
        self.bytes, self.units = [0], [0]
        for ch in data.decode("utf-8", "replace"):
            self.bytes.append(self.bytes[-1] + len(ch.encode("utf-8")))
            self.units.append(self.units[-1] + (2 if ord(ch) > 0xFFFF else 1))

    def unit(self, byte: int) -> int:
        return self.units[bisect.bisect_right(self.bytes, byte) - 1]


def query(text: str) -> tuple[int, str] | None:
    """Byte index (within the evidence text) and spelling of the last identifier, ignoring type
    arguments and the call's argument list; ``None`` for a wildcard import."""
    if text.startswith("import"):
        text = text.split(";")[0].rstrip()
        if text.endswith("*"):
            return None
    # Blank out (nested) type arguments in place, so `a.<T>m` and `new a.Foo<Bar>` keep offsets.
    masked = text
    while (blanked := ANGLES.sub(lambda m: " " * len(m.group()), masked)) != masked:
        masked = blanked
    cut = len(masked)
    for stop in "([":
        at = masked.find(stop)
        if at != -1:
            cut = min(cut, at)
    found = list(IDENT.finditer(masked[:cut]))
    if not found:
        return None
    m = found[-1]
    return len(text[: m.start()].encode()), m.group()


def main(root: Path, uses_path: Path, out: Path) -> None:
    oracle = json.loads(uses_path.read_text())
    uses = {f: {row[0]: row for row in rows} for f, rows in oracle["uses"].items() if f}
    snap = capture(root)
    data = {f.path: f.data for f in snap.files}
    full = derive(snap, "evaluation")
    entities = {e.key: e for e in full.entities}
    conv = {p: Utf16(b) for p, b in data.items() if p.endswith(".java")}
    package = {}
    for p, b in data.items():
        if p.endswith(".java"):
            m = PACKAGE.search(b)
            package[p] = m.group(1).decode() if m else None
    # Symbol spans in UTF-16 units, per file, for the innermost-entity (strict) check.
    spans: dict[str, list[tuple[int, int, str]]] = collections.defaultdict(list)
    for e in full.entities:
        if e.category == "symbol" and e.source is not None and e.source.path in conv:
            c = conv[e.source.path]
            spans[e.source.path].append((c.unit(e.source.start), c.unit(e.source.end), e.key))

    def inside(decl: list, key: str) -> bool:
        path, at = decl
        if key.startswith("module:java:"):
            return package.get(path) == key.removeprefix("module:java:")
        e = entities.get(key)
        if e is None or e.source is None or e.source.path != path:
            return False
        c = conv[path]
        return c.unit(e.source.start) <= at < c.unit(e.source.end)

    def innermost(decl: list) -> str | None:
        path, at = decl
        best = None
        for start, end, key in spans.get(path, ()):
            if start <= at < end and (best is None or end - start < best[1] - best[0]):
                best = (start, end, key)
        return best[2] if best else None

    def strict(decl: list, key: str, kind: str) -> bool:
        """The javac declaration's innermost OXIDE symbol is the target, and its kind fits."""
        if key.startswith("module:java:"):
            return inside(decl, key)
        owner = innermost(decl)
        if kind in TYPE_KINDS or kind in ("METHOD", "CONSTRUCTOR"):
            return owner == key
        # A static field/enum-constant import targets the owner type.
        return kind in ("FIELD", "ENUM_CONSTANT") and owner == key

    counts: dict[str, collections.Counter] = {k: collections.Counter() for k in KINDS}
    disagree, loose_only, unresolved = [], [], []
    no_oracle: collections.Counter = collections.Counter()
    for edge in full.edges:
        at = edge.evidence
        if edge.kind not in KINDS or at is None or not at.path.endswith(".java"):
            continue
        text = data[at.path][at.start : at.end].decode("utf-8", "replace")
        q = query(text)
        c = counts[edge.kind]
        if q is None:
            c["skipped:wildcard-or-no-identifier"] += 1
            continue
        offset = conv[at.path].unit(at.start + q[0])
        use = uses.get(at.path, {}).get(offset)
        line = data[at.path][: at.start].count(b"\n") + 1
        row = {"kind": edge.kind, "source": edge.source, "text": text, "at": f"{at.path}:{line}",
               "targets": list(edge.targets), "resolution": edge.resolution}  # fmt: skip
        if use is None:
            status, decls, name, ekind = "no-use", [], None, None
        else:
            _, name, ekind, status, decls = use
        row["javac"] = {"name": name, "kind": ekind, "status": status, "decls": decls}
        if name is not None and name != q[1]:
            c["scorer:name-mismatch"] += 1
        in_repo = status in ("ok", "import") and bool(decls)
        if edge.resolution == "resolved":
            if not in_repo:
                c["resolved:no-oracle"] += 1
                no_oracle[f"{edge.kind}:{status}"] += 1
            elif any(inside(d, t) for d in decls for t in edge.targets):
                c["resolved:agree"] += 1
                if any(strict(d, t, ekind or "") for d in decls for t in edge.targets):
                    c["resolved:agree-strict"] += 1
                else:
                    loose_only.append(row)
            else:
                c["resolved:disagree"] += 1
                disagree.append(row)
        elif edge.resolution == "ambiguous":
            if not in_repo:
                c["ambiguous:no-oracle"] += 1
            elif any(inside(d, t) for d in decls for t in edge.targets):
                c["ambiguous:in-candidates"] += 1
            else:
                c["ambiguous:in-none"] += 1
        else:
            c[f"unresolved:{'in-repo' if in_repo else 'no-oracle'}"] += 1
            if in_repo and edge.kind in ("calls", "references"):
                unresolved.append((hashlib.sha256(repr(edge).encode()).hexdigest(), row))

    def precision(c: collections.Counter, key: str) -> float | None:
        n = c["resolved:agree"] + c["resolved:disagree"]
        return round(c[key] / n, 4) if n else None

    total = sum(counts.values(), collections.Counter())
    table = [*counts.items(), ("overall", total)]
    result = {
        "javac": oracle["javac"],
        "modules": oracle["modules"],
        "counts": {k: dict(sorted(v.items())) for k, v in table},
        "precision": {k: precision(v, "resolved:agree") for k, v in table},
        "precision_strict": {k: precision(v, "resolved:agree-strict") for k, v in table},
        "no_oracle_reasons": dict(sorted(no_oracle.items())),
        "disagreements": disagree,
        "agree_but_not_strict": loose_only,
        "unresolved_sample": [row for _, row in sorted(unresolved, key=lambda t: t[0])[:40]],
    }
    out.write_text(json.dumps(result, indent=1) + "\n")
    summary = ("counts", "precision", "precision_strict", "no_oracle_reasons")
    print(json.dumps({k: result[k] for k in summary}, indent=1))
    print("disagreements", len(disagree), "agree-not-strict", len(loose_only))


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]))
