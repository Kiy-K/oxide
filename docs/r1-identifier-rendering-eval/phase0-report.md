# R1 identifier-rendering experiment (final, after independent review)

Status: research only. Production is unchanged; nothing committed or pushed;
no GitHub issue edited; no fingerprint changed. Every number is **measured**
by `phase0.py` unless marked *inferred*. Raw output: `results/phase0.json`.

## Summary

**Disposition: INSUFFICIENT EVIDENCE.**

- **Phase 0 (mechanism) passes clearly.**
  - Continuation-piece fragmentation of exposed identifiers falls by 69.6 %
    (the stop rule was 25 %).
  - 22 held-out tasks are exposed (the stop rule was 15).
- **Phase 1+ (dev, held-out, ContextBench quality and cost) could not run.**
  - It needs the third-party corpora at their parent commits, to re-embed
    real `symbol_embed_text` for D0 and R1.
  - GitHub is blocked in this Cloud environment: HTTP 403 from the network
    policy, and the corpus clone was refused by the session's permission
    policy.
  - Under PROTOCOL §5, missing Phase 1+ corpora gives INSUFFICIENT EVIDENCE.
- No quality claim is made. The mechanism being present does not show a
  retrieval gain.

## 1. Baseline

`origin/main` = `024afa5e76d0fc3e4f3ed4f64be1935e0c20ebab`.

## 2. Tokenizer / model revision

- **Shipped profile:** `arctic-embed-xs-q` → fastembed 6.0.2
  `SnowflakeArcticEmbedXSQ` → HF `snowflake/snowflake-arctic-embed-xs`,
  `onnx/model_quantized.onnx`.
  - Source: `src/embeddings/native/profiles.rs:58`, and fastembed
    `src/models/text_embedding.rs:455`, read from the crates.io 6.0.2 tarball.
- **Tokenizer:** `tokenizer.json` at HF revision
  `d8c86521100d3556476a063fc2342036d45c106f` (last modified 2024-12-13).
  - Loaded with HF `tokenizers` **0.22.2**, the version in `Cargo.lock`.
  - Hashes in `tok/SHA256SUMS`; `tokenizer.json` sha256 `91f1def9…1854`.
  - Configuration: BertNormalizer with **lowercase = True**, BertPreTokenizer,
    WordPiece with a 30,522-token vocabulary and `##` continuation pieces,
    max length 512.
- **Caveat:** production does not pin a model revision.
  `EmbeddingSpaceFingerprint.artifact_revision` is empty and fastembed
  resolves `main`. Revision `d8c86521` is HF `main` today *(inferred to be
  the one production downloads)*.

## 3. Preregistered protocol

- `PROTOCOL.md`, sha256 `c88b49f1a95c592f8e1e0b0026010d4489ee6eecd8e2d380d9f39f2d68b27d83`.
- Frozen 2026-09-28T09:35:56Z, before any identifier was tokenized or any
  exposure counted.
- The only evidence of freeze order is file mtimes: `PROTOCOL.md` 09:35:47,
  hash 09:35:56, `phase0.py` 09:36:21, first results 09:36:32.

## 4. R1 rendering rule (frozen)

- **Rendering:** replacement, not dual.
- **Fields:** `qualified_name`, `signature`, and each `imports` / `references`
  entry.
- **Rule:**

  ```python
  re.sub(r'(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])', ' ', field)
  ```

  It inserts one space at camel/Pascal boundaries and never deletes or
  lowercases.
- **Not rewritten:** `file` and `kind`. Underscores, slashes, dots, `::` and
  digit runs are untouched.
- **Examples (verified):**

  | input | R1 output |
  |---|---|
  | `ConnectionPool` | `Connection Pool` |
  | `HTTPTransport` | `HTTP Transport` |
  | `safeParseAsync` | `safe Parse Async` |
  | `Base64Encoder` | `Base64 Encoder` |
  | `Client.sendRequest` | `Client.send Request` |
  | `utf8`, `snake_case`, `__init__`, `HTTP_PROXY`, `a.b/c` | unchanged |

## 5. Phase 0: tokenizer mechanism

**Exposed identifier sample** (PROTOCOL §3 definition): all 60 distinct
split-affected tokens of gold keys in exposed tasks (dev + held-out).

| metric | baseline | R1 |
|---|---:|---:|
| continuation (`##`) pieces | 227 | **69** (**−69.6 %**) |
| total pieces | 287 | 224 (−22.0 %) |
| components recoverable as a whole word-start piece (152 components) | 34 (22 %) | **100 (66 %)** |

