"""#42 TreeSelect v0 runner. Research binary = frozen commit 96abae8 + research.patch.

  run.py parity  -> results/parity_p1.tsv  (P1: switch off, `oxide context --json`, research vs frozen)
  run.py arms    -> results/packs.jsonl    (A, B, TX, T, TC, T4096 per task, two runs each; resumable)
  run.py rss     -> results/rss_o1.tsv     (O1 memory: 3 largest corpora, `A` vs `A,T`, alternating, 2 reps)

Tasks are #40's frozen 149 (~/.cache/oxide-jev-eval/tasks/{heldout,cb}-frozen.jsonl): only
id, query and root are read here. #40's Jev scores are never read.
"""
import json, os, sqlite3, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
RES = os.path.join(HERE, '..', 'results')
J = os.path.expanduser('~/.cache/oxide-jev-eval')
X = os.path.expanduser('~/.cache/oxide-treeselect-eval')
FROZEN = f'{J}/target-frozen/release/oxide'
RESEARCH = f'{X}/target/release/oxide'
PACK = f'{X}/target/release/examples/treeselect_pack'
CAP = ['systemd-run', '--user', '--scope', '-q', '-p', 'MemoryMax=4G', '-p', 'MemorySwapMax=512M',
       '-p', 'CPUQuota=200%', 'nice', '-n', '10']
ENV = {k: v for k, v in os.environ.items() if k not in (
    'OXIDE_EMBED_NATIVE', 'OXIDE_EMBED_URL', 'OXIDE_EMBED_MODEL', 'OXIDE_RETRIEVAL_MODE',
    'OXIDE_CONTEXT_MAX_PRIMARIES', 'OXIDE_TERM_COVERAGE_ALPHA', 'OXIDE_DEBUG_DUMP_KEPT')}
sys.dont_write_bytecode = True


def tasks():
    return [t for s in ('heldout', 'cb') for t in map(json.loads, open(f'{J}/tasks/{s}-frozen.jsonl'))]


def pack(root, items, arms):
    """Run the harness on one corpus; returns (records, VmHWM kB)."""
    with tempfile.NamedTemporaryFile('w', suffix='.jsonl', delete=False) as f:
        f.write(''.join(json.dumps({'id': t['id'], 'query': t['query']}) + '\n' for t in items))
    p = subprocess.run(CAP + [PACK, root, f.name, '--arms', arms], capture_output=True, text=True, env=ENV)
    os.unlink(f.name)
    if p.returncode != 0:
        raise RuntimeError(p.stderr[-800:])
    hwm = next((int(l.split()[1]) for l in p.stderr.splitlines() if l.startswith('VmHWM:')), None)
    return [json.loads(l) for l in p.stdout.split('\n') if l.strip()], hwm


def parity():
    os.makedirs(RES, exist_ok=True)
    with open(os.path.join(RES, 'parity_p1.tsv'), 'w') as out:
        out.write('id\tresult\tbytes\tused\titems\n')
        for t in tasks():
            outs = [subprocess.run(CAP + [b, 'context', '--json', '--path', t['root'], t['query']],
                                   capture_output=True, env=ENV) for b in (FROZEN, RESEARCH)]
            same = outs[0].returncode == outs[1].returncode == 0 and outs[0].stdout == outs[1].stdout
            used, keys = '', ''
            if outs[0].returncode == 0:
                j = json.loads(outs[0].stdout)
                used = j['used_tokens']
                keys = '|'.join(f"{i['file']}#{i['qualified_name']}:{i['est_tokens']}" for i in j['items'])
            out.write(f"{t['id']}\t{'identical' if same else 'mismatch'}\t{len(outs[0].stdout)}\t{used}\t{keys}\n")
            out.flush()
            print(t['id'], 'identical' if same else 'MISMATCH', flush=True)


def arms():
    os.makedirs(RES, exist_ok=True)
    path = os.path.join(RES, 'packs.jsonl')
    done = {json.loads(l)['id'] for l in open(path)} if os.path.exists(path) else set()
    with open(path, 'a') as out:
        for t in tasks():
            if t['id'] in done:
                continue
            recs, _ = pack(t['root'], [t], 'A,B,TX,T,TC,T4096')
            assert [r['arm'] for r in recs] == ['A', 'B', 'TX', 'T', 'TC', 'T4096'], t['id']
            out.write(''.join(json.dumps(r) + '\n' for r in recs))
            out.flush()
            print(t['id'], ' '.join(f"{r['arm']}:{r['used']}" for r in recs), flush=True)


def rss():
    ts = tasks()
    size = {}
    for t in ts:
        db = sqlite3.connect(f"file:{t['root']}/.oxide/index.db?mode=ro", uri=True)
        size[t['id']] = db.execute('select count(*) from symbols').fetchone()[0]
        db.close()
    top = sorted(ts, key=lambda t: (-size[t['id']], t['id']))[:3]
    with open(os.path.join(RES, 'rss_o1.tsv'), 'w') as out:
        out.write('id\tsymbols\trep\tarms\tvmhwm_kb\n')
        for t in top:
            for rep in range(2):
                for a in (('A', 'A,T') if rep == 0 else ('A,T', 'A')):  # alternating order
                    _, hwm = pack(t['root'], [t], a)
                    out.write(f"{t['id']}\t{size[t['id']]}\t{rep}\t{a}\t{hwm}\n")
                    out.flush()
                    print(t['id'], rep, a, hwm, flush=True)


if __name__ == '__main__':
    {'parity': parity, 'arms': arms, 'rss': rss}[sys.argv[1]]()
