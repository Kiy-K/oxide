"""#40 V5 parity and G5 pack arms (PROTOCOL §4, §5 V5). Research binary = frozen + research.patch.

  packs.py parity  -> results/parity_v5.tsv   (before any Jev call)
      Every frozen task: `oxide context --json` from the unpatched frozen binary and from the
      research binary (override off) must be byte-identical.
  packs.py arms    -> results/packs.jsonl     (after scoring)
      jev_pack arm A (tracing on, no override) and arm B per order scorer X in S, primary,
      in_scope, implementation. Arm-B order: the view files' fused top-50 rows (`cand`) by
      (file rank under X+F, ties by best fused rank; then fr). X+F comes from eval_40.load().
"""
import json, os, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
RES = os.path.join(HERE, '..', 'results')
J = os.path.expanduser('~/.cache/oxide-jev-eval')
FROZEN = f'{J}/target-frozen/release/oxide'
RESEARCH = f'{J}/target-research/release/oxide'
PACK = f'{J}/target-research/release/examples/jev_pack'
LABELS = ('S', 'primary', 'in_scope', 'implementation')
sys.dont_write_bytecode = True
sys.path.insert(0, HERE)
from build import CAP, ENV  # noqa: E402


def tasks():
    return {t['id']: t for s in ('heldout', 'cb') for t in map(json.loads, open(f'{J}/tasks/{s}-frozen.jsonl'))}


def parity():
    os.makedirs(RES, exist_ok=True)
    with open(os.path.join(RES, 'parity_v5.tsv'), 'w') as out:
        out.write('id\tresult\tbytes\n')
        for t in tasks().values():
            outs = [subprocess.run(CAP + [b, 'context', '--json', '--path', t['root'], t['query']],
                                   capture_output=True, env=ENV) for b in (FROZEN, RESEARCH)]
            same = outs[0].returncode == outs[1].returncode == 0 and outs[0].stdout == outs[1].stdout
            out.write(f"{t['id']}\t{'identical' if same else 'mismatch'}\t{len(outs[0].stdout)}\n")
            out.flush()
            print(t['id'], 'identical' if same else 'MISMATCH', flush=True)


def order(rows, cand, x):
    """Arm-B keys for scorer x: files by X+F (ties: best fused rank), then rows by fr."""
    rank = {r['key']: i for i, r in enumerate(sorted(rows, key=lambda r: (-r['s'][x + '+F'], r['fr'])))}
    return [c['key'] for c in sorted(cand, key=lambda c: (rank[c['path']], c['fr']))]


def pack(root, lines, arm):
    with tempfile.NamedTemporaryFile('w', suffix='.jsonl', delete=False) as f:
        f.write(''.join(json.dumps(l) + '\n' for l in lines))
    p = subprocess.run(CAP + [PACK, root, f.name, '--arms', arm], capture_output=True, text=True, env=ENV)
    os.unlink(f.name)
    if p.returncode != 0:
        raise RuntimeError(p.stderr[-500:])
    return [json.loads(l) for l in p.stdout.split('\n') if l.strip()]  # not splitlines(): see build.fused_dump


def arms():
    from eval_40 import load
    src = tasks()
    _, kept, _, _ = load()
    with open(os.path.join(RES, 'packs.jsonl'), 'w') as out:
        for t in kept:
            q, cand = src[t['id']]['query'], src[t['id']]['cand']
            recs = pack(t['root'], [{'id': t['id'], 'query': q, 'order': None}], 'a')
            recs += pack(t['root'], [{'id': t['id'], 'query': q, 'order': order(t['rows'], cand, x)}
                                     for x in LABELS], 'b')
            for r, label in zip(recs, ('a', *(f'b:{x}' for x in LABELS))):
                out.write(json.dumps({**r, 'label': label}) + '\n')
            out.flush()
            print('packed', t['id'], flush=True)


if __name__ == '__main__':
    {'parity': parity, 'arms': arms}[sys.argv[1]]()
