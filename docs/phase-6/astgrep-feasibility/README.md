# ast-grep feasibility and structural search

Status: **PROMOTE** (preregistered rule, all six criteria pass), 2026-10-18. The
margins on agent need (C1) are thin and the replay shows ast-grep complementing grep,
not replacing it. The integration is the narrowest one the rule allows:
- an explicit, read-only `Repository.structural_search`;
- shipped as the optional `structural` extra;
- no CLI or MCP tool;
- no change to retrieval, routing, ranking or packing.

Measured unless marked *inferred*. [preregistration.md](preregistration.md) was
frozen in `a010f39` before any held-out or corpus measurement (hashes in
`results/freeze.txt`); [research.md](research.md) is the desk research before it.

## Question

Does `ast-grep-py` 0.51.0 add structural-search value beyond OXIDE's entities,
precomputed relations (`calls`, heritage `references`, `imports`, `tested_by`) and
lexical search? Callers and implementors are already precomputed relations
(`docs/precomputed-relations-migration/`) and were out of scope. Only **shape**
queries were evaluated: arguments, keyword arguments, `except` bodies, "not inside a
`try`", "inside a loop".

## Verdict by criterion

| | Criterion | Measured | |
|---|---|---|---|
| C1 | Agent need | held-out `syntactic` searches 63/452 = **13.9%** (≥ 10%); in 52/185 = **28.1%** of searching runs (≥ 25%); replay **helpful 16/60 = 26.7%** (≥ 25%) | pass, thin |
| C2 | Unique precision | 5 of 8 informative non-control queries have regex F1 ≤ 0.90 or no regex | pass |
| C3 | Correctness | ast-grep vs tree-sitter Jaccard **1.000** on all 11 informative queries; every audited regex disagreement was a regex error | pass |
| C4 | Necessity | pure tree-sitter queries express 7/11 = 64% of informative queries (< 90%) | pass |
| C5 | Cost | VS Code: 8.24 s for 8 rules (1.03 s/rule), peak RSS +27.6 MiB over tree-sitter; 0 crashes/exceptions; pathological ≤ 5.1 s; installs on 3.12/3.13/3.14 Linux; PyPI wheels for every supported target | pass |
| C6 | Identity | 0 byte-span mismatches (offline and on OXIDE's retained source) | pass |

## Study A: what agents search for

All 777 `rg` patterns in the 322 Codex runs of the agent-navigation experiment were
extracted (`scripts/agent_searches.py`). `scripts/classify.py` classified them; it was
written on the dev split only.

| class | dev (325) | held-out (452) | who answers it today |
|---|---|---|---|
| name | 120 | 154 | entity names, lexical search |
| definition (`def X`, `class X`) | 98 | 141 | entities |
| call (`X\(`) | 12 | 35 | `calls` relations |
| import | 0 | 4 | `imports` relations |
| cooccur (`a.*b`) | 41 | 49 | — (mostly prose co-occurrence) |
| **syntactic** | 43 | **63** | nothing in OXIDE |
| text | 11 | 6 | lexical / grep |

Over 70% of agent searches are names, definitions or calls, which OXIDE's entities
and relations already cover (`results/searches-classified.jsonl`).

Classifier audit (`results/classifier_audit.json`):
- Of the 63 held-out `syntactic` searches:
  - 46 are real shape intent (`method=`, `context=`, `keep_attrs=True`,
    `template =`, `except ValueError`, `return NotImplemented`, `OPTIONS[...]`);
  - 14 copy a known code line to locate it (`tk / np.sqrt`), where regex is exact by
    construction;
  - 3 are misclassified (SQL text, a definition with a signature).
- In a seeded sample of 40 others, 37 agree; three `cooccur` searches arguably had
  call-shape intent.

Audited shape intent is 46/452 = 10.2%.

**Replay** (`scripts/replay.py`, `results/replay*.json`). For 60 of the 61 distinct
held-out syntactic searches (seeded sample), I wrote an ast-grep rule for the whole
search from the pattern alone (`results/replay_rules.py`). I then ran both rg and
ast-grep on the task's exact source. Audit (`results/replay_audit.json`):

| label | n | what happened |
|---|---|---|
| helpful | 16 | drops regex false positives: `\.loc` hits `.lock`/`.locks`, `loc\[` hits `.iloc[`, `5\.2` hits float literals, `NotImplemented` hits `NotImplementedError`, `def as_sql` hits `as_sqlite`. Adds what rg missed: typed defaults `method: str = None`, multi-line `.sel(` calls, `since="5.1"` against a single-quoted regex |
| harmful | 18 | drops relevant hits: `_get_keep_attrs` for `keep_attrs`, `class _LocIndexer` for `LocIndexer`, `def apply_ufunc(` / `def as_sql(` for call-shaped rules, `def test_where_attrs`, the `keep_attrs = lambda attrs, context:` line that is likely the bug site; one over-broad rule adds 66 unrelated `.where` |
| neutral | 26 | identical or empty |

One helpful case (#33) is the agent's unescaped `%(distinct)s` regex, not syntax;
without it the rate is 15/60 = 25.0%. Every harmful case comes from the same
property: a rule is exact about names and syntactic roles, while grep's substring
matching often catches neighbours an agent needs. **ast-grep is a complement to grep,
not a replacement.** It's useful when the regex's false positives are the problem.

## Study B: precision on 16 shape queries, 10 corpora

`scripts/queries.py` defines each query in three arms: an ast-grep rule, a tree-sitter
query on OXIDE's pinned grammars, and the regex an agent would plausibly write. Totals
over requests, flask, pytest, xarray and seaborn (Python), and code-server,
darkreader, vue core, MUI and VS Code (TypeScript), from `results/structural/*.json`:

| query | ast-grep | tree-sitter | Jaccard | regex P | regex R | regex F1 |
|---|---|---|---|---|---|---|
| ts-empty-catch | 385 | 385 | 1.000 | 0.979 | 0.244 | **0.391** |
| ts-json-parse-unguarded | 252 | 252 | 1.000 | 0.589 | 1.000 | **0.741** |
| ts-non-null-member | 3,374 | 3,374 | 1.000 | 0.995 | 0.998 | 0.997 |
| ts-await-in-loop | 629 | 629 | 1.000 | — | — | **no regex** |
| ts-as-any (control) | 1,027 | 1,027 | 1.000 | 0.975 | 1.000 | 0.987 |
| ts-console-log (control) | 323 | 323 | 1.000 | 0.601 | 1.000 | 0.751 |
| ts-async-promise-executor (control) | **0** | 6 | 0.000 | — | — | — |
| ts-useeffect-empty-deps | 0 | 0 | — | 0.0 (100 hits) | — | — |
| py-except-pass | 99 | 99 | 1.000 | 0.832 | 0.899 | **0.864** |
| py-isinstance-tuple | 108 | 108 | 1.000 | 0.990 | 0.963 | 0.977 |
| py-mutable-default | 17 | 17 | 1.000 | 0.700 | 0.471 | **0.563** |
| py-open-no-encoding | 90 | 90 | 1.000 | 0.849 | 1.000 | 0.918 |
| py-raise-from-none (control) | 20 | 20 | 1.000 | 1.000 | 0.300 | 0.462 |
| py-bare-except (control) | 0 | 0 | — | 0.0 (6 hits) | — | — |
| py-eq-none | 0 | 0 | — | — | — | — |
| py-return-in-finally | 0 | 0 | — | — | — | — |

Audit of every disagreement sample:
- Every regex "false" or "missed" hit was a regex error:
  - matches in comments, strings and test-source strings;
  - multi-line constructs;
  - `pseudofixturedef = {}` matched as `def …= {`;
  - `(Iterable, ) + x` taken for a tuple.
- Three AST-side failures, all in non-informative queries and all query-authoring
  pitfalls a structural tool does not prevent:
  - **`new Promise($E)` misses `new Promise<T>(async …)`.** ast-grep patterns are
    literal about the syntax they spell. All 6 real cases had type arguments; the
    field-based tree-sitter query found them.
  - **Comments are named children.** In `except:  # comment` the comment precedes the
    block, so "block first" misses it. Both AST arms missed all 6 real bare
    `except:`, and the regex was right.
  - **`useEffect($F, [])` misses `React.useEffect(...)`**, the form MUI uses.
    Separately, relational `has` also visits the unnamed `{`/`}` tokens; the
    fixtures caught that before the freeze.
- C4: pure tree-sitter queries need no Python for 7 of the 11 informative queries.
  The 4 that need it are "not inside", "inside before a function boundary",
  "has no keyword argument named X" and "only comments". ast-grep expresses all four
  declaratively, which a tool caller can send as data.

## Study C: cost and robustness

Each run was a fresh process in a scope capped at 200% CPU and 3 GiB, on Linux
x86_64, CPython 3.12.15.

| corpus | files | MB | ast-grep parse+query, 8 rules | tree-sitter (+Python predicates) |
|---|---|---|---|---|
| requests | 35 | 0.35 | 0.07 s / 45 MiB | 0.10 s / 41 MiB |
| xarray | 135 | 2.56 | 0.52 s / 70 MiB | 0.77 s / 61 MiB |
| vue core | 464 | 3.43 | 0.80 s / 56 MiB | 1.15 s / 55 MiB |
| MUI | 2,905 | 6.56 | 1.50 s / 70 MiB | 1.74 s / 63 MiB |
| VS Code | 3,902 | 41.5 | 8.24 s / 200 MiB | 11.80 s / 173 MiB |

- **Integrated API** (`Repository.structural_search`, limit 1000, entity mapping
  included): 0.35–0.40 s per rule on xarray and 0.49–0.81 s on vue core, including
  460 matches.
- **Malformed source** (`results/robustness/malformed-*.json`): three damaged
  variants of every file of requests (35) and darkreader (210):
  - 0 exceptions and 0 crashes;
  - 0 matches lost before the damage point, in either arm.

  Matches inside damaged regions are silently absent. That's why
  `StructuralResult.partial` names those files, from the index's own parse coverage.
- **Pathological input** (`results/robustness/pathological.json`), each case in a
  subprocess:
  - ast-grep stays ≤ 5.1 s and ≤ 328 MiB on 100k-deep nesting, a 100k-link method
    chain, and 100k-line files;
  - the tree-sitter arm **timed out (> 60 s) on the 100k-link chain**.
- **Identity** (`results/robustness/mapping-*.json`): on retained source of indexed
  requests and darkreader, 0 byte mismatches after the character→byte conversion.
  - requests: 26 of 29 matches map to a symbol;
  - darkreader: 20 of 32.
- **Packaging** (`results/packaging.txt`, `results/pypi_matrix.json`):
  - It installs alongside OXIDE and passes the self-test on CPython 3.12.15, 3.13.16
    and 3.14.8 on Linux x86_64; import takes 6–10 ms.
  - It adds **46.2 MB to a 93.6 MB install (+49%)**; the wheel is 5.66 MB.
  - PyPI has cp312–cp314 wheels for manylinux x86_64, macOS x86_64, macOS arm64 and
    win_amd64 (metadata only).
  - **macOS and Windows were not run.** Only Linux results exist. MIT-licensed.

## Integration (promoted)

- `Repository.structural_search(rule, *, language, paths=(), limit=100) ->
  StructuralResult`. The code is `src/oxide/core/structural.py`; the contract is
  `docs/public-api.md` § Structural search.
- `rule` is an ast-grep pattern string or a rule config mapping (relational and
  negated rules are the part grep and pure tree-sitter queries can't express).
  `language` is `python` or `typescript` (`.tsx` uses the TSX grammar).
- It runs over the **retained source of the published generation** through
  `View.source`, which is digest-checked, never the worktree. It converts ast-grep's
  character offsets to byte `SourceRef`s and maps each match to its innermost
  declaration with `View.innermost`.
- It returns path-then-source-order matches, `truncated`, `files`, `partial` and
  `skipped` (non-UTF-8).
- Bounds come before the call: `paths` scope and `limit ≤ 1000`. ast-grep has no
  timeout and holds the GIL.
- A rule is validated against every grammar of the language before any source is
  read. Invalid rules raise `InvalidArgument`; a missing extra raises
  `native_unavailable`.
- The optional `structural` extra pins `ast-grep-py==0.51.0`; the dev group pins it
  too, so `mise run verify` tests it. Default installs and their behaviour are
  unchanged.
- **Not done, by design:**
  - no CLI or MCP tool. The agent-navigation runs never called OXIDE tools (0/18 on
    dev), and agents already have the ast-grep CLI, agent skill and MCP server for
    shell use;
  - no use in `search`/`build_context`;
  - no rewriting or fixes;
  - no second authoritative parser: indexing still uses OXIDE's tree-sitter
    pipeline, and ast-grep only answers explicit queries.

Structural search is **not** natural-language retrieval. Nothing here measures or
claims any effect on relevance ranking or context quality.

## Supported languages and known limitations

- **Python and TypeScript/TSX only**, the two languages OXIDE indexes. ast-grep
  bundles about 25 grammars, but entity mapping needs OXIDE's index.
- ast-grep's bundled grammars match OXIDE's pins today (`tree-sitter-python` 0.25.0,
  `tree-sitter-typescript` 0.23.2). Upgrading the extra can change them independently,
  so re-run `scripts/queries.py` and Study B before bumping the pin.
- These limits are inherent to rules, not bugs (measured above):
  - pattern literalness (generics);
  - comments as named nodes;
  - unnamed tokens in `has`;
  - exact-name rules miss the substring neighbours grep finds.
- Matches inside syntax-error regions are absent; `partial` flags those files.
- Non-UTF-8 files are skipped and reported.

## Future languages (JavaScript, Java, Go, C, C++)

ast-grep would **not** simplify adding languages (Study D, `results/pypi_matrix.json`):

- **Size.** OXIDE's route is one `tree-sitter-<lang>` wheel per language: JavaScript
  0.25.0 (100 KB), Java 0.23.5 (84 KB), Go 0.25.0 (66 KB), C 0.24.2 (94 KB), C++ 0.23.4
  (316 KB). All are abi3 with wheels for every supported target, MIT-licensed, and the
  same versions ast-grep 0.51.0 bundles. ast-grep is 5.66 MB of wheel and 46 MB
  installed with no per-language opt-in.
- **Control.** The pins would be ast-grep's release cadence (three releases in three
  months), not OXIDE's derivation versioning.
- **Fit** *(inferred)*. OXIDE's extraction is one compiled tree-sitter `Query` per
  grammar whose captures feed bounded Python policy. ast-grep-py exposes no
  tree-sitter `Query` and no byte offsets, so extraction on it would mean Python tree
  walks (which AGENTS.md rules out for latency-sensitive work) or a second parse per
  file.

Structural search gets each new language for free once OXIDE indexes it, provided
ast-grep bundles that grammar (it does for all five).

## Deviations from the preregistration

1. **Non-Python files in the replay.** `replay.py` parsed explicitly named
   non-Python files (`CHANGES.rst`) as Python, because `rg --files -t py` lists
   explicit paths whatever their type. I restricted it to `.py` files and reran it
   after the first run. Searches #17 and #18 changed from "AST-only hits" to
   identical.
2. **Replay rules encode the whole search.** Each rule encodes every branch, not only
   its syntactic ones (names become identifiers, text becomes string literals). The
   frozen wording ("the pattern's evident syntactic intent") allowed this. Comparing
   only the syntactic branches would have counted rg's other branches as noise.
3. **The helpful/harmful audit is mine alone** (single rater, not blinded to which arm
   produced a hit). Reasons are recorded per search.

## Other findings

- **OXIDE's Python parser is super-linear on long method chains**, independent of
  ast-grep. `oxide.core.python.parse` takes 3.0 s for 10k links, 29.6 s for 30k and
  more than 120 s for 100k. A file under the 1 MiB capture limit (about 130k
  `.then(f)` links) can therefore stall `oxide index` for minutes. The cause is not
  diagnosed; the same pattern timed out the tree-sitter arm here. This needs a fix
  before release *(not done here: out of scope)*.

## What this can't establish

- task success;
- TypeScript agent behaviour (the transcripts are Python only);
- whether agents author correct rules: the replay rules are mine, and Study B shows
  real authoring pitfalls;
- macOS and Windows behaviour.

## Recommended next milestone

1. Fix the super-linear parse on long call chains above, a release blocker for robust
   indexing.
2. Before any CLI/MCP exposure, run a small preregistered agent trial: a coding agent
   gets `oxide_structural` next to grep on tasks whose evidence needs a shape query.
   Measure use, rule-authoring errors and task outcomes. If agents don't call it, or
   their rules hit the pitfalls above more often than grep misleads them, keep it
   API-only.

## Reproduce

Scratch environment outside the repository: `uv venv --python 3.12` with
`ast-grep-py==0.51.0` and `uv pip install -e .` of this checkout. Corpora are the
ContextBench caches (`mise run bootstrap`); transcripts are
`~/Projects/oxide-eval-data/oxide-agentnav/runs` (`$RUNS`); `$E` is a scratch work
directory.

```bash
cd docs/phase-6/astgrep-feasibility
python scripts/queries.py                                   # fixture self-test
python scripts/agent_searches.py $RUNS ../../agent-navigation-eval/tasks.json $E/searches.jsonl
python scripts/classify.py $E/searches.jsonl results/searches-classified.jsonl
python results/replay_rules.py results/replay_keys.json results/replay_rules.json
python scripts/replay.py results/replay_rules.json ../../agent-navigation-eval/tasks.json $E results/replay.json
for c in requests flask pytest xarray seaborn code-server darkreader vue-core mui vscode; do
  python scripts/structural_eval.py $c results/structural                 # all arms
  python scripts/structural_eval.py $c results/structural --arms astgrep  # cost, fresh process
  python scripts/structural_eval.py $c results/structural --arms treesitter
done
python scripts/malformed.py requests results/robustness   # and darkreader
python scripts/pathological.py results/robustness
python scripts/mapping.py requests $E/mapdata results/robustness   # and darkreader
bash scripts/packaging.sh $E results/packaging.txt
python scripts/pypi_matrix.py results/pypi_matrix.json
```

Run every heavy step under `systemd-run --user --scope -p MemoryMax=3G -p
MemorySwapMax=0 -p CPUQuota=200%`, as all recorded runs were.
