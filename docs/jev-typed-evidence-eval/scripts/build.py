"""#40 frozen task set (PROTOCOL §1): fused dumps, candidate rows, file views, eligibility.

Rows use docs/joint-interaction-eval/scripts/prep.py's field definitions, except that
`text` is read at the indexed revision (the checked-out parent / base_commit worktree
that was indexed). Views are views.file_view() unchanged; eligibility is
common.eligible(). Labels enter only `y` (and G5 gold lines); they never reach states.

  build.py heldout      -> tasks/heldout-frozen.jsonl (first 10 eligible per repo, make_tasks order)
  build.py cb <parquet> -> tasks/cb-frozen.jsonl (seeded per-stratum round-robin draw)

Every task line: {set, id, repo (owner/name), query, qclass, stratum, root, views, cand, g5_gold,
commit_date (held-out, UTC day)}. Every drawn/attempted task and why it was ineligible
goes to tasks/<set>-draws.jsonl.
"""
import json, os, random, re, subprocess, sys, tempfile
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, '..', '..', '..'))
J = os.path.expanduser('~/.cache/oxide-jev-eval')
OX = f'{J}/target-frozen/release/oxide'
DUMP = f'{J}/target-frozen/release/examples/fusion_dump'
CAP = ['systemd-run', '--user', '--scope', '-q', '-p', 'MemoryMax=4G', '-p', 'MemorySwapMax=512M',
       '-p', 'CPUQuota=400%', 'nice', '-n', '10']
ENV = {k: v for k, v in os.environ.items() if k not in (
    'OXIDE_EMBED_NATIVE', 'OXIDE_EMBED_URL', 'OXIDE_EMBED_MODEL', 'OXIDE_RETRIEVAL_MODE',
    'OXIDE_CONTEXT_MAX_PRIMARIES', 'OXIDE_TERM_COVERAGE_ALPHA')}
TOP, PER_REPO, SEED = 50, 10, 40
CB_TARGET = {'description': 25, 'quoted': 34, 'other': 10}
DESC = {'NL behavioral description', 'implementation discovery (NL)'}
HELDOUT_REPOS = {'httpx': 'encode/httpx', 'requests': 'psf/requests', 'flask': 'pallets/flask',
                 'ripgrep': 'burntsushi/ripgrep', 'clap': 'clap-rs/clap', 'rayon': 'rayon-rs/rayon',
                 'zod': 'colinhacks/zod', 'axios': 'axios/axios'}
NORM = re.compile(r"^(?:/workspace/[^/]+/|/testbed/)")  # prep.py / cbgold.py

sys.dont_write_bytecode = True
sys.path.insert(0, f'{REPO}/docs/selection-separability-eval/scripts')
sys.path.insert(0, f'{REPO}/docs/typed-evidence-eval/scripts')
_src = open(f'{REPO}/docs/retrieval-failure-taxonomy/scripts/analyze.py').read()
_ns = {}
exec(_src.split('# ---------------------------------------------------------------- per-record analysis')[0], _ns)
classify = _ns['classify']  # the frozen query classifier (the regex part of analyze.py)
from features import is_test  # noqa: E402  production test predicate, as in prep.py
from common import eligible  # noqa: E402
from views import file_view  # noqa: E402


def stratum(q):
    c = classify(q)
    return c, 'description' if c in DESC else 'quoted' if c == 'quoted literal/error text' else 'other'


def fused_dump(root, task):
    with tempfile.NamedTemporaryFile('w', suffix='.jsonl', delete=False) as f:
        f.write(json.dumps({'id': task['id'], 'query': task['query']}) + '\n')
    p = subprocess.run(CAP + [DUMP, root, f.name], capture_output=True, text=True, env=ENV)
    os.unlink(f.name)
    if p.returncode != 0 or not p.stdout.strip():
        return None, f'dump failed: {p.stderr[-200:]}'
    return json.loads(p.stdout.split('\n', 1)[0]), None  # not splitlines(): serde_json leaves U+2028 etc. raw


_lines = {}


