# R1 identifier-rendering experiment (camelCase splitting in the document embedding text)

Status: **research complete. Disposition: REJECT.** Production is
unchanged: `symbol_embed_text`, the model, fingerprints, RRF, lexical,
allocator and structural expansion are all untouched. The R1 arm existed
only in a research worktree (`harness/research.patch`). Every number is
**measured** unless marked *inferred*.

## Answer

**The Arctic symbol-text formatting track is closed.**

- R1 splits camelCase/PascalCase identifiers in the document text
  (`ConnectionPool` → `Connection Pool`).
- It fixes the tokenizer mechanism: WordPiece continuation pieces fall by
  **69.6 %**. But it does **not** improve semantic retrieval for OXIDE.
- On the leakage-free held-out set, all three preregistered quality gates
  fail:

  | gate | held-out result |
  |---|---|
  | exposed-stratum semantic R@50 | **−0.089 [−0.208, +0.014]** |
  | fused nDCG@10 | **−0.051** |
  | identifier-query nDCG@10 | **−0.023 [−0.081, +0.038]** |

- The positive dev signal did not transfer.
- ContextBench showed essentially no benefit.
- Throughput and storage do not regress, so cost is not the reason for the
  rejection; quality is.

Conclusions:

- camelCase fragmentation is real but **not causative**. Substantially
  cleaner WordPiece tokenization does not translate into better semantic
  retrieval.
- Do not try ad-hoc R2/R3 text-formatting variants.
- With D1/D3/D5 (`docs/semantic-quality-eval/CHECKPOINT.md`) and #30,
  document-text reformatting for the shipped 22M-parameter model is now
  repeatedly negative. No further text-recipe tweak is currently justified.

A separate correctness finding stands regardless: the embedding fingerprint
does not encode the symbol-text recipe (§8).

## 1. Provenance and preregistration

| item | value |
|---|---|
| baseline | `origin/main` `024afa5e76d0fc3e4f3ed4f64be1935e0c20ebab` |
| shipped model | `arctic-embed-xs-q` = fastembed 6.0.2 `SnowflakeArcticEmbedXSQ` = HF `snowflake/snowflake-arctic-embed-xs` `onnx/model_quantized.onnx` |
| model/tokenizer revision | HF `d8c86521100d3556476a063fc2342036d45c106f`; `tokenizers` 0.22.2 (= `Cargo.lock`) |
| `PROTOCOL.md` | sha256 `c88b49f1…7d83`, frozen 2026-09-28T09:35:56Z, before any tokenization; unchanged since |
| `ANALYSIS_PLAN.md` | `fc3cf871…bb02`, frozen before any dev metric |
| `PARITY_DIAGNOSIS.md`, `score.py`, `parity.py` | `FROZEN_SCRIPTS.sha256`, before any dev metric |
| dev results | `DEV_FROZEN.sha256`, before held-out was scored |
| held-out results | `HELDOUT_FROZEN.sha256`, before ContextBench was scored |

- The tokenizer hashes are in `tok/SHA256SUMS`. The runtime-downloaded
  `tokenizer.json` is byte-identical to the one Phase 0 measured.
- `sha256sum -c` passes for every hash file in this directory.
- Freeze ordering is evidenced by file mtimes only.
- Phase 0 ran in a Cloud session that could not reach GitHub, and its
  standalone report is `phase0-report.md`. Phase 1+ ran later, once GitHub
  was reachable.

## 2. R1 rule (frozen)

- **Rule:** replacement, not dual rendering.

  ```python
  re.sub(r'(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])', ' ', field)
  ```

  It inserts one space at camel/Pascal boundaries and never deletes or
  lowercases.
- **Fields rewritten:** `qualified_name`, `signature`, and each `imports`
  and `references` entry.
- **Unchanged:** `file` and `kind`; underscores, dots, slashes, `::` and
  digits; the query text and prefix.
- **Implementation:** `r1_split` in `harness/research.patch`, a Rust port of
  the regex. Verified equal to the Python rule on 329,808/329,808 symbols
  (parity c); R1 changes 68 % of them.

