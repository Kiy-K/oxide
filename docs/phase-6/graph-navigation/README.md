# Agent-facing graph navigation (2026-10-25): Stage 1 gate FAIL

Question: can explicit, source-backed graph paths from `Repository.impact` answer
multi-file impact questions better than what an agent already has (grep, `oxide_search`
search/context), enough to justify an agent-facing graph representation? Frozen rule:
[preregistration.md](preregistration.md) (`results/freeze.txt`, before any arm was
scored). Numbers are measured unless marked *inferred*.

**Verdict.** The gate failed on the completeness signal, so Stage 2 (an agent trial)
was not run. No MCP tool, mode, CLI command or public field is added. Impact's
transitive answer is precise and cheap (Q2 F1 +0.22 over iterated grep at 3% of the
tokens), but it misses 41% of dependents. The one completeness signal it has
(`unresolved`) stays silent on 3 of the 11 incomplete targets and fires on 7 of the 13
complete ones. An agent given impact output therefore cannot tell when it has the whole
answer. The fix made is to the documentation: `public-api.md` § Impact had claimed
`unresolved == 0` meant no site names the root, which this study falsified.

## Audit: what OXIDE already has internally

| Need | Present | Where |
| --- | --- | --- |
| exact target | yes | `Impact.root`, `roots` (descendants, a file's modules) |
| relationship path | first shallowest step only | `ImpactHop.via` = `kind:in|out:key`; chains are rebuilt by following `via` |
| source witness | yes, not in `Impact` | `Relation.evidence` (a `SourceRef` of the site) via `relations(key)` |
| provenance | yes | `Relation.resolution`, `basis`; `ImpactHop.proven_depth` |
| ambiguity | counted as reachable, never proven | ambiguous edges set `depth`, not `proven_depth` |
| completeness | partial | `stopped_by` (bounds); `unresolved` (by-name, see below) |

Hop distance (`depth`) and certainty (`proven_depth`, `basis`) are already separate
fields. OXIDE makes no change-impact or breakage claim, unlike GitNexus's "depth 1 WILL
BREAK" grouping. Witnesses, like LSP `fromRanges` and SCIP `enclosing_range`
occurrences, exist per relation. Every evidence element of an agent-facing
representation was therefore already available. The open question was whether it is
correct and complete enough to rely on.

## Design

24 targets (12 module functions, 12 methods; 8 each from flask, requests, pytest),
sampled by seed from symbols that jedi shows have dependents in ≥ 2 files. Truth is
jedi 0.20.1 `get_references`, mapped to the innermost enclosing OXIDE entity. Q1 asks for
direct dependents, Q2 for dependents within 2 hops. Arms:

- **G**: `rg -nw` for the name; for Q2, plus one `rg` per function or method found.
- **C**: `build_context`, budget 4096, with a symbol hint.
- **I**: `impact` over every edge.
- **P**: the proven edges only.

Tokens are o200k counts of the arm's raw text.

## Results (`results/report.json`)

| Arm | Q1 P / R / F1 | Q1 tokens (Σ, median) | Q2 P / R / F1 | Q2 tokens (Σ, median) |
| --- | --- | --- | --- | --- |
| G grep | 0.778 / 1.000 / **0.828** | 9,454 / 229 | 0.311 / 1.000 / 0.413 | 134,463 / 2,637 (6 calls median) |
| C context | — / 0.414 / — | 2,814 median | — / 0.311 / — | same bundle |
| I impact | 0.993 / 0.659 / 0.701 | 2,409 / 69 | 0.993 / 0.585 / **0.633** | 4,326 / 79 (1 call) |
| P proven | 1.000 / 0.410 / 0.424 | 1,718 / 0 | 1.000 / 0.310 / 0.356 | 2,120 / 0 |

| Subset | G Q1 F1 | I Q1 F1 | G Q2 F1 | I Q2 F1 | I Q1 recall |
| --- | --- | --- | --- | --- | --- |
| functions (12) | 0.887 | 0.840 | 0.470 | 0.807 | 0.819 |
| methods (12) | 0.770 | 0.562 | 0.356 | 0.458 | 0.498 |

- **Path precision**: 93/93 depth-1 hops had a witness span that contained a jedi
  reference site of the target. All 77 depth-2 hops had a valid middle hop. Impact made
  1 false claim set (on `redirect`), which the audit shows were 3 true calls jedi missed.
  When impact names a path, the path is real.
- **False impact claims**: grep made 110 on Q1, mainly same-name methods
  (`dict.setdefault`, other `send`/`loads`), docstrings and comments. Impact made 0
  after the audit.
- **Missing dependents**: impact's recall is 0.66 on Q1. The misses are method calls
  through untyped receivers (`_FakeSignal.send` and `_AppCtxGlobals.setdefault` reach
  nothing), pytest fixture injection (`tmp_path` reaches nothing), decorators, bare names
  passed as values and receiver uses. Proven-only gives zero recall on every method,
  because method dispatch is never proven.
- **Completeness signal**: 11 targets are incomplete on Q1. The signal
  (`unresolved > 0` or `stopped_by`) has sensitivity 0.727 (gate ≥ 0.9) and specificity
  0.462. Silent misses:
  - `after_this_request`: a decorator, so no relation is recorded.
  - `wsgi_errors_stream`: a bare-name argument records no relation, and the receiver
    use `wsgi_errors_stream._get_current_object()` is not matched by `mentions()`.
  - `tmp_path`: fixture injection.

  4 of the 11 incomplete targets return no hop at all.
- **Context (C)** recovers 41% of direct dependents at about 2.8k tokens. The existing
  context workflow is not an impact tool, which is consistent with its design.
- **Latency** (warm median): rg 8 ms, impact 11–13 ms, `build_context` 88 ms.
- **Tool calls**: Q2 by grep needs a median of 6 calls; impact needs 1.

Gate (`results/report.json` § gate):

| Criterion | Value | Result |
| --- | --- | --- |
| I Q1: F1 gain ≥ 0.10 at ≤ G tokens | −0.127 | fail |
| P Q1 | −0.404 | fail |
| I Q2 | **+0.220**, 3.2% of G's tokens | pass |
| P Q2 | −0.057 | fail |
| completeness sensitivity ≥ 0.9 | 0.727 | **fail** |

## Audit of the oracle

- All 15 Q1 impact misses/false claims across the 11 targets were read in source.
  - The 3 `redirect` "false" claims are true `flask.redirect(...)` calls inside nested
    test functions that jedi did not return. This is a confirmed oracle error affecting
    one target (≤ 0.04 on any macro).
  - Every other miss is a real dependent that OXIDE did not reach.
- A sample of 30 of grep's 110 false Q1 claims (seed 20261025) was checked against the
  `rg` lines. Most are true false positives. A few are probable jedi misses: pytest
  fixture parameters, and `X.from_parent` on `FSCollector` subclasses. They were not
  confirmed individually.
- **Direction.** Every oracle error found is a jedi *miss*. Adding a miss to truth
  cannot lower grep's recall, which is 1.0 by construction (jedi's references are
  name occurrences, so `rg -w` is a superset). It can only lower impact's recall. The
  corrections therefore move the comparison against impact, and the FAIL is robust to
  them. *Inferred*: the Q2 F1 margin shrinks under full correction.