def read_lines(root, path):
    k = (root, path)
    if k not in _lines:
        try:
            _lines[k] = open(os.path.join(root, path), errors='replace').read().splitlines()
        except OSError:
            _lines[k] = None
    return _lines[k]


def rows_for(d, root, label):
    """prep.py rows from fused[:50]; text read from the indexed checkout."""
    rows = []
    for fr, (k, fscore, _) in enumerate(d['fused'][:TOP], 1):
        f, qn = k.split('#', 1)
        sp = d['spans'][k]
        lines = read_lines(root, f)
        text = '' if lines is None else '\n'.join(lines[sp[0] - 1:sp[1]])
        is_mod = qn.endswith(':__module__')
        name = re.split(r'\.|::', qn)[-1]
        rows.append({'key': k, 'fr': fr, 'fscore': fscore, 'is_module': is_mod,
                     'is_test': is_test(f, name), 'path': f, 'qname': qn, 'span': sp,
                     'text': text, 'y': label(k, f, sp, is_mod)})
    return rows


def cand(rows, views):
    """Arm-B input (PROTOCOL §4): the fused top-50 rows of the view files, key and fr only."""
    paths = {v['path'] for v in views}
    return [{'key': r['key'], 'path': r['path'], 'fr': r['fr']} for r in rows if r['path'] in paths]


def index(root, log):
    if os.path.exists(f'{root}/.oxide/.done'):
        return True
    p = subprocess.run(CAP + [OX, 'index', '.'], cwd=root, capture_output=True, text=True, env=ENV)
    open(log, 'w').write(p.stderr[-4000:])
    if p.returncode == 0:
        open(f'{root}/.oxide/.done', 'w').close()
    return p.returncode == 0


def heldout():
    tasks = [json.loads(l) for l in open(f'{J}/tasks/heldout-primary.jsonl')]
    gold = json.load(open(f'{J}/tasks/heldout_gold.json'))
    order = [json.loads(l)['id'] for l in open(f'{J}/tasks/raw.jsonl')]  # make_tasks output order
    tasks.sort(key=lambda t: order.index(t['id']))
    kept, draws, per = [], [], {}
    for t in tasks:
        if per.get(t['repo'], 0) >= PER_REPO:
            continue
        g = set(t['gold'])
        rec = {'set': 'heldout', 'id': t['id'], 'repo': HELDOUT_REPOS[t['repo']]}
        d, err = fused_dump(t['path'], t)
        if d is None:
            draws.append({**rec, 'eligible': False, 'why': err})
            continue
        rows = rows_for(d, t['path'], lambda k, f, sp, m: int(k in g))
        views = file_view(rows)
        ok = eligible(views)
        draws.append({**rec, 'eligible': ok, 'why': None if ok else 'not eligible'})
        if not ok:
            continue
        per[t['repo']] = per.get(t['repo'], 0) + 1
        cdate = subprocess.run(['git', '-C', f"{J}/repos/{t['repo']}", 'show', '-s', '--format=%cI', t['commit']],
                               capture_output=True, text=True).stdout.strip()
        qc, st = stratum(t['query'])
        kept.append({**rec, 'query': t['query'], 'qclass': qc, 'stratum': st, 'root': t['path'],
                     'commit': t['commit'], 'gold': t['gold'],
                     'commit_date': datetime.fromisoformat(cdate).astimezone(timezone.utc).date().isoformat(),
                     'views': views, 'cand': cand(rows, views), 'g5_gold': gold[t['id']]['lines']})
    write('heldout', kept, draws)


