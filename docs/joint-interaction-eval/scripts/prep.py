"""Issue #39: build the fused top-50 candidate universe with source text (PROTOCOL §3-4).

Reads the committed ranking-fusion dumps (the frozen #32 inputs) and the task
repos at each task's indexed revision (`git show <sha>:<path>`). Labels go in
`y` and never enter the teacher input. Output: results/cands.jsonl.gz.

usage: prep.py <src dir with repo clones> <ContextBench full.parquet>
"""
import gzip, json, os, re, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, '..', '..', '..'))
R = f'{REPO}/docs/ranking-fusion-eval/results/'
OUT = os.path.join(HERE, '..', 'results')
sys.dont_write_bytecode = True
sys.path.insert(0, f'{REPO}/docs/retrieval-failure-taxonomy/scripts')
sys.path.insert(0, f'{REPO}/docs/selection-separability-eval/scripts')
from analyze import classify  # query-only regex classifier (#32 §13)
from features import is_test  # production test predicate (#32 §5)

SETS = [('heldout', 'dump-heldout', 'heldout-clean'), ('cb', 'dump-contextbench', 'cb-tasks')]
TOP = 50
# Held-out tasks whose dump path is not a `/wt/` worktree were indexed at the
# repo's HEAD, recovered by the taxonomy (retrieval-failure-taxonomy/scripts/build_inputs.py).
HEADS = {'ripgrep': '3fce3b5bb0236da2df6d99672afb8a719642eca7',
         'httpx': 'b5addb64f0161ff6bfe94c124ef76f6a1fba5254',
         'pylint': 'ba5c0c79bc9c5f752404de96e4cde7e3aa684c82'}
NORM = re.compile(r"^(?:/workspace/[^/]+/|/testbed/)")  # retrieval-failure-taxonomy/scripts/cbgold.py


def cb_gold(parquet):
    import pandas as pd
    out = {}
    for _, r in pd.read_parquet(parquet).iterrows():
        g = json.loads(r.gold_context) if isinstance(r.gold_context, str) else list(r.gold_context)
        gl = {}
        for it in g:
            f = NORM.sub('', it.get('file') or '')
            if f:
                gl.setdefault(f, []).append((int(it.get('start_line', 1)), int(it.get('end_line', 1))))
        out[r.instance_id] = gl
    return out


_files = {}


def file_lines(src, repo, sha, path):
    k = (repo, sha, path)
    if k not in _files:
        cwd = os.path.join(src, repo)
        mode = subprocess.run(['git', 'ls-tree', sha, '--', path], cwd=cwd,
                              capture_output=True, text=True).stdout.split(' ')[0]
        p = subprocess.run(['git', 'show', f'{sha}:{path}'], cwd=cwd, capture_output=True)
        if p.returncode == 0 and mode == '120000':  # symlink: the indexer read its target
            target = os.path.normpath(os.path.join(os.path.dirname(path), p.stdout.decode().strip()))
            return file_lines(src, repo, sha, target)
        _files[k] = p.stdout.decode('utf-8', 'replace').splitlines() if p.returncode == 0 else None
    return _files[k]


def main():
    src, parquet = sys.argv[1], sys.argv[2]
    cbg = cb_gold(parquet)
    stats = {'cands': 0, 'missing_file': 0, 'module_len_match': 0, 'module_len_mismatch': 0}
    with gzip.open(os.path.join(OUT, 'cands.jsonl.gz'), 'wt') as out:
        for s, dump, tasks in SETS:
            T = {json.loads(l)['id']: json.loads(l) for l in open(R + tasks + '.jsonl')}
            for l in gzip.open(R + dump + '.jsonl.gz', 'rt'):
                d = json.loads(l)
                t = T.get(d['id'])
                if t is None:
                    continue
                if s == 'heldout':
                    sha = t['commit'] if '/wt/' in t['path'] else HEADS[t['repo']]
                    repo, gold, gl = t['repo'], set(t['gold']), None
                else:
                    repo, sha, gold, gl = t['repo'].split('/')[1], t['base_commit'], None, cbg[d['id']]
                for fr, (k, fscore, _) in enumerate(d['fused'][:TOP], 1):
                    f, qn = k.split('#', 1)
                    sp = d['spans'][k]
                    lines = file_lines(src, repo, sha, f)
                    stats['cands'] += 1
                    if lines is None:
                        stats['missing_file'] += 1
                        text = ''
                    else:
                        text = '\n'.join(lines[sp[0] - 1:sp[1]])
                    is_mod = qn.endswith(':__module__')
                    if is_mod and lines is not None:
                        stats['module_len_match' if sp[1] == max(len(lines), 1) else 'module_len_mismatch'] += 1
                    if s == 'heldout':
                        y = int(k in gold)
                    else:  # #32 PROTOCOL §4 bearer rule
                        y = int(not is_mod and any(sp[0] <= b and a <= sp[1] for a, b in gl.get(f, [])))
                    name = re.split(r'\.|::', qn)[-1]
                    out.write(json.dumps({
                        'set': s, 'task': d['id'], 'repo': repo, 'key': k, 'fr': fr, 'fscore': fscore,
                        'band': 'B1' if fr <= 5 else 'B2' if fr <= 16 else 'B3',
                        'qclass': classify(t['query']), 'is_module': is_mod,
                        'is_test': is_test(f, name), 'path': f, 'qname': qn, 'span': sp,
                        'query': t['query'], 'text': text, 'y': y}) + '\n')
    print(json.dumps(stats))


if __name__ == '__main__':
    main()
