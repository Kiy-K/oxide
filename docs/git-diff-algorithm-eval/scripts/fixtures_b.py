"""Fixture cases built exactly like tests/candidate_output_golden.rs::repo()."""
import os, shutil, subprocess, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gitenv import env_for, rust_lines, diff_argv, parse_unified
import stage_b
from stage_b import run_state
FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "../../../fixtures")
OUT = sys.argv[1]
def git(root, *a):
    e = env_for("default"); e.update(GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t",
        GIT_AUTHOR_DATE="2026-01-01T00:00:00Z", GIT_COMMITTER_DATE="2026-01-01T00:00:00Z")
    subprocess.run(["git", *a], cwd=root, env=e, check=True, capture_output=True)
def roles(root):
    files = {
     "pkg/core.py": "def normalize(value):\n    return value.strip().lower()\n\n\ndef compute(value):\n    return normalize(value) + \"!\"\n",
     "pkg/service.py": "from pkg.core import compute\n\n\nclass Service:\n    def run(self, value):\n        return compute(value)\n",
     "tests/helpers.py": "from pkg.core import compute\n\n\ndef build_case():\n    return compute(\" Case \")\n",
     "tests/test_core.py": "from pkg.core import compute, normalize\n\n\ndef test_compute():\n    assert compute(\" A \") == \"a!\"\n\n\ndef test_normalize():\n    assert normalize(\" B \") == \"b\"\n",
     "pkg/core_test.py": "from pkg.core import compute\n\n\ndef check_compute():\n    assert compute(\"x\") == \"x!\"\n"}
    for p, s in files.items():
        os.makedirs(os.path.join(root, os.path.dirname(p)), exist_ok=True); open(os.path.join(root, p), "w").write(s)
cases = [
 ("py_repo", lambda r: shutil.copytree(f"{FIX}/py_repo", r, dirs_exist_ok=True), "oxidepy/retry.py", "oxidepy/http_client.py",
  ["retry with exponential backoff", "refresh auth token", "cache"]),
 ("ts_repo", lambda r: shutil.copytree(f"{FIX}/ts_repo", r, dirs_exist_ok=True), "src/net/retry.ts", "src/net/client.ts",
  ["retry policy backoff", "auth service login", "button click"]),
 ("roles", roles, "pkg/core.py", "pkg/service.py", ["compute normalized value", "build case helper"]),
]
for name, files, changed, partner, queries in cases:
    root = os.path.join(OUT, "work", name); shutil.rmtree(root, ignore_errors=True); os.makedirs(root)
    files(root)
    full = open(os.path.join(root, changed), newline="").read()
    lines = rust_lines(full)
    open(os.path.join(root, changed), "w", newline="").write("\n".join(lines[: len(lines)//2]))
    git(root, "init", "-q"); git(root, "add", "-A"); git(root, "commit", "-qm", "base")
    cm = "#" if changed.endswith(".py") else "//"
    for rev in (1, 2):
        for f in (changed, partner):
            p = os.path.join(root, f); t = open(p, newline="").read() + f"\n{cm} rev {rev}\n"; open(p, "w", newline="").write(t)
        git(root, "commit", "-qam", f"rev {rev}")
    open(os.path.join(root, changed), "w", newline="").write(full)
    for a in ("myers", "histogram", "patience", "myers_noindent"):
        t = subprocess.run(diff_argv(""), cwd=root, env=env_for(a), capture_output=True, text=True, check=True).stdout
        open(os.path.join(OUT, f"{name}.{a}.diff"), "w").write(t)
        print(name, a, parse_unified(t))
    subprocess.run([stage_b.OXIDE, "index", "."], cwd=root, env={**env_for("default"), "OXIDE_EMBED_NATIVE": "hashed"}, check=True, capture_output=True)
    run_state(root, queries, os.path.join(OUT, "out", name), [("bal", []), ("bal_blast", ["--blast-radius"]), ("quality", ["--profile", "quality"])])
