"""Study C: malformed-source behaviour of the two AST arms on real files.

Every file of a corpus gets three deterministic damaged variants:

- truncate: cut at half its bytes (on a character boundary)
- unclosed: the first `)` after the midpoint removed
- garbage:  a line `def (:` (Python) / `function (:` (TypeScript) inserted at the middle

and every query of the corpus language runs over each variant in both arms. Reported per
arm: exceptions, and matches lost *before* the damage point (a match of the intact file
that ends before the damage and is missing from the damaged one), the silent loss an
agent can't see. Run it in a subprocess: a native crash shows as its exit code.

    malformed.py CORPUS_NAME OUT_DIR
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

import arms
from oxide.capture import capture
from queries import QUERIES, Q
from structural_eval import CACHE, CORPORA


def variants(data: bytes, lang: str) -> dict[str, tuple[bytes, int]]:
    """name -> (damaged bytes, damage offset)."""
    half = len(data) // 2
    while half > 0 and (data[half] & 0xC0) == 0x80:
        half -= 1
    out = {"truncate": (data[:half], half)}
    close = data.find(b")", half)
    if close >= 0:
        out["unclosed"] = (data[:close] + data[close + 1 :], close)
    line = data.rfind(b"\n", 0, half) + 1
    junk = b"def (:\n" if lang == "python" else b"function (:\n"
    out["garbage"] = (data[:line] + junk + data[line:], line)
    return out


def run(arm: str, q: Q, data: bytes, grammar: str) -> set[tuple[int, int]]:
    if arm == "astgrep":
        found = arms.astgrep(q, data, grammar)
    else:
        found = arms.treesitter(q, data, grammar)
    return {(m.start, m.end) for m in found}


def main() -> None:
    name, out_dir = sys.argv[1], Path(sys.argv[2])
    rel, lang = CORPORA[name]
    queries = [q for q in QUERIES if q.lang == lang]
    captured = capture(CACHE / rel).files
    files = [(f.path, f.data, g) for f in captured if (g := arms.grammar(f.path))]
    files = [(p, d, g) for p, d, g in files if (g == "python") == (lang == "python")]
    counts: Counter[str] = Counter()
    examples: list[dict[str, object]] = []
    for path, data, grammar in files:
        for arm in ("astgrep", "treesitter"):
            intact = {q.id: run(arm, q, data, grammar) for q in queries}
            for kind, (damaged, at) in variants(data, lang).items():
                counts[f"{arm}:variants"] += 1
                if arms.parse(damaged, grammar).root_node.has_error:
                    counts[f"{arm}:{kind}:error_trees"] += 1
                for q in queries:
                    try:
                        after = run(arm, q, damaged, grammar)
                    except Exception as error:  # counted, never fatal
                        counts[f"{arm}:exceptions"] += 1
                        examples.append({"arm": arm, "path": path, "error": repr(error)})
                        continue
                    before = {s for s in intact[q.id] if s[1] <= at}
                    lost = before - after
                    counts[f"{arm}:{kind}:prefix_matches"] += len(before)
                    counts[f"{arm}:{kind}:prefix_lost"] += len(lost)
                    if lost and len(examples) < 40:
                        s = min(lost)
                        text = data[s[0] : s[1]][:120].decode("utf-8", "replace")
                        examples.append(
                            {"arm": arm, "variant": kind, "query": q.id, "path": path, "text": text}
                        )
    report = {
        "corpus": name,
        "files": len(files),
        "counts": dict(sorted(counts.items())),
        "examples": examples,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"malformed-{name}.json").write_text(json.dumps(report, indent=1) + "\n")


if __name__ == "__main__":
    main()
