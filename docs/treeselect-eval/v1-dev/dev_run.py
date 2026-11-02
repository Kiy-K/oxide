"""#42 → TreeSelect v1 DIAGNOSIS on the 149 #42 tasks, used as a development set only.

  dev_run.py [round2] -> ~/.cache/oxide-treeselect-eval/dev/packs[-round2].jsonl  (resumable)

Arms are treeselect_pack specs (see research.patch's examples/treeselect_pack.rs `parse_arm`).
"""
import json, os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'scripts'))
sys.dont_write_bytecode = True
import run  # noqa: E402  (same CAP/ENV/tasks/pack as the #42 runner)

OUT = os.path.expanduser('~/.cache/oxide-treeselect-eval/dev/packs.jsonl')
V0 = ['B', 'TX', 'T', 'TC', 'T4096']
GRID = [f'TX:flat:nocollapse:{s}{p}:d{d}' for s in ('sqrt', 'mean', 'max', 'lin')
        for p in ('', ':twopass') for d in (16, 32, 50)]
FLAT = [f'B{p}:d{d}' for p in ('', ':twopass') for d in (16, 32, 50)]
ARMS = ['A'] + V0 + FLAT + GRID
# Round 2 (after round 1 refuted count bias and pool depth): keep production's role order and use
# the tree only within a role.
ROUND2 = ['A', 'B', 'TX', 'B:twopass', 'TX:flat:nocollapse:rolefirst', 'TX:flat:nocollapse:max:rolefirst',
          'TX:flat:nocollapse:mean:rolefirst', 'TX:rolefirst', 'TX:flat:nocollapse:rolefirst:twopass']


def main():
    global OUT, ARMS
    if sys.argv[1:] == ['round2']:
        OUT, ARMS = OUT.replace('packs.jsonl', 'packs-round2.jsonl'), ROUND2
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    done = {json.loads(l)['id'] for l in open(OUT)} if os.path.exists(OUT) else set()
    with open(OUT, 'a') as out:
        for t in run.tasks():
            if t['id'] in done:
                continue
            recs, _ = run.pack(t['root'], [t], ','.join(ARMS))
            assert [r['arm'] for r in recs] == ARMS, t['id']
            out.write(''.join(json.dumps(r) + '\n' for r in recs))
            out.flush()
            print(t['id'], len(recs), flush=True)


if __name__ == '__main__':
    main()
