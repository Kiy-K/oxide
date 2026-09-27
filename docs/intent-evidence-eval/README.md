# Developer-intent evidence research (issue #30)

Status: **research complete; disposition REJECT for the tested evidence
lanes. Production unchanged.** Nothing here changes the schema, symbol ids,
`symbol_embed_text`, the embedding provider, frozen RRF (K=60, 0.6/0.4, depth
200), context budgets or CLI/MCP contracts.

- Baseline SHA: `519fb02c58756bc75415f4b8a2c18acd41db0e28` (pushed `main`),
  worked in a detached `/tmp` worktree.
- Harness binary: `examples/intent_dump.rs` built at that SHA, pinned by
  `results/intent_dump.SHA256SUMS` (`35f72b2e…`).
- Scratch data (evidence JSONL, dumps, embedding cache): `~/.cache/oxide-intent-eval/`
  (machine-local, not repository artifacts).

Every number below is **measured** unless marked *inferred*. All figures are
from the second, corrected run (after the Codex review, §11).

## 1. Answer in one paragraph

Can comments, docstrings and repository docs recover coding-agent context
that symbol-centric retrieval misses? **Not in any tested shape, once the
right controls are applied.** Symbol-attached intent (C) does improve the
pack against production on the held-out commit set, and on its strictly
identifier-free stratum it even lifts top-10 recall. However, a *non-intent*
control of the same shape — the same symbols' code bodies with every comment
removed, fused the same way — delivers statistically the same gain
(intent − code: pack +0.050 [−0.028, +0.131]; strict-desc +0.037
[−0.100, +0.173]). On ContextBench's independent human gold, C ties
production's own next candidates on delivered pack lines (−0.003
[−0.016, +0.010]) and loses top-10 line coverage (−0.101 [−0.183, −0.031]).
Separate evidence nodes (B) added no gold lines on ContextBench. README and
docs chunks never located gold code in 210 tasks (a code-locator result;
their value as *explanation* is unmeasured). The one clear intent win is on
the comment-anchored set, which favors intent by construction. The cost is
real: +13–72 % index size (median ~40 %) and roughly doubled first-index
embedding.

## 2. Phase 1 — inventory (13 repositories, Python/Rust/TypeScript)

Extractor: `scripts/extract_intent.py` (tracked files only; Python docstrings
via `ast`; comment blocks by marker for other languages; docs split at
headings; long content split on line boundaries so every record carries the
line span of its own text; symbol association from the corpus's own OXIDE
index, read-only). Full tables: `results/inventory.md`.

43,932 records (all / kept after filtering):

| type | all | kept | mean chars | median | p90 | symbol-associated |
|---|---:|---:|---:|---:|---:|---:|
| docstring (Python) | 5,689 | 5,189 | 139 | 66 | 302 | 98 % |
| leading_comment | 7,902 | 6,612 | 206 | 88 | 536 | 100 % |
| inline_comment | 19,616 | 16,542 | 92 | 56 | 178 | 80 % |
| module_doc | 2,328 | 1,452 | 200 | 62 | 512 | file-level |
| readme | 557 | 553 | 509 | 358 | 1,297 | none |
| doc | 5,328 | 4,892 | 588 | 390 | 1,456 | none |
| changelog | 2,512 | 2,499 | 396 | 198 | 1,308 | none |
| TODO/FIXME-tagged (orthogonal) | 707 | — | — | — | — | — |

- **Volume**: kept intent text = 0.06–1.20× the indexed code's tokens
  (flask 1.20× because of its docs tree; darkreader 0.06×, nushell 0.08×).
- **Symbol coverage**: 5–52 % of non-module symbols carry a docstring or
  leading comment (darkreader 5 %, ripgrep 52 %); 4–59 % of attached symbols
  are in test files (pylint 59 %, pytest 42 %).
- **Duplication**: 2–15 % of kept records are exact duplicates within a repo
  (zod 15 %, pylint/nushell 13 %). Docs almost never restate code comments
  verbatim (≤ 6 chunks per repo).
- **Already visible to production lexical search**: Python docstrings and
  inline comments lie inside symbol spans, and BM25 already indexes symbol
  bodies (weight 1). Only leading comments (`///`, JSDoc, Go) and file/repo
  docs are new to lexical search. 16–92 top intent records per task set were
  skipped at pack time as duplicates of already-packed code.
