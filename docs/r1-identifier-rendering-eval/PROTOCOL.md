# R1 identifier-rendering experiment: PREREGISTERED PROTOCOL

Frozen before any tokenizer measurement, exposure count or embedding.
Its hash goes to `PROTOCOL.sha256`. Nothing below changes after results are seen.

- Baseline: `origin/main` = `024afa5e76d0fc3e4f3ed4f64be1935e0c20ebab`.
- Shipped model/profile:
  - `arctic-embed-xs-q` = fastembed 6.0.2 `SnowflakeArcticEmbedXSQ`;
  - HF `snowflake/snowflake-arctic-embed-xs`, `onnx/model_quantized.onnx`;
  - CLS pooling, L2-normalized, 384-d;
  - query prefix `"Represent this sentence for searching relevant passages: "`, no document prefix.
- Tokenizer:
  - that repo's `tokenizer.json` at HF revision `d8c86521100d3556476a063fc2342036d45c106f`;
  - loaded with HF `tokenizers` 0.22.2, the version in `Cargo.lock`;
  - hashes in `tok/SHA256SUMS`.

## Already known before freezing (disclosure)

- The tokenizer config: BertNormalizer lowercase=True, BertPreTokenizer,
  WordPiece with 30,522 vocab, `##` continuation prefix, max length 512.
- No identifier has been tokenized yet, and no exposure count has been
  computed.
- Gold files and task queries were read earlier (for #32), but not for this
  purpose.

## 1. R1 rendering rule (one rule, frozen)

**Replacement, not dual rendering.** In the document embedding text only, and
only inside the identifier-bearing fields, insert one ASCII space at every
camelCase/PascalCase boundary. The fields are `qualified_name`, `signature`,
each `imports` entry and each `references` entry. Boundaries:

- `(?<=[a-z0-9])(?=[A-Z])`: lower-case letter or digit followed by an upper-case letter;
- `(?<=[A-Z])(?=[A-Z][a-z])`: the end of an upper-case run followed by Upper+lower.

Implementation:

```python
re.sub(r'(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])', ' ', field)
```

Examples:

- `ConnectionPool` → `Connection Pool`
- `HTTPTransport` → `HTTP Transport`
- `safeParseAsync` → `safe Parse Async`
- `Base64Encoder` → `Base64 Encoder`
- unchanged: `utf8`, `snake_case`, `__init__`, `HTTP_PROXY`, `a.b/c`

What stays unchanged:

- Underscores, slashes, dots, `::`, digits-before-letters and every other
  character stay as they are. No characters are removed, and case is not
  changed.
- `file` and `kind` are not rewritten. The path keeps its slashes, dots and
  any camelCase segment.
- The query text and prefix, the model, pooling, normalization and dimension
  are untouched, as are lexical/BM25, RRF, the allocator and structural
  expansion.

## 2. Exposure definition (mechanical, frozen)

- **Identifier tokens** of a gold key: the maximal `[A-Za-z0-9_]+` runs of its
  `qualified_name` (the text after `#`). Module keys (`…:__module__`) have none.
- A token is **split-affected** if the §1 rule inserts at least one space
  into it.
- **Components** of a split-affected token: the pieces after the §1 split,
  each with `_` stripped at both ends and lowercased. Only purely alphabetic
  pieces of length ≥ 3 count.
- **Query words:** the lowercased maximal `[A-Za-z]+` runs of the raw query.
  The query is **not** camel-split, so a merged `ConnectionPool` in the query
  does not count as containing `connection`.
- **Exposed task:** some gold key has a split-affected token with some
  component equal to a query word.
- The definition reads gold keys and the query only. No manual judgment is
  involved.

Other strata (mechanical):

- **ident** (identifier-oriented): some gold key's simple name (the last
  `.`/`::` component of its qualified name, length ≥ 3, not a module) appears
  as a whole word in the query, case-insensitive, at `[A-Za-z0-9_]` word
  boundaries.
- **desc**: not ident.

Counts are reported for dev, held-out and ContextBench. Masked is excluded
and is never used to rescue R1. For ContextBench the count is "not
computable" if its gold is unavailable.

## 3. Phase 0: mechanism screen (tokenizer only; stop rules)

**Exposed identifier sample:** the distinct split-affected tokens of gold keys
in exposed tasks, pooled over dev and held-out. For each, compare
baseline = tokenize(token) with R1 = tokenize(split(token)), counting pieces
without `[CLS]`/`[SEP]`:

