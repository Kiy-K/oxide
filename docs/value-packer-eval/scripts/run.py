"""Value packer (dev, descriptive): #42's harness and tasks, arms A,B,P,PND,PKD.

  run.py arms -> results/packs.jsonl
Binary: frozen 96abae8 + docs/value-packer-eval/research.patch (#42's patch plus the P arms), built in
~/.cache/oxide-valuepack-eval/target. Tasks and indexes: #42's 149 (~/.cache/oxide-jev-eval).
"""
import json, os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', '..', 'treeselect-eval', 'scripts'))
sys.dont_write_bytecode = True
import run as r42  # noqa: E402

T = os.path.expanduser('~/.cache/oxide-valuepack-eval/target/release')
r42.RES = os.path.join(HERE, '..', 'results')
r42.PACK = f'{T}/examples/treeselect_pack'
ARMS = ['A', 'B', 'P', 'PND', 'PKD']


def arms():
    os.makedirs(r42.RES, exist_ok=True)
    path = os.path.join(r42.RES, 'packs.jsonl')
    done = {json.loads(l)['id'] for l in open(path)} if os.path.exists(path) else set()
    with open(path, 'a') as out:
        for t in r42.tasks():
            if t['id'] in done:
                continue
            recs, _ = r42.pack(t['root'], [t], ','.join(ARMS))
            assert [r['arm'] for r in recs] == ARMS, t['id']
            out.write(''.join(json.dumps(r) + '\n' for r in recs))
            out.flush()
            print(t['id'], ' '.join(f"{r['arm']}:{r['used']}" for r in recs), flush=True)


if __name__ == '__main__':
    {'arms': arms}[sys.argv[1]]()
