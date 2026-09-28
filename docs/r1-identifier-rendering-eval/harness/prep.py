"""R1 Phase 1 manifests: corpora (name -> repo, full sha) and per-corpus task
files. dev + held-out-HEAD + CB corpora are exactly the committed-dump corpora
(parity (a)); held-out evaluation corpora are each task commit's first parent
(the #30 parent-commit convention)."""
import json, os, subprocess, collections
W = os.path.dirname(os.path.abspath(__file__))
R = '/home/user/oxide/docs/ranking-fusion-eval/results/'
HEADS = json.load(open('/home/user/oxide/docs/retrieval-failure-taxonomy/results/corpora.json'))
SRC = {'pylint': 'pylint', 'pytest': 'pytest', 'zod': 'zod', 'requests': 'requests', 'flask': 'flask',
       'ripgrep': 'ripgrep', 'httpx': 'httpx', 'pylint-dev/pylint': 'pylint', 'psf/requests': 'requests',
       'pytest-dev/pytest': 'pytest', 'darkreader/darkreader': 'darkreader', 'coder/code-server': 'code-server',
       'pallets/flask': 'flask', 'mwaskom/seaborn': 'seaborn'}


def full(repo, rev):
    return subprocess.check_output(['git', '-C', f'{W}/src/{repo}', 'rev-parse', rev + '^{commit}'], text=True).strip()


corpora = {}
tasks = collections.defaultdict(list)
cbg = json.load(open(f'{W}/cb/cb_gold.json'))
for t in map(json.loads, open(R + 'tasks.jsonl')):
    name = next(k for k in HEADS if k.startswith(f"head-{t['repo']}@"))
    corpora[name] = (t['repo'], full(t['repo'], HEADS[name][1]))
    tasks[('dev', name)].append({'id': t['id'], 'query': t['query'], 'gold': t['gold']})
for t in map(json.loads, open(R + 'heldout-clean.jsonl')):
    parent = full(t['repo'], t['commit'] + '^')
    name = f"par-{t['repo']}@{parent[:12]}"
    corpora[name] = (t['repo'], parent)
    tasks[('heldout', name)].append({'id': t['id'], 'query': t['query'], 'gold': t['gold'], 'commit': t['commit']})
    if '/wt/' not in t.get('path', ''):  # held-out tasks whose committed dump used a HEAD corpus
        hn = next(k for k in HEADS if k.startswith(f"head-{t['repo']}@"))
        corpora[hn] = (t['repo'], full(t['repo'], HEADS[hn][1]))
        tasks[('parity_heldout_head', hn)].append({'id': t['id'], 'query': t['query'], 'gold': t['gold']})
for t in map(json.loads, open(R + 'cb-tasks.jsonl')):
    repo = SRC[t['repo']]
    name = f"cb-{repo}@{t['base_commit'][:12]}"
    corpora[name] = (repo, full(repo, t['base_commit']))
    tasks[('cb', name)].append({'id': t['id'], 'query': t['query'], 'gold_lines': cbg[t['id']]['gold_lines']})
os.makedirs(f'{W}/tasks', exist_ok=True)
for (s, c), ts in tasks.items():
    with open(f'{W}/tasks/{s}__{c}.jsonl', 'w') as f:
        for t in ts:
            f.write(json.dumps(t) + '\n')
json.dump(corpora, open(f'{W}/corpora.json', 'w'), indent=1)
print(len(corpora), 'corpora;', collections.Counter(s for s, _ in tasks), 'task files;',
      {s: sum(len(v) for (ss, _), v in tasks.items() if ss == s) for s in {s for s, _ in tasks}})