- **Update frequency** (`scripts/churn.py`, 200–500 non-merge commits; the
  pytest/ripgrep/tokio/darkreader clones were too slow to scan): 16–61 % of
  commits touch comment lines (flask 18 %, requests 16 %, zod 52 %);
  comment:code line churn 0.05–0.11; docs touched by 12–54 % of commits.
  Record churn between adjacent held-out corpora of one repo: 0.2–1.4 %
  (median 2–42 records, max 246). This is record churn, not a measured
  incremental index update.
- **Noise** (flagged and not kept): directive/trivial 10 %, too short 4 %,
  commented-out code 2 %, license 1 % (mostly `module_doc`), generated files
  1 % (mostly generated docs), OXIDE-unindexed code files 0.2 %.
- **Privacy/safety**: 39 secret-pattern hits (0.09 %). On manual review of
  the first run's 38, **all were false positives** (URL path segments,
  commit hashes in links, placeholder credentials such as `user:password@`,
  an example hash). 98 records contain e-mail addresses; in the corrected
  run they are excluded from embedding. These public repositories contain no
  real secrets, so this corpus **cannot** validate a secret filter's recall
  on private code (*inferred*). Vendored/build paths and lockfiles are
  excluded by path.

Classification:

| source | class | reason |
|---|---|---|
| Python docstrings | **insufficient evidence** | reliable, 98 % symbol-attached, but already in BM25 body text; no intent-specific gain over the code control |
| leading comments (`///`, JSDoc) | **insufficient evidence** | reliable; the only intent source new to lexical search; no significant gain over the code control |
| inline comments | **noisy** | most numerous, short, 10 % directive noise, heavy test-file share; drive the displacement failures (§6) |
| module/file docs | **noisy** | ~35 % license/directive (dropped); never located gold |
| README chunks | **noisy as code locators** | 0 gold located in 210 tasks; value as explanation unmeasured |
| repository docs / changelog | **noisy as code locators**; generated-doc trees **unsafe without filtering** | 0 gold located |
| TODO/FIXME | **insufficient evidence** | 707 records (1.6 %); too few task hits to measure |

## 3. Representations tested (Phase 2)

Offline only. The production lists are dumped untouched, and every challenger
is computed from the same dump (`scripts/score_intent.py`). The protocol was
written in its docstring before any challenger result was seen; post-hoc
additions are labeled.

- **A — symbol-only baseline**: production `fused` list and production pack.
  Self-check: 63/63 held-out tasks reproduce the earlier pinned baseline
  dump's lexical, semantic, fused and pack lists exactly.
- **I — research intent channel**: its own BM25 over kept evidence
  (production tokenizer and formula), plus an exact dot-product scan with the
  production embedder and query vector. Fused RRF(K=60) at the production
  0.6/0.4, not tuned. Evidence text is embedded **alone**, with no code
  identity.
- **B — separate evidence nodes**: RRF of [A w1.0, I w0.5]. Evidence records
  are units with provenance: file, own line span, type, attached symbol,
  source text, and a stable research id `ev:<sha1>`.
- **C — symbol-attached**: I mapped to attached symbols (docstring, leading
  and inline comments), then RRF of [A w1.0, I-symbols w0.5]. Symbol ids,
  names and embedding text are unchanged.
- **H — constrained hybrid**: C plus at most one file/repo-level doc record
  in the pack.
- Ablations: B-code and B-docs (evidence type); C-lex and C-sem (channel).
- **Post-hoc, labeled**: B1/C1 at w=1.0. At w=0.5 an intent unit's best RRF
  score only ties production rank ~62, so B can never enter a top-10 list.
- **Controls** (added after the first BCD/CA run, before scoring held-out,
  CB and dev):
  - **A+**: a same-rule insertion control. Production's own next two top-10
    symbols are inserted under the same pack rule. It is *not* token-matched:
    A+ inserts ~15 % more tokens than C, so per-token metrics are the fair
    comparison.
  - **Cpy-code**: C-shaped fusion over each symbol's code body with every
    extracted comment/docstring line removed (no intent text).
  - **Cpy-intent**: the identical Python BM25 over intent records.

  The code control is not an exact causal subtraction. It is per-symbol and
  lexical-only, while intent is per-record and C also has a semantic
  channel.