- Deviation: the preregistration called for ≤ 40 audited claims per corpus. The audit
  above is smaller (all impact discrepancies on Q1, plus 30 grep claims), because the
  verdict cannot flip in its direction.

## Recommendation

1. **No agent-facing graph tool or mode now.** The information is precise but
   silently incomplete, so an agent following impact alone would miss dependents with
   no warning. Grep, which agents already use (0 OXIDE calls in 84 prior trial runs),
   has full recall at Q1. Two prior agent trials found unused navigation tools; this
   study gives no reason to expect an adopted one would help on Q1.
2. **What would change the verdict** is completeness, not representation. In order:
   - Record decorator and bare-name value references (a Python derivation change, with
     a baseline check because references feed routing).
   - Count receiver uses (`name.*`) in `unresolved` (costs the VS Code latency win from
     graph-v2, because the name index is the last segment).
   - Report method roots as untypable-dispatch incomplete outright.

   Re-run this study's scripts; Stage 2 needs sensitivity ≥ 0.9 first.
3. **Where impact already earns its keep** (*inferred* from Q2): transitive questions
   on module-level functions, where it reaches 0.807 F1 against grep's 0.470 at about
   5% of the tokens, as a Python API used *together with* grep, never instead of it.

## Not tested

Classes as targets, TypeScript/Go/Rust, repositories larger than pytest, and agents
(Stage 2).

## Reproduce

```bash
S=docs/phase-6/graph-navigation/scripts
uv run python $S/dump.py                                     # index, entity spans
~/Projects/oxide-eval-data/oxide-graph-eval/jedi/venv/bin/python $S/oracle.py   # targets, truth
TIKTOKEN_CACHE_DIR=~/Projects/oxide-eval-data/tiktoken uv run --with tiktoken python $S/arms.py
python3 $S/analyze.py
```

Data (stores, entity dumps) lives in `$OXIDE_GRAPHNAV_DATA`
(default `~/Projects/oxide-eval-data/oxide-graphnav-eval`), outside git.

References reviewed: CodeGraph's adaptive output budget and completeness signal;
GitNexus `impact` (depth groups with breakage labels, `minConfidence`, empty means
UNKNOWN); SCIP `Occurrence.enclosing_range` and `Relationship`; LSP call hierarchy
(`fromRanges`, partial results only by `partialResultToken`); CodeScaleBench (paired
with/without-tool runs, curated ground truth, calibration gates).
