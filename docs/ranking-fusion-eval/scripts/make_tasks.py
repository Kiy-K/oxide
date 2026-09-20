#!/usr/bin/env python3
"""Derive independently labeled retrieval tasks from real commits.

For each recent non-merge commit whose changed source files are unchanged
since (so the HEAD index describes exactly the post-commit code), the task
query is the commit message and the gold set is the symbols the commit
actually changed — labeled by the repository's own authors, never by
OXIDE's ranking. Changed-line → symbol mapping uses `oxide review --diff`,
which is structural (line spans), not retrieval.

usage: make_tasks.py <oxide> <repo> <label> <max_tasks> >> tasks.jsonl
"""
import json, os, re, subprocess, sys

oxide, repo, label, max_tasks = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4])
exclude = set()
worktree_dir = None
for i, a in enumerate(sys.argv):
    if a == "--exclude":
        exclude = {json.loads(l)["commit"] for l in open(sys.argv[i + 1]) if l.strip()}
    if a == "--worktree":
        worktree_dir = sys.argv[i + 1]
NATIVE_ENV = {k: v for k, v in os.environ.items() if k not in ("OXIDE_EMBED_NATIVE", "OXIDE_EMBED_URL")}
SRC = (".py", ".ts", ".tsx", ".js", ".jsx", ".rs", ".go", ".rb", ".php", ".c", ".cc", ".cpp", ".h", ".java")
SKIP_WORDS = ("bump", "changelog", "release", "typo", "merge", "version", "pre-commit", "lint", "ci:", "docs:", "[pre-commit.ci]")

def git(*a):
    return subprocess.run(["git", *a], cwd=repo, capture_output=True, text=True).stdout

log = git("log", "--no-merges", "--format=%H%x00%s%x00%b%x1e", "-n", "900")
made = 0
for rec in log.split("\x1e"):
    rec = rec.strip("\n")
    if not rec.strip():
        continue
    sha, subject, body = (rec.split("\x00") + ["", ""])[:3]
    if any(w in subject.lower() for w in SKIP_WORDS) or sha in exclude:
        continue
    files = [f for f in git("show", "--name-only", "--format=", sha).split("\n") if f]
    src = [f for f in files if f.endswith(SRC) and "/test" not in f and not f.startswith(("test", "docs/"))]
    if not src or len(src) > 4 or len(files) > 8:
        continue
    # HEAD mode: every changed source file must be byte-identical between the
    # commit and HEAD. Worktree mode: check the commit out and index it, so
    # high-churn repositories contribute tasks too.
    if worktree_dir is None:
        if any(subprocess.run(["git", "diff", "--quiet", sha, "HEAD", "--", f], cwd=repo).returncode != 0 for f in src):
            continue
    body = re.sub(r"(?im)^(co-authored-by|signed-off-by|fixes|closes|refs?|see also).*$", "", body)
    body = re.sub(r"\(#\d+\)|#\d+|https?://\S+", "", body)
    query = re.sub(r"\s+", " ", (subject + " " + body)).strip()
    query = re.sub(r"\(#?\d+\)$", "", query).strip()
    if len(query.split()) < 6:
        continue
    query = query[:400]
    task_path = repo
    if worktree_dir is not None:
        task_path = os.path.join(worktree_dir, f"{label}-{sha[:8]}")
        if not os.path.exists(task_path):
            if subprocess.run(["git", "worktree", "add", "-q", "--detach", task_path, sha], cwd=repo).returncode != 0:
                continue
            p = subprocess.run([oxide, "index", "."], cwd=task_path, env=NATIVE_ENV, capture_output=True, text=True)
            if p.returncode != 0:
                continue
    try:
        rv = subprocess.run([oxide, "review", "--diff", f"{sha}~1..{sha}", "--json"], cwd=task_path,
                            capture_output=True, text=True, env={k: v for k, v in os.environ.items() if k not in ("OXIDE_EMBED_NATIVE", "OXIDE_EMBED_URL")})
        ctx = json.loads(rv.stdout)
    except Exception:
        continue
    gold = sorted({f"{c['file']}#{c['qualified_name']}" for c in ctx.get("changed_symbols", [])
                   if c["file"] in src and not c["qualified_name"].endswith(":__module__")})
    if not gold or len(gold) > 8:
        continue
    print(json.dumps({"id": f"{label}-{sha[:8]}", "repo": label, "commit": sha, "query": query,
                      "gold": gold, "files": src, "path": os.path.abspath(task_path)}))
    made += 1
    if made >= max_tasks:
        break
print(f"{label}: {made} tasks", file=sys.stderr)
