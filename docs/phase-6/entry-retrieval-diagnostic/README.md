# Natural-language entry retrieval: failure taxonomy (diagnostic, 2026-10-17)

Why do natural-language tasks miss their gold at entry? This is a diagnostic milestone:
nothing in `src/` changed, production retrieval (`oxide-retrieval-v2`, LadybugDB FTS over
name, signature and path terms, top 20) stays the default, and no frozen artifact moved.
Only dev tasks were used: ContextBench-py `baseline-v5` dev (15 tasks, 47 verified spans)
and TypeScript ContextBench `typescript-v2` dev (26 tasks, 115 verified spans). The
held-out splits were not opened. Numbers are measured unless marked *inferred*; counts
are spans, and nested gold spans of one task are not independent.

Context: entry is the largest loss. At 4,096 units, 265 of TypeScript's 384 verified
spans are lost at entry, and on vscode dev `entry_recall@20` is 0.044. The router study
next door ([../router-reachability](../router-reachability/README.md)) shows that routing
cannot compensate.

## Method (`scripts/retrieval_probe.py`, `scripts/retrieval_sum.py`)

For every verified gold span, the probe takes the entities that cover it (a symbol
containing one of its code lines, plus its file when the span has module-level lines).
It records their best rank under several scorers, and which query terms they share.

- **`fts`:** production LadybugDB FTS (Porter, default stopwords, disjunctive BM25) on
  the production query, with no top-20 cut.
- **`replica`:** an in-memory BM25 (k1 1.2, b 0.75) over the production documents, using
  `oxide-terms-v1`, a crude symmetric stemmer, a stopword list and distinct query terms.
  It is the control for every challenger below. Calibration on dev: 21 vs `fts` 22 spans
  in the top 20 (py), 32 vs 29 (ts).
- **`body`:** the replica plus each symbol's source body (identifiers, comments, strings).
- **`leaf_body`** (*post hoc*): the same, with bodies only on symbols that contain no
  other symbol.
- **`replica_pruned`, `body_pruned`:** one preset query pruning, not swept: drop
  digit-bearing terms and terms in more than 10 % of documents.
