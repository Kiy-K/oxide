# Independent reviews (R1)

Codex is not installed in this environment, so both reviews were done by a
**fresh Claude subagent**. Each:

- took no part in design, tuning or result generation;
- worked read-only, with its own recomputation;
- was instructed not to propose retuning.

## Phase 0 review (Cloud session)

No BLOCKER or MAJOR.

- It reproduced the exposure counts exactly with a hand-written boundary
  scanner (dev 20/21/5, held-out 22/24/12).
- It confirmed the tokenizer matches production (same repo, files and
  `tokenizers` 0.22.2).
- MINOR findings, all fixed:
  1. The exposed identifier sample was narrower than the protocol's
     definition. Re-run on the protocol sample: −69.6 %; the first run is
     kept.
  2. The split is not monotone. Disclosed.
  3. The fingerprint fix must cover every provider, not only native.
  4. The cost wording overclaimed. Softened.
  5. A recoverability fallback was used. Removed.
  6. An illustrative example was not in the measured data. Labeled.
  7. Freeze evidence is mtime-only. Stated.

## Phase 1+ review

**No BLOCKER or MAJOR. REJECT follows from the preregistered rules.**

**Reproduced independently** from the raw dumps, task files and parquet:

| check | reproduced |
|---|---|
| exposure / ident counts | held-out 21 / 22, dev 20 / 21, CB 11 / 10 |
| held-out exposed Δ sem R@50 (20k-resample bootstrap) | −0.089 [−0.214, +0.008] |
| held-out exposed Δ nDCG@10 | −0.051 [−0.119, +0.002] |
| ident Δ nDCG@10 | −0.023 [−0.081, +0.034] |
| ContextBench | exposed +0.023 / +0.001, no contradiction |
| corrected held-out token counts | 131.81 → 127.95 (−2.93 %) |

**Also confirmed:**

- The Rust `r1_split` is equivalent to the frozen regex.
- Lexical lists are identical across arms on 158/158 tasks.
- D0 re-embed dumps equal the D0 dumps on 92/92.
- The fingerprint statement is accurate for native, remote and HTTP
  providers, the cache and `EXTRACTION_VERSION`.

| # | severity | finding | resolution |
|---|---|---|---|
| 1a | NOTE | Absolute D0 numbers are not `canonical-baseline.md` numbers (the fused top-10 differs on 15/106 tasks) | Stated (README §4) |
| 1b | MINOR | Parity (b) "69,447 re-embedded" includes 28,030 vectors from the harness's own D0 cache | Split reported (41,417 fresh) |
| 2 | MINOR | The complete parity (b)/(c) records postdate held-out/CB scoring; the pre-dev record covers 9/14 (b) corpora and 72,091 symbols for (c). `cost.py` was not frozen and was edited after the gates. | Disclosed (README §4, §6); all checks pass |
| 3 | NOTE | Two exposed held-out tasks are exposed only via gold keys absent at the parent (Δ = 0); the present-gold count is n = 19 (Δ R@50 −0.098, nDCG −0.056) | Disclosed (README §4) |
| 6 | MINOR | Miscounts: 35 missing gold keys (not 44) across 18 valid tasks; 2,857 distinct CB gold lines (not 3,150); 27/209 CB gold symbols are `__module__` fallbacks | Corrected and verified (README §4) |
| 7 | NOTE | The identifier gate's point-estimate miss is marginal (−0.0226 vs −0.02) | Stated; the verdict does not depend on it |
| 8 | MINOR | Token numbers were missing from the draft, and the `tokens_dev` label was wrong (it is the head-* corpora). "VACUUMed" was inaccurate. Latency rests on 2 runs with about 12 % intra-arm spread. | Corrected numbers reported, label fixed, wording fixed, latency called indicative (README §6) |
| 10 | NOTE | All three dev gates failed formally; "not badly" was a judgment call | Stated (README §5) |

No analysis, gate or decision rule was changed after review.
