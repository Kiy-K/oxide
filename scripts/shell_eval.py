"""Real-repository shell evaluation: `uv run python scripts/shell_eval.py [path]`.

Indexes a checkout with the shell slice and reports deterministic structural
counts plus an independent `bash -n` syntax reference. Never executes scripts.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from oxide.capture import capture
from oxide.core.indexing import derive, validate

DEFAULT = Path(__file__).resolve().parents[1] / "docs/cap-removal-eval"


def main() -> None:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else DEFAULT).resolve()
    found = derive(capture(root), "shell-eval")
    validate(found)
    shell_files = [f for f in found.files if f.language in ("bash", "sh")]
    relations = [e for e in found.edges if e.kind in ("imports", "calls", "references")]
    syntax_ok: dict[str, bool] = {}
    for file in shell_files:
        check = subprocess.run(["bash", "-n", str(root / file.path)], capture_output=True)
        syntax_ok[file.path] = check.returncode == 0
    print(
        json.dumps(
            {
                "root": str(root),
                "files": len(found.files),
                "shell_files": len(shell_files),
                "coverage": {
                    "complete": sum(f.coverage == "complete" for f in shell_files),
                    "partial": sum(f.coverage == "partial" for f in shell_files),
                },
                "symbols": sum(e.category == "symbol" for e in found.entities),
                "relations": len(relations),
                "labels": {
                    "proven": sum(e.resolution == "resolved" for e in relations),
                    "ambiguous": sum(e.resolution == "ambiguous" for e in relations),
                    "unresolved": sum(e.resolution == "unresolved" for e in relations),
                },
                "bash_n_accepted": sum(syntax_ok.values()),
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