- **File level:** a file's rank by its best entity, and by BM25 over a file document
  (path plus every member's name and signature terms).
- **"Distinctive" term:** a query term in at most 5 % of the corpus files.

`dev-py.json` and `dev-ts.json` are the raw per-span outputs.

## Taxonomy of the spans lost at entry

| Class | py (25 of 47 lost) | ts (86 of 115 lost) |
| --- | ---: | ---: |
| **A. Matched in the indexed fields, ranked below 20** | **25** | **78** |
| … ranked 21–100 | 15 | 19 |
| … ranked 101–1,000 | 9 | 14 |
| … ranked > 1,000 | 1 | 45 |
| … sharing a distinctive query term with the indexed document | 0 | 28 |
| B. Not matched; a distinctive query term only in the body | 0 | 2 |
| C. Not matched; only generic shared terms in the body | 0 | 4 |
| **D. No shared term at all (pure vocabulary mismatch)** | **0** | **2** |

By repository, ts losses are 67 vscode, 10 code-server, 9 darkreader and 0 fixture.

What the A spans look like (dev examples):
- **The queries are raw issue text:** median 55 distinct terms on ts and 16 on py.
  24 of 26 ts queries carry terms absent from the corpus (hashes, versions, template
  boilerplate such as `bugreporttemplate`, `codeofconduct`, `bisect`).
- **Gold matches on topic or path terms shared by thousands of entities:** `vs`,
  `editor`, `common`, `link`, `terminal`, `request`, `plot`. Disjunctive BM25 then sums
  many weak matches, and longer or better-named neighbours outrank the gold.

## Remedies tested (top-20 hits over all verified spans; + / − against `replica`)

| Scorer | py top 20 | py + / − | ts top 20 | ts + / − | vscode top 20 (of 77 dev spans) |
| --- | ---: | --- | ---: | --- | ---: |
| `fts` (production) | 22 | — | 29 | — | 10 |
| `replica` | 21 | — | 32 | — | 14 |
| `body` | **32** | +11 / −0 (4 tasks) | 25 | +5 / −12 | 6 |
| `leaf_body` (*post hoc*) | 24 | +8 / −5 | 27 | +6 / −11 | 6 |
| `replica_pruned` | 19 | +0 / −2 | 25 | +1 / −8 | 10 |
| `body_pruned` | 31 | +11 / −1 | 24 | +6 / −14 | 6 |

Gold files in the top 10, out of the gold files each task has (ts per repository):

| Repository | Gold files | File by best entity | File document |
| --- | ---: | ---: | ---: |
| vscode | 24 | 5 | 4 (top 20: 5) |
| code-server | 6 | 2 | 5 |
| darkreader | 4 | 3 | 3 |
| fixture | 10 | 10 | 10 |

On py, 21 and 23 of 27 gold files are in the top 10, but py repositories are small
(about 30–250 files), so file rank says little there.

Identifiers quoted in the query (camelCase, snake_case, dotted) occur in the indexed
document of 0 py and 4 ts lost spans, and in the body of 8 py and 7 ts lost spans.
These come from 3 tasks per benchmark.

## Answers

1. **Lexical retrieval limitations, or vocabulary mismatch?** The strict mismatch class
   is rare: 0 of 25 py and 2 of 86 ts spans share no term with their query. The
   dominant failure is **weak lexical discrimination**. The terms exist in the
   repository and in the gold's indexed fields, but they are topic terms that hundreds
   or thousands of entities share. A long, noisy issue text then cannot single the gold
   out. On vscode this holds at file level too: only 5 of 24 gold files reach the top 20.
2. **Indexed fields.** The body is a real limitation on small repositories. On py it
   moves 11 of 25 lost spans into the top 20 (4 of 15 tasks) and displaces none, and on
   code-server and darkreader it gains 5. On vscode it hurts (14 → 6), with whole bodies
   or leaf bodies alike. More text adds more shared generic terms, and the discriminating
   ones are diluted (*inferred*).
3. **Code-aware tokenization** is not implicated. `oxide-terms-v1` already splits
   camelCase and acronyms and keeps the joined identifier. Only 8 of 111 lost spans are
   unmatched by the FTS at all, which bounds any tokenization fix.
4. **Query pruning** by document frequency hurts on both benchmarks. The gold's own
   matches are often exactly the frequent topic terms.
5. **Entry-rank gains are not context gains.** The previous body experiment
   ([../../lexical-fields-eval](../../lexical-fields-eval/README.md), Rust/Phase 4, 9
   tasks) saw entry gains that did not survive packing at 1,024 units. Nothing here was
   run through `build_context`.

## Most promising next experiment (not run)

A **preregistered, end-to-end body-field experiment against the real FTS**, which is what
`lexical-fields-eval` named as the condition for reopening.

- **The change:** add a symbol-body field to the LadybugDB lexical index, as an
  experimental derivation that is not promoted. Score with the production FTS, not a
  replica.
- **Measure:** run both dev splits through the frozen scorers. Report `entry_recall@20`,
  `span_recall` and `line_recall` at 256, 1,024 and 4,096 units, displaced gold, index
  size and time, and query latency.
- **Gate:** report per repository, and fail on any repository regression beyond the
  tolerance. vscode's −8 in the replica is the main risk to rule in or out.
- **Coverage:** the dev split has 6 distinct tasks where the body variant lifts lost gold into the top 20 over production `fts` (4 py, 2 ts),
  above the 5-task coverage rule used in `routed-module-selection`. The held-out splits
  are opened once, at the end.

If the body field helps only small repositories, scope the next question to vscode-scale
discrimination: identifying the area before the symbol. That needs evidence beyond bag
of words, and per SPEC the deterministic offline path must stay intact. It is not
proposed until the body experiment is read.

## Reproduce

From the repository root, with both benchmarks' corpora fetched:

    uv run python docs/phase-6/entry-retrieval-diagnostic/scripts/retrieval_probe.py py OUT-py.json
    uv run python docs/phase-6/entry-retrieval-diagnostic/scripts/retrieval_probe.py ts OUT-ts.json
    uv run python docs/phase-6/entry-retrieval-diagnostic/scripts/retrieval_sum.py OUT-py.json OUT-ts.json