Packs: at most two inserted units, capped at the 4096 production budget.
**The budget never bound in any run**, so no production item was ever
replaced; these experiments test two extra selections, not reallocation.
Pack items are scored on the lines production would deliver. Symbols go
through a port of `context.rs::render_snippet` (350-token cap, query-term
window); evidence goes by its own span. Evidence inside or attached to an
already-packed symbol is skipped as a duplicate.

## 4. Benchmarks (Phase 3)

| set | n | gold | corpora | bias note |
|---|---:|---|---|---|
| ContextBench (CB) | 21 | human line spans (code) | base commit | **independent control**; canonical set unchanged |
| held-out (parent commits) | 63 | symbols changed by the commit; query = commit message | parent commit (no post-change leakage) | ~3 of 19 C wins are doc/docstring-edit commits whose message echoes the comment |
| dev | 70 | same as held-out | post-commit corpora | leakage favors intent (screen only) |
| BCD blind code-first descriptions | 37 | one sampled symbol | 4 repos | random sample (seed 30, non-test, 8–60 lines); queries written from comment-stripped code, ≤ 1 name token; 3 thin wrappers/tests excluded |
| CA comment-anchored | 19 | symbol owning a "why/workaround" comment | 4 repos | **favors intent by construction**; upper bound only |

All gold is *code*. Doc records can therefore only score by locating code
(attached to, or inside, a gold symbol, or overlapping gold lines). No task
has a documentation answer.

Strata: `desc` = the gold name's last component does not appear as a query
word. `desc-strict` = no tokenizer sub-token of any gold name appears in the
query (held-out n=25, dev 25, BCD 29, CA 9). The looser `desc` still
contains identifier cues (e.g. `iter_{content,lines}`).

Task files: `results/bcd.jsonl`, `results/ca.jsonl` (corpus `path` values are written with `~/`; expand them to absolute paths before running the scripts).

## 5. Results

`@10 sym` = gold-symbol coverage of the top 10. `pack sym` = gold-symbol
coverage of the pack (an evidence unit counts if attached to, or inside, a
gold symbol). `unit-hit tok/1k` = tokens of pack units that hit gold per 1k
pack tokens. This is a unit-level share, not token-level precision.

Full tables incl. strata: `results/score-*.txt`. Paired CIs (bootstrap,
2000 resamples, seed 0): `results/paired.txt`.

**Held-out (n=63, leak-free parent corpora)**

| arm | @10 sym | pack sym | unit-hit tok/1k | inserted tok/task | inserted unit-hit share | displaced@10 |
|---|---:|---:|---:|---:|---:|---:|
| A | 0.465 | 0.319 | 90.0 | 0 | – | 0 |
| A+ | 0.465 | 0.369 | 88.2 | 549 | 0.08 | 0 |
| B | 0.465 | 0.392 | 83.3 | 208 | 0.04 | 0 |
| B-code | 0.465 | 0.432 | 93.4 | 110 | 0.14 | 0 |
| B-docs | 0.465 | 0.319 | 73.1 | 314 | **0.00** | 0 |
| C | 0.501 | 0.486 | 120.0 | 479 | 0.21 | 14 |
| Cpy-code (no intent) | **0.574** | 0.439 | 106.9 | 410 | 0.16 | 8 |
| Cpy-intent | 0.493 | 0.489 | 119.5 | 485 | 0.20 | 17 |

- C − A pack +0.167 [+0.097, +0.250]; C − A+ pack +0.117 [+0.048, +0.201].
- Intent − code (Cpy-intent − Cpy-code): pack +0.050 [−0.028, +0.131],
  @10 −0.081 [−0.187, +0.025]. **Not significant.**
- desc-strict (n=25): C − A+ @10 +0.177 [+0.057, +0.313], pack +0.137
  [+0.037, +0.263]. Intent − code: @10 −0.017 [−0.200, +0.170], pack +0.037
  [−0.100, +0.173]. The strict-desc gain is real, but it comes from the
  second channel, not from intent text.

**ContextBench (n=21, human gold, base commit; pack lines = delivered snippet lines)**

| arm | @10 line | pack line | gold lines/1k pack tok | inserted tok/task | inserted unit-hit share |
|---|---:|---:|---:|---:|---:|
| A | 0.731 | 0.208 | 8.3 | 0 | – |
| A+ | 0.731 | 0.231 | 9.8 | 695 | 0.18 |
| B | 0.731 | 0.208 | 6.9 | 367 | **0.00** |
| B-code | 0.731 | 0.208 | 7.8 | 123 | 0.01 |
| B-docs | 0.731 | 0.208 | 6.7 | 460 | **0.00** |
| C | 0.631 | 0.228 | 7.6 | 591 | 0.07 |
| Cpy-code | 0.464 | **0.273** | **10.4** | 579 | 0.27 |
| Cpy-intent | 0.664 | 0.243 | 9.2 | 586 | 0.16 |