**Correction after review.** The first run (`results/phase0.v1-narrow-sample.json`)
used a narrower sample than the protocol defines: only the 50 tokens whose
component matched a query word. It gave −68.2 %, 226 → 180 pieces and 27 → 78
of 121 components. The script now follows the protocol definition. Stop rule 1
passes either way. The same fix dropped a non-protocol component fallback,
which changed only the descriptive component count (830 → 828).

**Descriptive, all split-affected tokens in held-out fused top-50 names**
(344 tokens; recoverability over 828 components):

- continuation pieces −74.6 %;
- total pieces −24.7 %;
- recoverability 25 % → 72 %.

Examples (`base pieces ⇒ R1 pieces`; `sendRequest` is illustrative and not
in the measured sample):

| identifier | base pieces | R1 pieces |
|---|---|---|
| `SecureCookieSessionInterface` | `secure ##co ##oki ##eses ##sion ##int ##er ##face` | `secure cookie session interface` |
| `NotFound` | `not ##fo ##und` | `not found` |
| `LineBuffer` | `line ##bu ##ffer` | `line buffer` |
| `sendRequest` | `send ##re ##quest` | `send request` |
| `ZodType` | `z ##od ##type` | `z ##od type` |

Residual fragmentation remains for words outside the vocabulary:

| identifier | R1 pieces |
|---|---|
| `validateAsync` | `valid ##ate as ##yn ##c` |
| `GitignoreBuilder` | `gi ##ti ##gno ##re builder` |
| `mayOmitUndefined` | `may om ##it und ##efined` |

- **The split is not monotone.** On the descriptive sample, 8 of 344 tokens
  get *more* pieces under R1. Examples: `LookupDict` (`##dict` → `di ##ct`),
  `SearcherTester` 3 → 4, `NetRCAuth` 3 → 4.
- The rule also produces some odd but rule-conformant splits: `ABCd` → `AB Cd`,
  `iOS` → `i OS`, and hex literals in signatures (`0xDeadBeef` →
  `0x Dead Beef`).

**Stop rule 1** (≥ 25 % continuation reduction): **PASS** (69.6 %).

## 6. Exposed-task counts (PROTOCOL §2, mechanical)

| set | tasks | exposed | ident | exposed ∩ ident |
|---|---:|---:|---:|---:|
| dev | 70 | **20** | 21 | 5 |
| held-out (`heldout-clean.jsonl`) | 65 | **22** | 24 | 12 |
| ContextBench | 21 | not computable (gold unavailable) | — | — |

- Exposed held-out tasks by repo:
  - ripgrep 3, requests 1, flask 3, pytest 5, zod 8, httpx 2.
  - pylint 0 on held-out; 10 of dev's 20.
- **Stop rule 2** (≥ 15 held-out exposed): **PASS** (22).
- The #30 parent-commit set has 63 of these 65 tasks. The task-level mapping
  is not committed, so the 63-task count cannot be computed. Even if both
  missing tasks were exposed, it would be ≥ 20.
- Overlap caveat: 12 of the 22 exposed held-out tasks are also ident. The
  component match often comes from the query naming the identifier's parts
  (e.g. an identifier-bearing commit message). Exposure is mechanical, and
  this overlap is reported, not filtered.

## 7–12. Dev, held-out semantic and fused, identifier regression, ContextBench, final pack

**Not run.** These need Phase 1 corpora. The preregistered gates in
PROTOCOL §4 are unchanged and unmeasured. Nothing was embedded.

## 13. Token-length impact

- **Measured proxy.** R1 changes the mean `qualified_name` token count from
  8.09 to 7.46 (**−7.8 %**) and p95 from 19 to 18.
  - The sample is 2,473 held-out fused top-50 non-module candidates, of which
    1,789 are distinct.
  - It is drawn from retrieval results, so it is selection-biased and is not
    the whole corpus.
- R1 never removes characters. WordPiece output usually gets shorter because
  whole vocabulary words replace sub-piece chains. It is not monotone: some
  tokens get longer (§5).
- **Full `symbol_embed_text` delta: unmeasured.** Signature, imports and
  references are not in the frozen dumps. The proxy points to a decrease, but
  the +5 % cost gate stays **open** until Phase 1 measures the full text,
  before any quality metric.

## 14–15. Indexing latency and storage

- **Not measured** (no corpora).
- Storage *(inferred)*:
  - vector size is fixed at 384 × f32;
  - the `content_hash` width is unchanged;
  - no new column;
  - expected Δ ≈ 0 %.