- continuation pieces (starting with `##`), summed over the sample;
- total pieces;
- component recoverability: the share of components that appear as a
  standalone word-start piece exactly equal to the component (lowercased);
- document token-length delta.
  - The full document text needs the index (signature, imports, references),
    which is not available in the frozen dumps.
  - The proxy used here: the tokenized `qualified_name` of every candidate in
    the held-out dumps' fused top-50, R1 vs baseline. It is labeled a proxy.
  - The full-text delta is measured in Phase 1 before any quality metric.

**Stop rules** (either one ⇒ **REJECT**, no embeddings):

1. The relative reduction in summed continuation pieces over the exposed
   identifier sample is below 25 %: 1 − Σ R1 / Σ baseline < 0.25.
2. Fewer than 15 held-out tasks are exposed. Held-out is
   `docs/ranking-fusion-eval/results/heldout-clean.jsonl` (65). The
   parent-commit corpora from #30 cover 63 of these; if the task-level
   mapping is available, the count is also reported on those 63, and the
   stricter of the two counts applies.

Also reported (descriptive): the same statistics on all distinct
split-affected tokens in held-out fused top-50 qualified names.

## 4. Phase 1+: quality evaluation

Runs only if Phase 0 passes, in this order: dev → freeze → held-out → ContextBench.

- **Corpora:**
  - held-out: the parent-commit corpora (#30), with frozen gold;
  - dev: commit-disjoint dev tasks at their corpora;
  - ContextBench: the 21 instances at their base commits, with corrected
    line gold.
  - Goldens are not regenerated. Invalid gold, corpus mismatches, duplicate
    tasks and leakage-sensitive tasks are flagged, and results are reported
    with and without the known-invalid tasks.
- **Arms:** baseline D0 = production `symbol_embed_text`, and R1 = §1 applied
  to it. The same production embedding path, re-embed on index copies and
  production search/context are used for both. The D0 arm must reproduce the
  stored vectors bit-for-bit.
- **Metrics:**
  - semantic R@16/50/200;
  - median and mean gold semantic rank;
  - fused nDCG@10;
  - fused gold rank;
  - final-pack coverage (secondary).
  - All are per stratum (exposed, ident, desc, all), with paired task
    bootstrap (2000 resamples, seed 32, percentile 95 % CI).
- **Held-out gates:**
  - **Semantic:** exposed stratum Δ R@50 ≥ +0.05, CI lower bound > 0.
  - **Fused:** exposed stratum Δ nDCG@10 ≥ +0.02.
  - **Identifier regression:** ident stratum Δ nDCG@10 ≥ −0.02 and CI lower
    bound ≥ −0.05.
  - **Cost:**
    - mean document token length +≤ 5 %;
    - embedding storage +≤ 1 %;
    - no material indexing-latency regression without a matching quality gain.
- **Dev:** used only to verify the harness and to report results. Dev can
  REJECT (if every gate fails badly) but cannot tune anything: the rule,
  exposure and gates are fixed here.
- **ContextBench:** it may reject. It contradicts R1 if the exposed (or, if
  that has fewer than 5 tasks, all-task) Δ semantic R@50 or Δ nDCG@10 is
  negative with a CI upper bound < 0.

## 5. Decision (frozen)

- **REJECT**, if any of these holds:
  - Phase 0 fails;
  - the held-out semantic or fused gate fails;
  - the identifier gate fails;
  - a cost gate fails;
  - ContextBench contradicts.
- **ACCEPT**, only if all of these hold:
  - Phase 0 passes;
  - all held-out gates pass;
  - ContextBench was evaluated and does not contradict;
  - the cost gates pass.
- **INSUFFICIENT EVIDENCE**, if:
  - the experiment is underpowered (held-out exposed stratum < 15 tasks at
    evaluation); or
  - the corpora needed for Phase 1+ cannot be obtained; or
  - held-out passes but ContextBench cannot be evaluated.

## 6. Fingerprint consequence (modeled, not implemented)

An accepted R1 changes `symbol_embed_text`, and so creates a new embedding
space:

- `embedding_input_hash` changes for almost every symbol with camelCase, and
  the cache/migration keys follow;
- the provider fingerprint's `document_profile` must change, so that old and
  new vectors cannot coexist silently;
- `EXTRACTION_VERSION` is **not** a substitute.

No production fingerprint changes in this experiment.
