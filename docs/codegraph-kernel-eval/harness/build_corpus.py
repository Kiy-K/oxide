#!/usr/bin/env python3
"""Research-only (issue #23): builds the differential corpus manifest.

Groups (all recorded in the manifest's `group` field, none silently dropped):
  conformance  - every file under fixtures/conformance/<lang>/ (OXIDE goldens)
  relations    - the inline sources of tests/precomputed_relations_conformance.rs,
                 materialized to work/relations/<n>/<name>
  fixtures     - fixtures/py_repo and fixtures/ts_repo (benchmark fixtures)
  real:<repo>  - systematic sample of one pinned real repo per language:
                 sorted candidate list, every k-th file, cap REAL_CAP files.
                 No size, content or outcome filter.

Language assignment is OXIDE's own `scanner::language_for_source` (via the
example's `classify` mode). Files OXIDE does not index are left out, as
OXIDE's scanner would; OXIDE-supported files CodeGraph cannot take (markdown)
stay in with cg_lang="" so they surface as "unsupported".
"""
import json, os, re, subprocess, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
WORK = HERE / "work"
REPOS = Path.home() / ".cache/oxide-contextbench/repos"
REAL_CAP = 120
EXAMPLE = ROOT / "target/release/examples/extraction_differential"

REAL = [  # (group, repo dir, subdirs, extensions)
    ("real:flask", REPOS / "flask@7ee9ceb71e86", ["src", "tests"], {".py"}),
    ("real:darkreader", REPOS / "darkreader@a787eb511f45", ["src"], {".ts", ".tsx"}),
    ("real:axios", REPOS / "axios@0abc70564746", ["lib"], {".js"}),
    ("real:tokio", REPOS / "tokio@43c224ff47e4", ["tokio/src"], {".rs"}),
    ("real:gin", HERE / "corpus-repos/gin", ["."], {".go"}),
    ("real:gson", HERE / "corpus-repos/gson", ["gson/src/main/java"], {".java"}),
    ("real:zstd", REPOS / "zstd@823a28a1f4cb", ["lib"], {".c", ".h"}),
    ("real:fmt", HERE / "corpus-repos/fmt", ["include", "src", "test"], {".h", ".cc", ".cpp"}),
]

def cg_lang(oxide_lang, path):
    if oxide_lang == "markdown":
        return ""
    if oxide_lang == "javascript":
        return "jsx" if path.endswith(".jsx") else "javascript"
    return oxide_lang

def relations_sources():
    src = (ROOT / "tests/precomputed_relations_conformance.rs").read_text()
    lit = r'"((?:[^"\\]|\\.)*)"'
    out = []
    for n, m in enumerate(re.finditer(r"indexed_with_relations\(&\[(.*?)\]\);", src, re.S)):
        for t in re.finditer(r"\(\s*" + lit + r",\s*" + lit + r",?\s*\)", m.group(1), re.S):
            name, body = t.group(1), t.group(2)
            body = re.sub(r"\\\n\s*", "", body)  # Rust line-continuation
            body = body.encode().decode("unicode_escape")
            p = WORK / "relations" / f"{n:02d}" / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(body)
            out.append((f"relations/{n:02d}", p, name))
    return out

def main():
    cands = []  # (group, abs path, rel)
    for d in sorted((ROOT / "fixtures/conformance").iterdir()):
        if d.is_dir():
            for p in sorted(d.rglob("*")):
                if p.is_file():
                    cands.append(("conformance", p, f"{d.name}/{p.relative_to(d)}"))
    for g, p, rel in relations_sources():
        cands.append(("relations", p, f"{g}/{rel}"))
    for fx in ["py_repo", "ts_repo"]:
        base = ROOT / "fixtures" / fx
        for p in sorted(base.rglob("*")):
            if p.is_file():
                cands.append(("fixtures", p, f"{fx}/{p.relative_to(base)}"))
    for group, repo, subdirs, exts in REAL:
        files = sorted(
            p for s in subdirs for p in (repo / s).rglob("*")
            if p.is_file() and p.suffix in exts and "/." not in str(p.relative_to(repo))
        )
        assert files, f"{group}: no candidate files under {repo}"
        k = max(1, -(-len(files) // REAL_CAP))
        for p in files[::k][:REAL_CAP]:
            cands.append((group, p, str(p.relative_to(repo))))

    WORK.mkdir(exist_ok=True)
    paths = WORK / "paths.txt"
    paths.write_text("".join(f"{p}\n" for _, p, _ in cands))
    langs = dict(
        l.split("\t") for l in subprocess.run(
            [str(EXAMPLE), "classify", str(paths)], check=True, capture_output=True, text=True
        ).stdout.splitlines()
    )
    n = 0
    with open(WORK / "manifest.jsonl", "w") as f:
        for group, p, rel in cands:
            ol = langs.get(str(p), "-")
            if ol == "-":
                continue
            f.write(json.dumps({"id": f"{group}::{rel}", "group": group, "path": str(p),
                                "rel": rel, "oxide_lang": ol, "cg_lang": cg_lang(ol, rel)}) + "\n")
            n += 1
    print(f"{n} files -> {WORK / 'manifest.jsonl'}", file=sys.stderr)

if __name__ == "__main__":
    main()