## 3. Phase 0: tokenizer mechanism (PASS)

On the 60 exposed identifiers (the protocol definition):

- continuation pieces fall **227 → 69 (−69.6 %)**, well above the ≥ 25 % stop
  rule;
- total pieces fall 287 → 224 (−22 %);
- component words recoverable as a whole piece rise from 22 % to 66 %.

For example, `SecureCookieSessionInterface` goes from
`secure ##co ##oki ##eses ##sion ##int ##er ##face` to
`secure cookie session interface`.

- The split is not monotone: 8 of 344 tokens get *more* pieces.
- An earlier narrow-sample run (−68.2 %) is kept in
  `results/phase0.v1-narrow-sample.json`.
- Held-out exposed tasks: 22, above the ≥ 15 stop rule.

## 4. Corpora, gold and baseline parity

- **Corpora:** 93 git checkouts at pinned SHAs (`harness/corpora.json`).

  | role | corpora | tasks |
  |---|---|---|
  | dev | 5 committed-dump HEAD corpora | 70 |
  | held-out | 65 first-parent corpora (the #30 parent-commit convention; leakage-free for commit gold) | 65 |
  | held-out HEAD parity | ripgrep and httpx HEAD | parity (a) only |
  | ContextBench | 21 base commits | 21 |

- **ContextBench gold:** `EuniAI/ContextBench` `data/full.parquet` at
  `2b43909` (sha256 `2f56535b…`).
  - The manifest pin `1436c28a` no longer contains that file.
  - Distinct gold-line counts match the committed scorer pin on 21/21, and
    base commits on 21/21.
- **Indexes** were built with the production `oxide index` (default
  embedder) from `024afa5` in a research worktree.
- **Arms:** D0 = the stored production vectors (`--no-embed`). R1 =
  re-embedded through production `update_embeddings` with the R1 text,
  with a cache keyed on (R1 fingerprint, exact text).

| parity gate | result |
|---|---|
| (a) D0 reproduces the committed 2026-09-20 ranking-fusion dumps | **FAILED, diagnosed.** Lexical is identical on 106/106. Semantic lists differ slightly: top-50 overlap averages 48.8–49.2/50 (min 46), and the fused top-10 is the same set on 91/106. |
| (b) harness D0 re-embed reproduces the stored production vectors | **PASS**: 14 corpora, 69,447/69,447 bit-identical (41,417 freshly embedded, 28,030 from the harness's own D0 cache) |
| (c) R1 text = the frozen Python rule; D0 text = `symbol_embed_text` | **PASS**: 329,808/329,808 |

Diagnosis of (a), in `PARITY_DIAGNOSIS.md`, frozen before any R1 metric:

- `oxide` builds at `5c62edd` (the ranking-study era), `32ff85e` (before the
  one-text-per-call fix) and `024afa5` produce **bit-identical vectors** on
  fresh indexes here. The code is not drifting.
- The committed lists came from the prior study's machine-local indexes,
  which cannot be regenerated.
- **The user decided** to proceed on a fresh-index D0 baseline, with both
  arms on identical indexes and code.
- Lexical lists are identical across D0 and R1 on all 158 tasks, and D0
  re-embed dumps equal the D0 dumps on 92/92.
- Consequence: absolute D0 numbers here are **not** the
  `docs/canonical-baseline.md` numbers (the fused top-10 differs on 15/106
  tasks). Only the paired R1 − D0 differences are the result.
- **Process note from review:** the complete parity (b)/(c) records
  (`results/parity.json`) were written after held-out and ContextBench were
  scored. The pre-dev record (`results/parity-dev.json`) covers 9 of the
  14 (b) corpora and 72,091 symbols for (c). Every check passed; the
  outcome is unaffected.

**Datasets:**

- **Held-out:** 63 of 65 tasks are valid.
  - `ripgrep-31d3f162` and `zod-62e6624b` have no gold symbol at their
    parent (the commit created it), matching #30's 63.
  - 18 valid tasks lose 35 gold keys at the parent; the remaining gold is
    scored.
  - The duplicate pair `httpx-336204f0` / `httpx-88a81c5d` is in neither the
    exposed nor the ident stratum.
- **ContextBench:**
  - 2,857 distinct gold lines, of which those in files OXIDE does not index
    are dropped.
  - 209 gold symbols under the innermost-symbol rule, 27 of them
    `__module__` fallbacks.
- **Dev:** 70/70 valid; post-commit corpora, so leakage-prone (screen only).
- **Exposed tasks** (PROTOCOL §2, all task gold keys): dev 20, held-out 21,
  ContextBench 11.
  - Two held-out exposed tasks are exposed only through gold keys absent at
    the parent, and both have Δ = 0.
  - Under a present-gold exposure count, n = 19 and the Δs are slightly
    worse: R@50 −0.098, nDCG@10 −0.056.

## 5. Quality results (Δ = R1 − D0; paired task bootstrap, 2000 resamples, seed 32, 95 % CI)

### Held-out, n = 63 (gating)

| stratum | n | Δ sem R@16 | Δ sem R@50 | Δ sem R@200 | Δ fused nDCG@10 | median best gold sem rank | median fused gold rank | Δ pack coverage |
|---|---:|---|---|---|---|---|---|---|
| **exposed** | 21 | −0.046 | **−0.089 [−0.208, +0.014]** (1 win, 4 losses) | +0.027 | **−0.051 [−0.116, +0.002]** | 8 → 5 | 4 → 4 | +0.002 |
| **ident** | 22 | +0.024 | −0.070 [−0.138, −0.009] | −0.018 | **−0.023 [−0.081, +0.038]** | 3 → 3 | 2 → 2.5 | +0.032 |
| desc | 41 | −0.055 | −0.016 | +0.024 | −0.026 | 37 → 43 | 14 → 14 | −0.028 |
| all | 63 | −0.027 | −0.035 [−0.080, +0.001] | +0.009 | −0.025 [−0.055, +0.003] | 18 → 19 | 8 → 9 | −0.007 |

Censored mean gold ranks and per-stratum wins/losses are in
`results/score-*.json`.

Gates (PROTOCOL §4):

- **Semantic** (exposed Δ R@50 ≥ +0.05, CI > 0): **FAIL**.
- **Fused** (exposed Δ nDCG@10 ≥ +0.02): **FAIL**.
- **Identifier** (ident Δ nDCG@10 ≥ −0.02, CI lower bound ≥ −0.05):
  **FAIL**, marginally on the point estimate (−0.0226), clearly on the CI.
  The verdict does not depend on this gate.

### Dev, n = 70 (screen; nothing tuned)

- Exposed (n = 20): Δ sem R@50 +0.085 [+0.000, +0.210] (3 wins, 0 losses);
  Δ nDCG@10 −0.039.
- Ident: Δ nDCG@10 −0.009 [−0.057, +0.028].
- All: Δ sem R@200 −0.035.
- All three dev gates failed formally. The semantic point estimate cleared
  its margin, so dev was not treated as "failing badly". "Badly" was a
  judgment call the protocol does not define.
- **The dev improvement did not transfer.**

### ContextBench, n = 21 (may reject, cannot rescue)

- Exposed (n = 11): Δ sem R@50 +0.023 [−0.051, +0.125]; Δ nDCG@10 +0.001
  [−0.023, +0.031]; median fused gold rank 4 → 7.
- Ident: Δ nDCG@10 +0.012.
- All: Δ sem R@50 +0.027; Δ nDCG@10 −0.000.
- **No contradiction.** Neither exposed Δ has a CI upper bound below 0.
- Essentially no benefit: both margins are missed.

### Final-pack coverage (secondary)

- held-out Δ −0.007
- ContextBench Δ −0.011
- dev Δ +0.018

All are noise-level.

## 6. Cost (gates pass)

**Document tokens**, counted with the shipped tokenizer, one text at a
time, with padding and truncation disabled:

| corpora | symbols | mean D0 → R1 | Δ | median | p95 |
|---|---:|---|---:|---|---|
| held-out parents (65; primary) | 206,395 | 131.8 → 128.0 | **−2.9 %** | 102 → 100 | 372 → 350 |
| head-* (5 dev + 2 held-out HEAD) | 35,453 | 114.9 → 112.2 | −2.4 % | 87 → 85 | 330 → 316 |
| ContextBench (21) | 87,960 | 87.8 → 86.5 | −1.5 % | 72 → 71 | 183 → 180 |

- At the production 510-piece cap, the held-out mean goes 129.3 → 125.6,
  and truncated symbols fall from 1,143 to 1,058.
- The first `cost.py` run had a counting bug: `encode_batch` applied
  `tokenizer.json`'s BatchLongest padding, so every count read 512. It was
  fixed after the quality gates were frozen. The reviewer recounted
  independently and got the same numbers.

**Storage:** +0.0 % for embedding blob bytes and DB bytes after full
re-embeds of the same 12 corpora. This is trivially true for fixed
384-d f32 vectors.

**Embedding throughput** (indicative): production per-text path
(`--bench-batch 1`), no cache, 2 alternating runs per arm, with about
12 % spread within an arm. Mean docs/s:

| corpus (parent) | D0 | R1 | R1 / D0 |
|---|---:|---:|---:|
| httpx | 77.1 | 76.2 | 0.99 |
| zod | 59.5 | 64.2 | 1.08 |
| ripgrep | 29.7 | 31.2 | 1.05 |

- No regression.
- The query path is unchanged; `embed_query` p50 is 5–7 ms in both arms.

## 7. Disposition

**REJECT** (PROTOCOL §5).

- The held-out semantic and fused gates fail by wide margins, with exposed
  n = 21 (19 under the stricter count), above the underpowered threshold.
- The identifier gate also fails.
- ContextBench does not contradict, and cannot rescue.
- Cost passes.

The result is robust to:

- the exposure-definition variant;
- an independent 20k-resample bootstrap (the reviewer's);
- dropping the marginal identifier gate.

## 8. Fingerprint correctness finding (separate from R1)

- The embedding fingerprint does not encode the symbol-text recipe:
  - native `document_profile` is `"none"` / `prefix:…`;
  - remote uses `none`, `voyage:document` or `jina:retrieval.passage`;
  - HTTP uses `{label}:none/prefix`.
- `content_hash = embedding_input_hash(parser_hash, symbol_embed_text(s))`
  changes per symbol, but existing indexes are not force-migrated. A
  recipe change would silently produce a **mixed-space index**.
- `src/embeddings/cache.rs` keys on (fingerprint, exact text), so the cache
  is safe.
- `EXTRACTION_VERSION` is unrelated and must not be the mechanism.
- It is tracked as its own correctness issue and is **not fixed here**.

## 9. Independent review

Codex is not installed in this environment, so the reviewer was a fresh
Claude subagent. Findings are in `REVIEW.md`: no BLOCKER or MAJOR, every
gating number reproduced independently, and REJECT follows. All MINOR and
NOTE items are fixed in this report.

## Files

- **Preregistration and freeze:** `PROTOCOL.md`, `ANALYSIS_PLAN.md`,
  `PARITY_DIAGNOSIS.md`, plus the `*.sha256` freeze records.
- **Scripts:** `phase0.py`, `score.py`, `parity.py`, `cost.py`.
- **`harness/`:**
  - `research.patch` (the R1 arm plus `--dump-texts` fields, research-only);
  - `prep.py` and the shell drivers;
  - `corpora.json` (corpus → repo, SHA).
- **`results/`:** Phase 0, parity, the frozen scores, cost and bench.
- **`tok/SHA256SUMS`**, **`phase0-report.md`**, **`REVIEW.md`**.
- **Not committed:** corpora, indexes, the model and tokenizer files,
  caches, the 74 MB of dumps and 540 MB of text dumps. `harness/` scripts
  contain the machine-local scratch paths they ran from.
