"""#43 runner: #42's run.py helpers, arms A,B only, on the fresh tasks.

  run.py parity -> results/parity_p1.tsv   run.py arms -> results/packs.jsonl   run.py rss -> results/rss_o1.tsv
Binaries: #42's archived amended research build (~/.cache/oxide-treeselect-eval/v0-bin/), whose
research.patch is committed as docs/treeselect-eval/research.patch; frozen baseline = #40's target-frozen.
"""
import json, os, sqlite3, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', '..', 'treeselect-eval', 'scripts'))
sys.dont_write_bytecode = True
import run as r42  # noqa: E402

V0 = os.path.expanduser('~/.cache/oxide-treeselect-eval/v0-bin')
r42.J = os.path.expanduser('~/.cache/oxide-capremoval-eval')
r42.RES = os.path.join(HERE, '..', 'results')
r42.RESEARCH, r42.PACK = f'{V0}/oxide', f'{V0}/treeselect_pack'


def arms():
    os.makedirs(r42.RES, exist_ok=True)
    path = os.path.join(r42.RES, 'packs.jsonl')
    done = {json.loads(l)['id'] for l in open(path)} if os.path.exists(path) else set()
    with open(path, 'a') as out:
        for t in r42.tasks():
            if t['id'] in done:
                continue
            recs, _ = r42.pack(t['root'], [t], 'A,B')
            assert [r['arm'] for r in recs] == ['A', 'B'], t['id']
            out.write(''.join(json.dumps(r) + '\n' for r in recs))
            out.flush()
            print(t['id'], ' '.join(f"{r['arm']}:{r['used']}" for r in recs), flush=True)


def rss():
    ts = r42.tasks()
    size = {}
    for t in ts:
        db = sqlite3.connect(f"file:{t['root']}/.oxide/index.db?mode=ro", uri=True)
        size[t['id']] = db.execute('select count(*) from symbols').fetchone()[0]
        db.close()
    top = sorted(ts, key=lambda t: (-size[t['id']], t['id']))[:3]
    with open(os.path.join(r42.RES, 'rss_o1.tsv'), 'w') as out:
        out.write('id\tsymbols\trep\tarms\tvmhwm_kb\n')
        for t in top:
            for rep in range(2):
                for a in (('A', 'A,B') if rep == 0 else ('A,B', 'A')):
                    _, hwm = r42.pack(t['root'], [t], a)
                    out.write(f"{t['id']}\t{size[t['id']]}\t{rep}\t{a}\t{hwm}\n")
                    out.flush()


if __name__ == '__main__':
    {'parity': r42.parity, 'arms': arms, 'rss': rss}[sys.argv[1]]()