def checkout(r):
    src = f"{J}/cb-repos/{r['repo'].lower().replace('/', '__')}"
    if not os.path.exists(f'{src}/.git'):
        subprocess.run(['git', 'clone', '-q', '--filter=blob:none', r['repo_url'], src], check=True,
                       capture_output=True, timeout=3600)
    if subprocess.run(['git', '-C', src, 'cat-file', '-e', f"{r['base_commit']}^{{commit}}"],
                      capture_output=True).returncode != 0:
        subprocess.run(['git', '-C', src, 'fetch', '-q', 'origin', r['base_commit']], check=True,
                       capture_output=True, timeout=3600)
    dst = f"{J}/cb-wt/{r['instance_id']}"
    if not os.path.exists(dst):
        subprocess.run(['git', '-C', src, 'worktree', 'add', '-q', '--detach', dst, r['base_commit']],
                       check=True, capture_output=True, timeout=3600)
    head = subprocess.run(['git', '-C', dst, 'rev-parse', 'HEAD'], capture_output=True, text=True).stdout.strip()
    if head != r['base_commit']:
        raise RuntimeError(f"worktree at {head}, expected {r['base_commit']}")
    return dst


def cb(parquet):
    import pyarrow.parquet as pq
    excl_text = open(f'{J}/excl/committed_text.txt').read()
    rows = [r for r in pq.read_table(parquet).to_pylist()
            if r['problem_statement'] and r['instance_id'] not in excl_text]
    kept, draws = [], []
    for st in ('description', 'quoted', 'other'):
        rng = random.Random(SEED)
        by = {}
        for r in sorted(rows, key=lambda r: r['instance_id']):
            if stratum(r['problem_statement'])[1] == st:
                by.setdefault(r['repo'].lower(), []).append(r)
        for name in sorted(by):  # shuffle each repo's list in sorted repo-name order
            rng.shuffle(by[name])
        repos = sorted(by)
        rng.shuffle(repos)
        got = 0
        while got < CB_TARGET[st] and any(by[p] for p in repos):
            for p in repos:
                if not by[p] or got >= CB_TARGET[st]:
                    continue
                r = by[p].pop()
                rec = {'set': 'cb', 'id': r['instance_id'], 'repo': p, 'stratum': st}
                try:
                    root = checkout(r)
                except Exception as e:  # noqa: BLE001  a failed checkout is ineligible, no retry
                    draws.append({**rec, 'eligible': False, 'why': f'checkout: {e}'[:300]})
                    continue
                if not index(root, f"{J}/logs/cbindex-{r['instance_id']}.err"):
                    draws.append({**rec, 'eligible': False, 'why': 'index failed'})
                    continue
                gl = {}
                gc = json.loads(r['gold_context']) if isinstance(r['gold_context'], str) else list(r['gold_context'])
                for it in gc:
                    f = NORM.sub('', it.get('file') or '')
                    if f:
                        gl.setdefault(f, []).append((int(it.get('start_line', 1)), int(it.get('end_line', 1))))
                d, err = fused_dump(root, {'id': r['instance_id'], 'query': r['problem_statement']})
                if d is None:
                    draws.append({**rec, 'eligible': False, 'why': err})
                    continue
                lab = lambda k, f, sp, m: int(not m and any(sp[0] <= b and a <= sp[1] for a, b in gl.get(f, [])))
                crows = rows_for(d, root, lab)
                views = file_view(crows)
                ok = eligible(views)
                draws.append({**rec, 'eligible': ok, 'why': None if ok else 'not eligible'})
                if not ok:
                    continue
                got += 1
                g5 = {f: sorted({x for a, b in v for x in range(a, b + 1)}) for f, v in gl.items()}
                kept.append({**rec, 'query': r['problem_statement'], 'qclass': stratum(r['problem_statement'])[0],
                             'root': root, 'base_commit': r['base_commit'], 'views': views,
                             'cand': cand(crows, views), 'g5_gold': g5})
                print('cb', st, got, r['instance_id'], flush=True)
    write('cb', kept, draws)


def write(name, kept, draws):
    with open(f'{J}/tasks/{name}-frozen.jsonl', 'w') as fh:
        for t in kept:
            fh.write(json.dumps(t) + '\n')
    with open(f'{J}/tasks/{name}-draws.jsonl', 'w') as fh:
        for d in draws:
            fh.write(json.dumps(d) + '\n')
    print(name, 'kept', len(kept), 'drawn', len(draws))


if __name__ == '__main__':
    heldout() if sys.argv[1] == 'heldout' else cb(sys.argv[2])
