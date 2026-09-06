# Can FTS5 reproduce OXIDE's lexical ranking?

This matters because `CLAUDE.md` forbids changing retrieval scoring without
comparing against `docs/canonical-baseline.md`, and swapping the lexical stage
for FTS5 is a scoring change unless parity is exact.

## Probed capabilities

Probed against the SQLite that OXIDE already ships — `rusqlite` 0.32 with
`features = ["bundled"]`, **SQLite 3.46.0**, no new dependency:

| Capability | Result |
| --- | --- |
| FTS5 virtual tables | available |
| `bm25()` ranking function | available |
| `tokenize='trigram'` | available, and substring `MATCH '"dle_in"'` returns the row |
| Loadable extensions | compiled in, gated at runtime by the authorizer (rusqlite's `load_extension` feature ungates it) |

So **two of the three "enhancements" cost zero new dependencies**. Only the
vector extension is an addition, and `sqlite-vec` is still published as
`0.1.10-alpha.4`.

## The field-weighting question

OXIDE's `LexicalIndex` is weighted-field BM25: `qualified_name` and `name` at
weight 4, `signature` and the path-derived tokens at 2, `references`, `imports`
and the body at 1. FTS5 expresses exactly this shape — one column per field and
per-column weights passed to `bm25(tbl, 4.0, 4.0, 2.0, 2.0, 1.0, 1.0, 1.0)`.

## The tokenizer question (the real one)

OXIDE's tokenizer is not any of FTS5's built-ins. `embeddings::tokenize_into`
splits on non-alphanumeric-or-underscore, then splits identifiers further
(camelCase / snake_case), lowercases, drops a stopword list that includes
language keywords (`fn`, `def`, `const`, `return`, …), and drops tokens shorter
than two characters. `unicode61`, `ascii`, `porter` and `trigram` do none of the
identifier splitting.

Writing a custom FTS5 tokenizer means reaching the `fts5_api` C interface, which
is awkward from `rusqlite`. The cheaper route preserves parity exactly:
**pre-tokenize before insert** — store `tokenize_into(field).join(" ")` in the
FTS column and let `unicode61` split on the spaces it is handed. Same token
stream, same postings, no C.

Two consequences to design around, neither fatal:

1. The FTS column no longer holds original text, so `snippet()`/`highlight()`
   are off the table for that table. OXIDE does not use them.
2. Literal substring search must run against a **separate trigram table over the
   raw body**, since trigrams of pre-tokenized text are meaningless. The gate
   harness is already built this way (`sym_fts` + `sym_tri`).

## What still needs re-baselining

Parity of *inputs* is not parity of *scores*. FTS5's BM25 uses its own k1/b and
its own document-length accounting, and OXIDE's `doc_len` is a sum of field
weights rather than a token count. A port must therefore be run through
`oxide eval --config fixtures/benchmark.json` against
`docs/canonical-baseline.md` and any ordering diff explained, per `CLAUDE.md` —
including tie-break-only diffs.
