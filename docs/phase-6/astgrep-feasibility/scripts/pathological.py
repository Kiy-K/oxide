"""Study C: bounded execution on adversarial inputs, one fresh subprocess per case.

Each case runs every query of its language in one arm over one generated file, under a
60 s timeout; reported are seconds, exit code (a native crash or stack overflow is a
non-zero code) and peak RSS. ast-grep has no timeout or cancellation of its own and
holds the GIL, so a caller can only bound it from outside (a process) or beforehand
(input size).

    pathological.py OUT_DIR                     # driver
    pathological.py --case LANG KIND N ARM      # one case (internal)
"""

from __future__ import annotations

import json
import resource
import subprocess
import sys
import time
from pathlib import Path

SIZES = (1_000, 10_000, 100_000)
CASES = [
    (lang, kind, n)
    for lang in ("python", "typescript")
    for kind, ns in (("nest", SIZES), ("chain", SIZES), ("long", (100_000,)))
    for n in ns
]


def source(lang: str, kind: str, n: int) -> bytes:
    py = lang == "python"
    if kind == "nest":  # n nested brackets
        text = "x = " + "(" * n + "1" + ")" * n if py else "const x = " + "[" * n + "1" + "]" * n
    elif kind == "chain":  # an n-link method chain
        text = "p" + ".then(f)" * n
    else:  # n lines
        text = ("x = f(a, b) == None\n" if py else "const x = f(a, b) as any;\n") * n
    return (text + ("\n" if py else ";\n")).encode()


def one(lang: str, kind: str, n: int, arm: str) -> None:
    import arms
    from queries import QUERIES

    data = source(lang, kind, n)
    started = time.perf_counter()
    total = 0
    for q in (q for q in QUERIES if q.lang == lang):
        found = arms.astgrep(q, data, lang) if arm == "astgrep" else arms.treesitter(q, data, lang)
        total += len(found)
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    print(
        json.dumps(
            {
                "seconds": round(time.perf_counter() - started, 3),
                "matches": total,
                "rss_mib": round(rss, 1),
            }
        )
    )


def main() -> None:
    if sys.argv[1] == "--case":
        one(sys.argv[2], sys.argv[3], int(sys.argv[4]), sys.argv[5])
        return
    out_dir = Path(sys.argv[1])
    rows = []
    for lang, kind, n in CASES:
        for arm in ("astgrep", "treesitter"):
            args = [sys.executable, __file__, "--case", lang, kind, str(n), arm]
            started = time.perf_counter()
            row: dict[str, object]
            try:
                done = subprocess.run(args, capture_output=True, text=True, timeout=60)
                result = json.loads(done.stdout) if done.returncode == 0 else {}
                row = {"rc": done.returncode, "stderr": done.stderr[-300:], **result}
            except subprocess.TimeoutExpired:
                row = {"rc": "timeout"}
            row.update(
                lang=lang, kind=kind, n=n, arm=arm, wall=round(time.perf_counter() - started, 3)
            )
            rows.append(row)
            print(row, file=sys.stderr)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "pathological.json").write_text(json.dumps(rows, indent=1) + "\n")


if __name__ == "__main__":
    main()
