# Parity (a) diagnosis and the baseline decision (before any R1 metric)

## Finding

The D0 dumps on fresh current-production indexes do **not** bit-reproduce the
committed `ranking-fusion-eval` semantic lists (dumps dated 2026-09-20).

- **Lexical** top-200 keys match exactly.
- **Semantic scores** differ slightly per symbol. On `requests-6f66281a`:
  - 76 of 198 shared scores are bit-equal, so the query vector is unchanged;
  - 122 differ, by at most 0.005 cosine.
  - On two CB tasks no score is equal.
- **Retrieval impact is small.** On 4/4 checked tasks (dev requests HEAD,
  plus 3 CB requests corpora):
  - semantic top-50 overlap is 49–50/50;
  - top-200 overlap is 198–200;
  - fused top-10 and top-16 are identical as sets.

## Diagnosis

The code is not drifting.

| test (fresh index, this machine) | result |
|---|---|
| `oxide` @ `5c62edd` (ranking-fusion baseline, 2026-09-21) vs @ `024afa5`, `cb-requests@091991be0da1` | **1166/1166 vectors bit-identical**; `signature`, `imports_json` and `references_json` identical; only `content_hash` differs (the documented `embedding_input_hash` key change) |
| `oxide` @ `32ff85e` (= `e1aeed5^`, before the one-text-per-call fix) vs @ `024afa5`, `head-requests@611c6162` | **933/933 bit-identical** |
| harness D0 re-embed vs stored production vectors (parity b) | 933/933 bit-identical |

The committed lists therefore come from the prior study's machine-local
indexes: their stored vectors differ per symbol from any fresh index built
here by any of these code versions. *Inferred*: this is how those indexes
were built (incremental updates through the pre-`e1aeed5` batched
dynamic-int8 path, and/or a different host). Those indexes cannot be
regenerated.

## Decision

The user was asked, and chose to proceed on fresh D0.

- **Baseline:** current-production D0 on fresh indexes of the pinned
  corpora. Both arms run on the same index and the same code, and only the
  document text differs.
- The D0 baseline is deterministic and reproducible:
  - parity (b) holds;
  - it is bit-identical across the three code versions above.
- **Parity (a) is recorded as FAILED, with this diagnosis.**
- Nothing about R1, the gates, exposure or the scoring changes.