- C − A+: pack line −0.003 [−0.016, +0.010]; @10 line −0.101
  [−0.183, −0.031]. B-code − A+: pack line −0.023 [−0.048, −0.005].
- Intent − code: pack line −0.030 [−0.079, +0.005]; @10 line +0.200
  [+0.083, +0.337]. The code control's list badly misranks top-10 lines, but
  it packs more gold lines.

**dev (n=70, leakage-prone)**: C − A+ pack +0.073 [+0.021, +0.131]. Intent −
code: pack −0.005 [−0.051, +0.043], @10 −0.140 [−0.240, −0.047].

**BCD (n=37, blind)**: C − A+ pack +0.027 [−0.054, +0.135]; C − A @10 −0.081.
Intent − code: pack −0.027, @10 −0.135 [−0.270, 0.000].

**CA (n=19, favorable by construction)**: C − A+ pack +0.211 [+0.053, +0.421];
intent − code pack +0.263 [+0.053, +0.474]. This is the only set where intent
text beats the code control, and the set was built from the comments
themselves.

**Route failures recovered** (gold absent from production's fused top-200,
present in the challenger's top 10): held-out 0/22 for A+, B, C and H; 2/22
for the post-hoc B1 (with 16 displaced top-10 golds) and 1/22 for C1; BCD
0/4.

## 6. Relevant-token efficiency and pollution

- Evidence nodes (B) are cheap per insertion (110–208 tokens/task) but rarely
  hit gold: unit-hit share 0.04–0.14 on held-out, 0.00–0.01 on CB. B-docs is
  0.00 on every set: README/docs chunks are pure pollution *as code locators*.
- B-code's best case (held-out desc-strict: pack +0.143 [+0.033, +0.287] over
  A+ at ~1/5 of A+'s inserted tokens) delivers a comment and its location,
  not the code. On CB it is slightly *negative* against A+.
- C raises held-out unit-hit tok/1k from 90 to 120. On CB it lowers gold
  lines/1k (7.6 against A's 8.3 and A+'s 9.8).
- Displacement: C pushes gold out of the top 10 in 14/63 held-out tasks (net
  @10 +0.035). Cause: test docstrings and generic phrases ("This method is
  called to…") promoting tests and unrelated symbols. In `requests-a044b020`
  (DigestAuth hashing), three digest-auth *tests* were promoted via their
  docstrings; in `requests-bc7dd0fc`, a Sphinx theme class. No packed item
  was displaced, because the budget never binds.

## 7. Operational cost

| measure | value |
|---|---|
| extraction (Python research extractor) | median 0.4 s, max ~2 s per corpus (90 corpora, `results/extract_time.jsonl`) |
| evidence count | 0.24–1.57 kept records per symbol (median ~0.77) |
| first-index embedding | 105–284 records/s (e.g. pytest 6,800 records 38.5 s; pylint 8,495 records 34.6 s). That is about the same as embedding the symbols themselves (pytest 7,804 symbols 38–47 s pooled, per the semantic-quality checkpoint), i.e. roughly **2× first-index embed time** (*inferred* across separate runs) |
| storage (`scripts/storage_proxy.py`: provenance + text + 384-d f32 vector + postings; a proxy, not a schema) | +1.0 to +20.4 MB, **+13–72 % of index.db** (median ~41 %) — `results/storage.md` |
| query latency (in-memory BM25 + exact scan, query vector reused) | 0.46 ms (flask), 1.8 ms (pytest), 2.4 ms (pylint) per query |
| peak RSS (process high-water mark, 3 queries, warm cache, vs `--no-evidence`) | +6 MB (flask), +21 MB (pytest), +23 MB (pylint) |
| incremental update | record churn only (§2): 0.2–1.4 % of records per adjacent-corpus step. Not measured as an index update |

## 8. Disposition

**REJECT** for every tested lane:

- **Separate evidence nodes (B)**: no gold lines on CB, near-zero unit-hit
  share elsewhere. README/docs never located code.
- **Symbol-attached intent (C)**: the gain over production is reproduced by a
  comment-free code-text channel of the same shape. The intent-specific
  increment is not significant on any independent set, and on CB C does not
  beat production's own next candidates.
- **Hybrid (H)**: inherits C's ranking plus B-docs' pollution.
- **Cost**: ~+40 % storage, ~2× first-index embedding, and a secret filter
  whose recall this public corpus cannot validate.

Scope limits: gold is code-only, so the value of docs as *explanation* for
an agent is **insufficient evidence** (unmeasured), not disproven. Coupling
intent into `symbol_embed_text` was not re-tested; the semantic-quality
track (D1) already found it flat on CB.

**Smallest justified next action**: none on #30 beyond recording this
negative result. The one live signal is outside #30's scope (§9) and needs
its own approval.

## 9. Observations outside #30's scope (not acted on)

- A second, separately fused lexical channel over comment-free code bodies
  (Cpy-code) moved held-out @10 by +0.109 [+0.019, +0.199] and CB delivered
  pack lines 0.208 → 0.273. But it cost CB @10 line −0.267. This is a
  fusion/ranking question: frozen, and `docs/ranking-fusion-eval` already
  rejected lexical weight ≥ 0.7.
- The production pack never fills its 4096 budget on these sets. Two extra
  production candidates (A+) raise pack coverage everywhere, which is an
  allocator question (the roadmap's "allocation caps").

## 10. Reproduce

```bash
git worktree add --detach /tmp/oxide-intent-519fb02 519fb02c
cd /tmp/oxide-intent-519fb02
CARGO_TARGET_DIR=~/.cache/oxide-intent-eval/target cargo build --release --example intent_dump
cp ~/.cache/oxide-intent-eval/target/release/examples/intent_dump ~/.cache/oxide-intent-eval/bin/
S=docs/intent-evidence-eval/scripts
$S/run_dump.sh <tasks.jsonl> <tag>                    # extract + dump per corpus
python3 $S/code_control.py <tasks.jsonl> > <tag>.control.jsonl
python3 $S/score_intent.py <tasks.jsonl> dumps/<tag>.jsonl --extra <tag>.control.jsonl [--cb]
python3 $S/paired.py dumps/<tag>.jsonl.scores.json <armX> <armY> [metric ...]
python3 $S/inventory_stats.py inventory_corpora.txt inv > inventory.md
python3 $S/storage_proxy.py inventory_corpora.txt inv <scratch_dir>
```

CB scoring needs `eval-agent/.venv/bin/python` (the ContextBench evaluator,
available in the main checkout).

## 11. Codex review (read-only) and resolution

No BLOCKER. Confirmed findings, all resolved before the second run:

| # | severity | finding | resolution |
|---|---|---|---|
| 1 | MAJOR | CB pack line coverage credited full symbol spans, not the snippet lines actually delivered | scorer ports `render_snippet` (350-token cap, query window) for every packed symbol. CB pack numbers dropped (A 0.478 → 0.208), and C vs A+ went from −0.089 to −0.003 [−0.016, +0.010]. Disposition unchanged |
| 2 | MAJOR | "equal-token" / "token-neutral" claims were inaccurate (budget never binds; A+ inserts more tokens) | renamed a same-rule insertion control; the report states the budget never binds and compares per-token metrics |
| 3 | MAJOR | "docs located gold in 0/210" is constrained by code-only gold | claim scoped to "as code locators"; docs-as-explanation marked insufficient evidence |
| 4 | MAJOR | split evidence parts inherited the whole chunk's line span | extractor splits on line boundaries with per-part spans (0 repeated kept spans). Also fixed a docstring off-by-one (`"""` on its own line): 1/8,551 residual text/span mismatch on pylint. Everything re-extracted and re-dumped |
| 5 | MINOR | the `desc` stratum still contains identifier sub-tokens | added `desc-strict` (tokenizer sub-tokens) and report it |
| 6 | MINOR | "inserted-token precision" is a unit-hit share | renamed throughout |
| — | note | e-mail records were still embedded (52 of 98) | e-mail now excludes a record from embedding |
| — | note | storage proxy not reproducible | added `scripts/storage_proxy.py` and `results/storage.md` |
| — | note | cost figures are proxies/inferences | labeled as such in §7 |

Also noted by Codex and accepted as a stated limitation: Cpy-code is not an
exact causal subtraction (per-symbol vs per-record, lexical-only).
