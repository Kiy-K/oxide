"""classify.py REPO OUT.json [--commits 300]: Part A, edit classes over real history.

Parses each modified source file of the newest non-merge commits before and after (production
parsers, ``git cat-file``) and classes it ``layout`` / ``body`` / ``interface`` by its
interface projection: every parse fact except byte offsets and the use lists (calls, refs)
that resolvers read only inside the owning file. See ../README.md, Part A.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from oxide.core.indexing import dialect
from oxide.core.languages import go, python, rust, typescript

MANIFESTS = ("go.mod", "Cargo.toml", "package.json")
RANK = {"layout": 0, "body": 1, "interface": 2, "structural": 3}


def facts(lang: str, data: bytes) -> list[Any] | str:
    if lang == "python":
        p: Any = python.parse(data)
        return p if isinstance(p, str) else python.encode(p)
    if lang == "go":
        p = go.parse(data)
        return p if isinstance(p, str) else go.encode(p)
    if lang == "rust":
        p = rust.parse(data)
        return p if isinstance(p, str) else rust.encode(p)
    p = typescript.parse(data, language=lang)  # type: ignore[arg-type]
    return p if isinstance(p, str) else typescript.encode(p)


def names(uses: list[list[Any]]) -> list[Any]:
    return [u[0] for u in uses]


def split(lang: str, enc: list[Any]) -> tuple[Any, Any]:
    """(interface projection, use names) of encoded facts; offsets dropped from both."""
    if lang == "python":
        scopes, rebound, _diag, attrs = enc
        face = [
            [s[0], s[1] and [s[1][0], s[1][1], s[1][4]], s[3], [i[:5] for i in s[4]],
             names(s[6]), s[7], s[8], s[9], s[10]]
            for s in scopes
        ]  # fmt: skip
        return [face, rebound, attrs], [names(s[5]) for s in scopes]
    if lang == "go":
        package, imports, scopes, _diag = enc
        face = [[s[0], s[1] and [*s[1][:3], s[1][5]], s[2], s[3]] for s in scopes]
        uses = [[names(s[4]), names(s[5])] for s in scopes]
        return [package, [i[:2] for i in imports], face], uses
    if lang == "rust":
        imports, mods, scopes, _diag = enc
        face = [[s[0], s[1] and [*s[1][:2], *s[1][4:]], s[2]] for s in scopes]
        uses = [[names(s[3]), names(s[4])] for s in scopes]
        return [[i[:6] for i in imports], [m[:5] for m in mods], face], uses
    scopes, imports, rebound, _diag = enc
    face = [
        [s[0], s[1] and [s[1][0], s[1][1], s[1][4], s[1][5]], s[3], names(s[5])] for s in scopes
    ]
    return [face, [i[:5] for i in imports], rebound], [names(s[4]) for s in scopes]


# Exploratory (post hoc, not preregistered): facts of a function scope that only its own
# file's lexical lookups read. Index into a projected scope, per language.
LOCAL = {"python": (2, 5, 6), "go": (3,), "rust": (2,)}


def refine(lang: str, face: Any) -> Any:
    """``face`` with function-local bindings blanked (``refined`` class)."""
    scopes = face[0] if lang not in ("go", "rust") else face[2]
    for s in scopes:
        d = s[1]
        if d and (not d[0] if lang == "python" else d[0] in ("function", "method")):
            for k in LOCAL.get(lang, (2,)):
                s[k] = None
    return face


def classify(lang: str, before: bytes, after: bytes) -> tuple[str, str]:
    """(preregistered class, refined class)."""
    a, b = facts(lang, before), facts(lang, after)
    if isinstance(a, str) or isinstance(b, str):
        return ("layout", "layout") if a == b else ("interface", "interface")
    (fa, ua), (fb, ub) = split(lang, a), split(lang, b)
    use = "layout" if ua == ub else "body"
    pre = "interface" if fa != fb else use
    return pre, "interface" if refine(lang, fa) != refine(lang, fb) else use


def manifest(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return name in MANIFESTS or (
        name.startswith(("tsconfig", "jsconfig")) and name.endswith(".json")
    )


class Blobs:
    def __init__(self, repo: str) -> None:
        self.proc = subprocess.Popen(
            ["git", "-C", repo, "cat-file", "--batch"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
        )

    def get(self, spec: str) -> bytes:
        assert self.proc.stdin and self.proc.stdout
        self.proc.stdin.write(spec.encode() + b"\n")
        self.proc.stdin.flush()
        header = self.proc.stdout.readline().split()
        if header[-1] == b"missing":
            raise KeyError(spec)
        data = self.proc.stdout.read(int(header[2]))
        self.proc.stdout.read(1)
        return data


def main() -> None:
    repo, out = sys.argv[1], Path(sys.argv[2])
    limit = int(sys.argv[sys.argv.index("--commits") + 1]) if "--commits" in sys.argv else 300
    args = ["-n", "2000", "--no-merges", "-M", "--name-status", "--format=@%H"]
    log = subprocess.run(
        ["git", "-C", repo, "log", *args], capture_output=True, text=True, check=True
    ).stdout
    blobs, commits = Blobs(repo), []
    for chunk in log.lstrip("@").split("\n@"):
        sha, *lines = chunk.strip().split("\n")
        changes = [line.split("\t") for line in lines if line]
        if not any(dialect(c[-1]) for c in changes):
            continue
        structural = any(manifest(c[-1]) for c in changes) or any(
            c[0][0] in "ADRC" and (dialect(c[-1]) or dialect(c[1])) for c in changes
        )
        files = []
        for status, *paths in changes:
            path, lang = paths[-1], dialect(paths[-1])
            if status != "M" or lang is None:
                continue
            try:
                pre, ref = classify(lang, blobs.get(f"{sha}^:{path}"), blobs.get(f"{sha}:{path}"))
            except KeyError:
                pre = ref = "interface"
            files.append({"path": path, "lang": lang, "class": pre, "refined": ref})
        commit = {"commit": sha}
        for field in ("class", "refined"):
            worst = max((f[field] for f in files), key=RANK.__getitem__, default="layout")
            commit[field] = "structural" if structural else worst
        commits.append({**commit, "files": files})
        if len(commits) == limit:
            break
    out.write_text(json.dumps(commits, indent=1) + "\n")
    summary = {}
    for field in ("class", "refined"):
        edits = [f[field] for c in commits for f in c["files"]]
        summary[field] = {
            "commits": {k: sum(c[field] == k for c in commits) for k in RANK},
            "edits": {k: edits.count(k) for k in ("layout", "body", "interface")},
        }
    print(json.dumps(summary))


if __name__ == "__main__":
    main()