- Latency *(inferred)*: one regex pass per symbol, and fewer pieces per
  *affected identifier* (−22 % on the exposed sample; −7.8 % on the
  qualified-name proxy). Expected neutral. Unmeasured.

## 16. Fingerprint implications (modeled; nothing changed)

- **R1 is a new embedding space.** Every symbol whose embed text contains a
  camel/Pascal identifier gets a different vector.
- **The current fingerprint would NOT detect it.** For native providers,
  `document_profile` encodes only the document *prefix*
  (`src/embeddings/native/mod.rs:461`, `"none"` for Arctic), not the
  document-text recipe.
- **The per-symbol key would change, but not atomically.**
  - `content_hash = embedding_input_hash(parser_hash, symbol_embed_text(s))`
    (`src/index/pipeline.rs:209`) would change.
  - But existing indexes are not force-migrated. Rows for files not reparsed
    keep old keys and vectors until those files change or `oxide index -a`
    runs (`docs/agents/invariants.md`, embedding-key invariant).
  - Without a fingerprint change, D0 and R1 vectors would **coexist silently**
    in one index.
- **Required if R1 were ever accepted:**
  - change **every provider's** `document_profile`, e.g. native `none` →
    `text:r1-camel-split-v1`. `symbol_embed_text` feeds every provider:
    native `native/mod.rs:461`, remote `remote.rs:277/427/566`, and HTTP
    `http.rs:268–301`. The fingerprint then differs, and the existing
    provider-switch / migration path clears and re-embeds all vectors;
  - update the pinned fingerprint tests;
  - the embedding cache needs no change: `src/embeddings/cache.rs` keys on
    `(fingerprint, content_hash(exact text))`, so it can never serve a D0
    vector for R1 text;
  - `EXTRACTION_VERSION` is not a substitute: it versions parsing and
    extraction, not the embedding text recipe.
- The same gap applies to any future `symbol_embed_text` change. A text-recipe
  field in `document_profile` is the general fix, but it is out of scope here.

## 17. Independent review

This ran in Claude Code cloud, where Codex was unavailable, so the reviewer
was **a fresh Claude subagent**.

- It had no part in design or execution, worked read-only, and did not
  retune anything.
- It recomputed everything with its own code: a hand-written boundary scanner
  instead of the regex, and without `phase0.py`.
- It found **no BLOCKER and no MAJOR** issue.
- It confirmed:
  - the exposure counts, exactly (dev 20/21/5, held-out 22/24/12, and the
    per-repo split);
  - that exposure reads only gold keys and the query, and masked is never used;
  - that the tokenizer matches production: fastembed 6.0.2 loads the same
    repo files through unpinned `main`, with `tokenizers` 0.22.2 in both;
  - every number;
  - that INSUFFICIENT EVIDENCE follows from PROTOCOL §5.

Its MINOR and NOTE findings, all fixed in this report:

1. The exposed sample was narrower than the protocol definition. Fixed:
   re-run on the protocol sample, −69.6 %; both runs are kept.
2. The rule is not monotone, with some odd splits. Disclosed (§5, §13).
3. Recoverability used a component fallback. Removed.
4. `sendRequest` is not in the measured data. Labeled illustrative.
5. The freeze evidence is mtime-only. Stated (§3).
6. The fingerprint fix must cover every provider's `document_profile`.
   Corrected (§16).
7. The cost wording overclaimed. Softened, with the proxy's selection bias
   and duplicates noted (§13–15).

## 18. Disposition

**INSUFFICIENT EVIDENCE.**

- Phase 0 passes.
- The quality, identifier-regression and cost gates could not be evaluated
  because the Phase 1 corpora cannot be obtained here.
- ContextBench gold is also unavailable.

## 19. Smallest justified next action

Run PROTOCOL §4 unchanged in an environment with GitHub access for the corpus
repositories. Nothing new needs designing:

- rebuild the #30 parent-commit held-out corpora and dev corpora, and the
  ContextBench base commits plus `EuniAI/ContextBench` gold. Package-registry
  sources cannot reproduce parent-commit corpora;
- add an R1 variant to `examples/semantic_variant.rs` in a research
  worktree, as a document-text wrapper like D1–D5;
- check D0 bit-parity, then run dev → held-out → ContextBench with the frozen
  gates.

Estimated cost, from the semantic-quality checkpoint: about 2–2.5 min per dev
variant with the pool + cache, plus corpus indexing.
