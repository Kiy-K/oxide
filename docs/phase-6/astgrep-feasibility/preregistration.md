# Preregistration: ast-grep feasibility and structural search

Written 2026-10-18 before any corpus measurement, after the desk research in
[research.md](research.md). Frozen by the commit that adds this file. The hashes in
`results/freeze.txt` cover this file and `scripts/`, and scripts that read results
are not changed after the freeze; any deviation is reported as one in the README.

## Question

Does `ast-grep-py` 0.51.0 give OXIDE enough structural-search value, beyond its
indexed entities, precomputed relations (`calls`, heritage `references`, `imports`,
`tested_by`) and lexical search, to justify reintroducing it? Callers and implementors
are out of scope: precomputed relations replaced request-time ast-grep for those
(`docs/precomputed-relations-migration/`). The question is **shape** queries only.
Ordinary natural-language retrieval, ranking and context selection are not touched by
any outcome.

## What was seen before the freeze

- Dev-split agent search patterns (sympy/sphinx tasks), used to write
  `scripts/classify.py`. On dev, `syntactic` was 43 of 325 searches.
- Synthetic fixtures (`scripts/queries.py`) and one dev search (`permute=True`) to
  smoke-test the scripts.
- No held-out pattern, no corpus query result, no timing.

## Studies

### A. Agent need (held-out Codex transcripts)

Data: `oxide-agentnav/runs`, 322 Codex CLI runs (gpt-6-luna) over 28 SWE-bench Verified
Python tasks, `docs/agent-navigation-eval/`. The dev/held-out split is that
experiment's.

1. `agent_searches.py` extracts every `rg`/`grep` pattern. `classify.py` labels each
   search, frozen as written on dev: definition / heritage / import / call / name /
   syntactic / cooccur / text.
2. **Classifier audit:** I hand-label every held-out search classified `syntactic` and
   a seeded sample of 40 others (`random.Random(20261018)`). I report agreement.
3. **Replay:** distinct held-out `syntactic` (task, pattern) pairs, all of them if ≤ 60,
   otherwise a seeded sample of 60.
   - For each I write an ast-grep rule encoding the pattern's evident syntactic intent,
     or `null` if it has none. Rules go into `results/replay_rules.json` before
     `replay.py` runs.
   - `replay.py` runs rg as the agent did and ast-grep over the same Python files of
     the task's exact source.
   - I audit each difference and label the AST result:
     - **helpful**: it drops code hits irrelevant to the intent, or adds a relevant
       hit rg missed, without dropping a relevant hit;
     - **harmful**: it drops a relevant hit, for example a doctest or comment the
       agent wanted;
     - **neutral**: no difference, or `null`.

### B. Precision and coverage (16 fixed shape queries, 10 corpora)

- `scripts/queries.py`: 8 Python and 8 TypeScript shape queries. 5 are controls,
  expected easy for regex.
- Each query has three arms:
  - an ast-grep rule;
  - a tree-sitter query over OXIDE's pinned grammars (11 are pure query language, 5
    need a Python predicate);
  - the regex an agent would plausibly write (2 queries have none).
- Corpora (`structural_eval.py`), each the first cached commit, with the file set
  OXIDE captures:
  - Python: requests, flask, pytest, xarray, seaborn;
  - TypeScript: code-server, darkreader, vue core, MUI, VS Code.
- Measured per query:
  - exact byte-span agreement between ast-grep and tree-sitter (Jaccard);
  - regex precision, recall and F1 against the ast-grep matches (a regex hit is
    correct when its line lies inside an ast-grep match);
  - deterministic samples of every disagreement, all of which I audit.
- An **informative** query has ≥ 5 ast-grep matches across its language's corpora.

### C. Cost and robustness

- **Latency and memory:** each corpus separately per arm (`--arms astgrep`,
  `--arms treesitter`), each in a fresh process under
  `systemd-run --user --scope -p MemoryMax=3G -p MemorySwapMax=0 -p CPUQuota=200%`.
  Reported: parse and query seconds, and peak RSS.
- **Malformed source** (`malformed.py`, requests and darkreader): three damaged
  variants per file. Reported: exceptions, crashes, and matches silently lost before
  the damage point.
- **Pathological input** (`pathological.py`): nesting and chains of 1k/10k/100k, and
  100k-line files. Each case runs in a subprocess with a 60 s timeout. Reported: exit
  code, seconds and RSS.
- **Identity** (`mapping.py`, requests and darkreader indexed by OXIDE): ast-grep runs
  on retained source, character offsets are converted to bytes, and each match is
  mapped with `View.innermost`. Byte mismatches must be 0.
- **Packaging:**
  - `packaging.sh` installs alongside OXIDE on CPython 3.12/3.13/3.14 (Linux x86_64),
    then runs the self-test, sizes and import time;
  - `pypi_matrix.py` checks wheel coverage of the four supported platforms × 3
    Pythons from metadata.
  - No other platform is claimed as validated.

### D. Future languages

`pypi_matrix.py` lists the wheel coverage and size of `tree-sitter-{javascript,java,go,
c,cpp}`, the per-language route OXIDE's current design would take. I compare it with
ast-grep's bundled grammars on dependency size, version control and extraction fit.
The extraction-fit argument is qualitative: the Python API exposes no tree-sitter
`Query`. I label it inferred.

## Criteria (decided now)

| | Criterion | Passes when |
|---|---|---|
| C1 | Agent need | held-out `syntactic` searches are ≥ 10% of held-out searches **and** occur in ≥ 25% of held-out runs that searched, **and** ≥ 25% of replayed searches are audited **helpful** |
| C2 | Unique precision | among informative non-control queries, at least half have regex F1 ≤ 0.90 against ast-grep, or no regex |
| C3 | Correctness | ast-grep vs tree-sitter Jaccard ≥ 0.98 on every informative query, every disagreement audited, and no audited ast-grep error |
| C4 | Necessity over existing dependencies | pure tree-sitter queries (no Python predicate) express < 90% of the informative queries. If they express ≥ 90%, the capability needs no new dependency |
| C5 | Cost | VS Code full scan per TypeScript query ≤ 15 s (ast-grep parse + query time / 8 queries); ast-grep peak RSS on VS Code ≤ 400 MiB above the tree-sitter arm; no crash or exception in malformed or pathological runs; every pathological case ≤ 60 s; installs and self-test pass on 3.12/3.13/3.14 Linux; PyPI wheels for every supported target |
| C6 | Identity | 0 byte-span mismatches in `mapping.py` and `structural_eval.py` (exact agreement with tree-sitter spans) |

## Verdict rules

- **PROMOTE** if C1–C6 all pass. The integration is then the smallest read-only,
  explicitly invoked structural search, separate from natural-language retrieval.
- **REJECT** (keep the architecture, document the negative result) if any of these
  holds:
  - C1 fails (no measured agent need);
  - C2 fails (regex already gives the evidence);
  - C4 fails (the capability needs no new dependency; the follow-up would be
    tree-sitter-query structural search, recorded separately);
  - C5 fails.
- **INCONCLUSIVE** if no REJECT condition holds but either:
  - C3 or C6 fails, or
  - there are fewer than 20 held-out `syntactic` searches.

What this can't establish:

- task-success effects (no agent runs here);
- TypeScript agent behaviour (the transcripts are Python only);
- agents' ability to *author* correct ast-grep rules.

Pattern ergonomics alone can't justify PROMOTE.
