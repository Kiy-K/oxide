"""Extract every content search (an `rg`/`grep`/`git grep` pattern) agents ran in the
agent-navigation transcripts, with its split.

usage: agent_searches.py RUNS_DIR TASKS_JSON OUT_JSONL
"""

from __future__ import annotations

import json
import shlex
import sys
from pathlib import Path

# rg/grep options that take a value; their value is never a search pattern.
VALUED = {
    "-g",
    "--glob",
    "--iglob",
    "-t",
    "--type",
    "-T",
    "--type-not",
    "-A",
    "-B",
    "-C",
    "-m",
    "--max-count",
    "-M",
    "--max-columns",
    "-f",
    "--file",
    "--include",
    "--exclude",
    "--exclude-dir",
    "-j",
    "--threads",
    "--sort",
    "--sortr",
    "--color",
    "--colors",
    "--max-depth",
    "-d",
    "--type-add",
    "--encoding",
    "--replace",
}
SEARCHERS = {"rg", "grep", "egrep", "fgrep"}


def inner(command: str) -> str:
    words = shlex.split(command)
    if len(words) >= 3 and words[0].endswith("bash") and words[1] in ("-lc", "-c"):
        return words[2]
    return command


def segments(script: str) -> list[tuple[list[str], bool]]:
    """(words, piped): each simple command, quote-aware, and whether it reads a pipe."""
    lexer = shlex.shlex(script.replace("\n", " ; "), posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    out: list[tuple[list[str], bool]] = []
    words: list[str] = []
    piped = False
    for token in lexer:
        if token and set(token) <= set("|&;()<>"):
            if words:
                out.append((words, piped))
            words, piped = [], token == "|"
        else:
            words.append(token)
    if words:
        out.append((words, piped))
    return out


def patterns(words: list[str]) -> tuple[list[str], set[str], list[str]]:
    """The patterns of one rg/grep invocation, its flags and the paths it searched."""
    found: list[str] = []
    flags: set[str] = set()
    positional: list[str] = []
    i = 2 if words[0] == "git" else 1
    while i < len(words):
        w = words[i]
        if w in ("-e", "--regexp"):
            found.append(words[i + 1] if i + 1 < len(words) else "")
            i += 2
            continue
        if w.startswith("--regexp="):
            found.append(w.split("=", 1)[1])
        elif w in VALUED:
            i += 2
            continue
        elif w.startswith("-") and w != "-":
            flags.add(w)
        else:
            positional.append(w)
        i += 1
    if not found and positional:
        found.append(positional.pop(0))
    return found, flags, positional


def searches(command: str) -> list[dict[str, object]]:
    out: list[dict[str, object]] = []
    try:
        script = inner(command)
    except ValueError:
        return out
    try:
        parsed = segments(script)
    except ValueError:
        return out
    for words, piped in parsed:
        while words and "=" in words[0] and not words[0].startswith("-"):
            words = words[1:]  # VAR=value prefixes
        if not words:
            continue
        head = Path(words[0]).name
        if not (head in SEARCHERS or (head == "git" and words[1:2] == ["grep"])):
            continue
        found, flags, paths = patterns(words)
        if "--files" in flags:
            continue
        fixed = "-F" in flags or "--fixed-strings" in flags or head == "fgrep"
        for pattern in found:
            out.append(
                {
                    "pattern": pattern,
                    "piped": piped,
                    "fixed": fixed,
                    "tool": head,
                    "flags": sorted(flags),
                    "paths": paths,
                }
            )
    return out


def main() -> None:
    runs, tasks_json, out_path = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
    split = {t["id"]: t["split"] for t in json.loads(tasks_json.read_text())["tasks"]}
    rows = []
    for events in sorted(runs.glob("*/*/*/events.jsonl")):
        meta = json.loads((events.parent / "meta.json").read_text())
        for line in filter(None, events.read_text().split("\n")):
            event = json.loads(line)
            item = event.get("item") or {}
            if event.get("type") != "item.completed" or item.get("type") != "command_execution":
                continue
            for search in searches(item["command"]):
                rows.append(
                    {
                        "split": split[meta["task"]],
                        "task": meta["task"],
                        "variant": meta["variant"],
                        "rep": meta["rep"],
                        "item": item["id"],
                        **search,
                    }
                )
    with out_path.open("w") as out:
        for row in rows:
            out.write(json.dumps(row) + "\n")
    print(f"{len(rows)} searches -> {out_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
