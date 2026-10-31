"""Study A replay: rerun real held-out agent searches with rg and with an ast-grep rule
encoding the same syntactic intent, on the task's exact source.

`replay_rules.json` (written from the patterns alone, before any replay output was
seen) holds, per distinct (task, pattern): the agent's `paths` and `flags`, and an
ast-grep `rule` config (None when the intent has no syntactic form).

For each search the task tree is `git archive <base_commit>` of its worktree (the
bytes the agent saw), extracted once under WORK/trees. rg runs as the agent ran it
(pattern, -i/-w/-F, paths); ast-grep runs over the `.py` files rg would search there.
Reported: rg hits in code files and in other files, ast-grep matches, rg code hits not
inside any ast-grep match (`rg_noise`), ast-grep matches containing no rg hit
(`sg_only`), and samples of both for the manual audit.

    replay.py RULES_JSON TASKS_JSON WORK_DIR OUT_JSON
"""

from __future__ import annotations

import io
import json
import subprocess
import sys
import tarfile
from collections import defaultdict
from pathlib import Path

from ast_grep_py import SgRoot

WORKTREES = [
    Path.home() / "Projects/oxide-eval-data/oxide-capremoval-eval/cb-wt",
    Path.home() / "Projects/oxide-eval-data/oxide-jev-eval/cb-wt",
]
KEEP_FLAGS = {"-i", "-w", "-F", "--ignore-case", "--word-regexp", "--fixed-strings", "-S"}
SAMPLE = 8


def tree(task: dict[str, str], work: Path) -> Path:
    out = work / "trees" / task["id"]
    if out.is_dir():
        return out
    wt = next(w / task["id"] for w in WORKTREES if (w / task["id"]).is_dir())
    archive = subprocess.run(
        ["git", "-C", str(wt), "archive", task["base_commit"]], capture_output=True, check=True
    ).stdout
    out.mkdir(parents=True)
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        tar.extractall(out, filter="data")
    return out


def rg(root: Path, pattern: str, flags: list[str], paths: list[str]) -> list[tuple[str, int]]:
    args = ["rg", "-n", "--no-heading", "--with-filename", "--color", "never"]
    args += [f for f in flags if f in KEEP_FLAGS]
    args += ["-e", pattern, "--", *(paths or ["."])]
    done = subprocess.run(args, cwd=root, capture_output=True, text=True)
    hits = []
    for line in done.stdout.splitlines():
        path, number, _ = line.split(":", 2)
        hits.append((str(Path(path)), int(number) - 1))
    return hits


def code_files(root: Path, paths: list[str]) -> list[str]:
    args = ["rg", "--files", "-t", "py", "--", *(paths or ["."])]
    done = subprocess.run(args, cwd=root, capture_output=True, text=True)
    # rg lists explicitly named files whatever their type (deviation fix, see README)
    return [str(Path(p)) for p in done.stdout.splitlines() if p.endswith(".py")]


def main() -> None:
    rules = json.loads(Path(sys.argv[1]).read_text())
    tasks = {t["id"]: t for t in json.loads(Path(sys.argv[2]).read_text())["tasks"]}
    work, out_path = Path(sys.argv[3]), Path(sys.argv[4])
    rows = []
    for entry in rules:
        root = tree(tasks[entry["task"]], work)
        hits = rg(root, entry["pattern"], entry["flags"], entry["paths"])
        code = [h for h in hits if h[0].endswith(".py")]
        row = {k: entry[k] for k in ("task", "pattern", "paths", "note")}
        row.update(rg_code_hits=len(code), rg_other_hits=len(hits) - len(code))
        if entry["rule"] is None:
            row["sg_matches"] = None
            rows.append(row)
            continue
        spans: dict[str, list[tuple[int, int]]] = defaultdict(list)
        texts: dict[tuple[str, int], str] = {}
        for path in code_files(root, entry["paths"]):
            text = (root / path).read_text("utf-8", "replace")
            for node in SgRoot(text, "python").root().find_all(entry["rule"]):
                r = node.range()
                spans[path].append((r.start.line, r.end.line))
                texts[(path, r.start.line)] = node.text()[:160]
        lines = {p: (root / p).read_text("utf-8", "replace").split("\n") for p, _ in code}
        noise = [h for h in code if not any(s <= h[1] <= e for s, e in spans[h[0]])]
        hit_lines: dict[str, set[int]] = defaultdict(set)
        for p, n in code:
            hit_lines[p].add(n)
        sg_only = [
            (p, s)
            for p, ss in spans.items()
            for s, e in ss
            if not any(s <= n <= e for n in hit_lines[p])
        ]
        row.update(
            sg_matches=sum(len(v) for v in spans.values()),
            rg_noise=len(noise),
            sg_only=len(sg_only),
            rg_noise_sample=[
                {"path": p, "line": n + 1, "text": lines[p][n].strip()[:160]}
                for p, n in sorted(noise)[:SAMPLE]
            ],
            sg_only_sample=[
                {"path": p, "line": n + 1, "text": texts[(p, n)]}
                for p, n in sorted(sg_only)[:SAMPLE]
            ],
        )
        rows.append(row)
        print(row["pattern"][:60], row["rg_code_hits"], row["sg_matches"], file=sys.stderr)
    out_path.write_text(json.dumps(rows, indent=1) + "\n")


if __name__ == "__main__":
    main()
