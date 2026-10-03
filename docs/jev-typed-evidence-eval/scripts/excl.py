"""#40 exclusions (PROTOCOL §1 *Exclusions*) and the step-1 expected post-date N.

Used SHAs come from every committed docs/**/*.jsonl[.gz] and eval-agent/**/*.jsonl file
at the frozen commit, read with `git show` (so untracked files never count):
top-level string fields commit/sha/base_commit/parent matching ^[0-9a-f]{8,40}$, plus the
8-hex suffix of id/task values matching -[0-9a-f]{8}$. A used SHA's parent is resolved per
clone with `git rev-parse --verify -q <u>~1` only when <u> resolves uniquely there.
make_tasks.py --exclude matches full SHAs, so the 8-char prefix rule is expanded here into
the full SHAs of every excluded commit in each clone's 900-commit scan window.

usage: excl.py <frozen_commit> <repos_dir> <out_dir> [--expected-n YYYY-MM-DD]
"""
import gzip, io, json, os, re, subprocess, sys
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.abspath(__file__)) + '/../../..'
SHA = re.compile(r'^[0-9a-f]{8,40}$')
SUF = re.compile(r'-([0-9a-f]{8})$')
# make_tasks.py pre-index filters, copied verbatim (its loop runs them before any worktree)
SRC = (".py", ".ts", ".tsx", ".js", ".jsx", ".rs", ".go", ".rb", ".php", ".c", ".cc", ".cpp", ".h", ".java")
SKIP_WORDS = ("bump", "changelog", "release", "typo", "merge", "version", "pre-commit", "lint", "ci:", "docs:", "[pre-commit.ci]")


def git(repo, *a):
    return subprocess.run(['git', '-C', repo, *a], capture_output=True, text=True).stdout


def committed_records(frozen):
    files = [f for f in git(ROOT, 'ls-tree', '-r', '--name-only', frozen).split('\n')
             if (f.startswith('docs/') and (f.endswith('.jsonl') or f.endswith('.jsonl.gz')))
             or (f.startswith('eval-agent/') and f.endswith('.jsonl'))]
    texts = []
    for f in files:
        raw = subprocess.run(['git', '-C', ROOT, 'show', f'{frozen}:{f}'], capture_output=True).stdout
        texts.append(gzip.decompress(raw).decode('utf-8', 'replace') if f.endswith('.gz') else raw.decode('utf-8', 'replace'))
    return files, texts


def used_shas(texts):
    used = set()
    for txt in texts:
        for line in io.StringIO(txt):
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if not isinstance(d, dict):
                continue
            for k in ('commit', 'sha', 'base_commit', 'parent'):
                v = d.get(k)
                if isinstance(v, str) and SHA.match(v):
                    used.add(v)
            for k in ('id', 'task'):
                v = d.get(k)
                m = SUF.search(v) if isinstance(v, str) else None
                if m:
                    used.add(m.group(1))
    return used


def log900(repo):
    log = git(repo, 'log', '--no-merges', '--format=%H%x00%P%x00%cI%x00%s%x00%b%x1e', '-n', '900')
    for rec in log.split('\x1e'):
        rec = rec.strip('\n')
        if not rec.strip():
            continue
        sha, parents, cdate, subject, body = (rec.split('\x00') + [''] * 5)[:5]
        yield sha, parents.split(), cdate, subject, body


def passes_filters(repo, sha, subject, body):
    """make_tasks.py's message/skip-word/file filters (no worktree/index/review)."""
    if any(w in subject.lower() for w in SKIP_WORDS):
        return False
    files = [f for f in git(repo, 'show', '--name-only', '--format=', sha).split('\n') if f]
    src = [f for f in files if f.endswith(SRC) and "/test" not in f and not f.startswith(("test", "docs/"))]
    if not src or len(src) > 4 or len(files) > 8:
        return False
    body = re.sub(r"(?im)^(co-authored-by|signed-off-by|fixes|closes|refs?|see also).*$", "", body)
    body = re.sub(r"\(#\d+\)|#\d+|https?://\S+", "", body)
    query = re.sub(r"\s+", " ", (subject + " " + body)).strip()
    query = re.sub(r"\(#?\d+\)$", "", query).strip()
    return len(query.split()) >= 6


def main():
    frozen, repos_dir, out = sys.argv[1], sys.argv[2], sys.argv[3]
    cutoff = sys.argv[sys.argv.index('--expected-n') + 1] if '--expected-n' in sys.argv else None
    os.makedirs(out, exist_ok=True)
    files, texts = committed_records(frozen)
    used = used_shas(texts)
    summary = {'frozen_commit': frozen, 'files_scanned': len(files), 'used_shas': len(used), 'repos': {}}
    for name in sorted(os.listdir(repos_dir)):
        repo = os.path.join(repos_dir, name)
        x = {u[:8] for u in used}
        for u in sorted(used):
            if git(repo, 'rev-parse', '--verify', '-q', f'{u}^{{commit}}').strip():
                p = git(repo, 'rev-parse', '--verify', '-q', f'{u}~1').strip()
                if p:
                    x.add(p[:8])
        excluded, first20, post = [], [], 0
        for sha, parents, cdate, subject, body in log900(repo):
            if sha[:8] in x or (parents and parents[0][:8] in x):
                excluded.append(sha)
                continue
            if cutoff and len(first20) < 20 and passes_filters(repo, sha, subject, body):
                day = datetime.fromisoformat(cdate).astimezone(timezone.utc).date().isoformat()
                first20.append((sha, day))
                post += day > cutoff
        with open(os.path.join(out, f'exclude-{name}.jsonl'), 'w') as fh:
            for s in excluded:
                fh.write(json.dumps({'commit': s}) + '\n')
        summary['repos'][name] = {'excluded_in_window': len(excluded)}
        if cutoff:
            summary['repos'][name].update(first20=len(first20), post_date_in_first20=post, contributes=min(10, post))
    if cutoff:
        summary['expected_post_date_n'] = sum(r['contributes'] for r in summary['repos'].values())
        summary['date_rule'] = f'committer date (UTC), day granularity, post iff > {cutoff}'
    # ContextBench excludes every instance_id appearing anywhere in the same committed files
    with open(os.path.join(out, 'committed_text.txt'), 'w') as fh:
        fh.write('\n'.join(texts))
    json.dump(summary, open(os.path.join(out, 'exclusions.json'), 'w'), indent=1)
    print(json.dumps(summary, indent=1))


if __name__ == '__main__':
    main()
