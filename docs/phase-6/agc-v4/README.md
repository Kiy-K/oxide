# AGC v4: one tool or two, natural vs forced use: STOP at stage 1

**Preregistered decision: STOP.** Stage 1 met neither continuation signal
([preregistration.md](preregistration.md) § Design; hashes in
[results/freeze.txt](results/freeze.txt)). In detail:

- **Adoption signal not met:** S and T each used a navigation mode in 2 of 6 structural
  runs (0.33, against the 0.50 required).
- **Utility signal not met:** F succeeded in 6 of 8 runs, the same as B.

So the study ends at 32 scored runs, as preregistered. Earlier verdicts are unchanged:
AGC v1 REJECT, v2 REJECT, v3 PROMOTE G.
[ADR-0013](../../adr/0013-graph-navigation-in-oxide-search.md) has no supporting evidence
from this study and stays Proposed. Nothing in `src/oxide` changed.

All numbers below are measured; statements marked *inferred* are interpretation.

## Stage 1 (1 rep: 8 tasks × 4 arms, 0 technical failures, all schemas present, F compliance 8/8)

| | B grep/read | S single `oxide_search` | T `oxide_search` + `oxide_context` | F single, first call forced |
| --- | --- | --- | --- | --- |
| Success | 6/8 | 6/8 | 3/8 | 6/8 |
| Macro F1 | 0.968 | 0.929 | 0.868 | 0.917 |
| False claims / run | 0.25 | 0.25 | 0.38 | 0.25 |
| CPS ratio to B | 1 | 1.14 | 2.26 | 1.47 |
| Tokens / seconds ratio | 1 / 1 | 1.56 / 0.91 | 1.39 / 0.91 | 2.37 / 1.08 |
| Runs calling OXIDE | – | 3/8 | 2/8 | 8/8 |
| Navigation mode in structural runs | – | 2/6 | 2/6 | 2/6 |
| OXIDE on lookups | – | 0/2 | 0/2 | 2/2 (forced) |
| Calls by mode (search / context / callers / outline) | – | 1 / 0 / 4 / 1 | 0 / 1 / 1 / 1 | 10 / 1 / 4 / 0 |
| First-step tokens over B | 0 | 615 | 702 | 633 |
| Cost vs B: runs without / with an OXIDE call | – | 1.03 / 1.37 | 1.15 / 1.02 | – / 1.50 |

Per-task results and false claims are in [results/stage1.json](results/stage1.json) and
[results/runs.json](results/runs.json). Notes:

- **The selected tasks were mostly grep-solvable.** B solved 6 of 8 (on v3's tasks it
  succeeded in 57% of runs): the lookups, overrides (O1), and both callers tasks (C1, C2).
  Only I1 (flask `get_debug_flag`, 8 items, two hops) and E1 (callees) defeated it.
  - *Inferred:* how hard the task is for grep decides whether the graph pays, as in v2
    (REJECT) against v3 (PROMOTE). This rule produced easier tasks than v3's
    dispatch/impact rule.
- **When unguided, the agent stays selective.** S and T never used a navigation mode on a
  lookup, and used one in a third of structural runs. Where they did, it didn't change
  the result:
  - S called `callers` 4 times on I1 and still failed it, at 1.2× B's tokens;
  - T used an outline on E1 and failed it.
- **The forced-use diagnostic didn't test graph utility.** F only had to call
  `oxide_search` first, and it mostly chose `mode=search` (10 of 15 calls). That forced
  the tool, not the graph. v3's F forced the relation itself (`relation=` on the named
  symbol) and showed utility. This is a design limitation of v4.
- **Single vs split, with no decision.** At n = 8 per arm the shapes can't be separated.
  T's 3/8 comes from 4 failures with F1 ≥ 0.67, one or two items off each, which is
  within single-run noise (*inferred*). Navigation use was identical (2/6 each), so this
  study shows no discoverability advantage for a separate tool.
- **Exposure.** Listing the single shape adds 615 first-step tokens and the split shape
  702. Runs that never called the tool cost 1.03× (S) and 1.15× (T) of B.

## Why D beat G in AGC v3 (post-hoc; v3's verdict unchanged)

[scripts/v3_dg.py](scripts/v3_dg.py) → [results/v3_dg.json](results/v3_dg.json):

| | D (unguided) | G (guided) | F (forced) |
| --- | --- | --- | --- |
| Successes / 21 | 16 | 14 | 18 |
| OXIDE calls / run | 1.76 | 3.33 | 3.00 |
| Steps / run | 5.1 | 6.0 | 5.3 |
| Tokens / run | 19 511 | 24 170 | 24 865 |
| grep calls / run | 1.86 | 0.86 | 1.05 |
| Answers listed / run | 5.33 | 5.81 | 4.19 |
| Precision / recall | 0.858 / 0.990 | 0.808 / 0.981 | 0.919 / 0.972 |
| Graph calls at depth ≥ 2 | 100% | 89% | 83% |
| Graph calls on a symbol other than the task's | 58% | 70% | 64% |

- **Success:** the D–G difference is not significant (Fisher p = 0.73). D won I1 1–0 and
  D3 3–2.
- **Cost:** the difference is real. G's CPS was 1.33× D's (95% CI 1.10–1.77, bootstrap
  over tasks).
- **Mechanism:**
  - The guidance said to call callers first and not to grep again. G doubled its graph
    calls, halved its verification greps and listed more answers. Precision fell at the
    same recall.
  - On I1, G repeated depth-2 walks from intermediate callers and reported third-hop
    functions.
  - Depth alone doesn't explain it: unguided D also picked depth ≥ 2 in every graph call.
- *Inferred:* "call first, don't verify" guidance hurts. ADR-0013 ships no usage
  guidance and no fixed depth.

## NetworkX review (530c6c5 against 0381565)

[../networkx-v1/scripts/review.py](../networkx-v1/scripts/review.py) →
[../networkx-v1/results/review.jsonl](../networkx-v1/results/review.jsonl). Fresh
interpreters, one implementation on the path each, 5 repositories, 3 index repetitions.

| Repo | Parity (snapshot, derivation, 400-walk hash) | Index s, before → after (median) | Peak RSS MB, before → after | 400 walks, s |
| --- | --- | --- | --- | --- |
| flask | identical | 0.580 → 0.622 | 152.5 → 163.9 | 2.19 → 2.08 |
| requests | identical | 0.419 → 0.472 | 147.5 → 158.2 | 1.75 → 1.87 |
| pytest | identical | 1.448 → 1.506 | 184.6 → 196.1 | 3.56 → 3.73 |
| xarray | identical | 1.660 → 1.724 | 187.3 → 199.1 | 6.52 → 6.08 |
| seaborn | identical | 1.220 → 1.230 | 172.5 → 183.7 | 3.00 → 3.09 |

- **Behavior is identical.** Snapshot ids, derivation ids, and the full output of 2 000
  impact walks match on all 5 repositories.
- **Import overhead:** none at `import oxide` (142.5 ms before, 138.1 ms after; NetworkX
  is not loaded).
  - Indexing a Python repository now loads NetworkX, for `Dispatch.subclasses`. That
    costs +10 to +64 ms per index (+1% to +13%, largest on the smallest repositories) and
    about +11 MB of peak RSS.
  - The first impact walk in a process pays the same import. Walk time is unchanged
    within noise.
- *Recommendation (inferred):* the subclass closure saves about 8 lines and costs every
  Python index the import and 11 MB. The SCC on the walk projection is the change that
  carries its weight, and AGENTS.md now names NetworkX for graph algorithms.
  - The options are to accept the index-time cost, or to keep NetworkX out of indexing
    and use it only for walks and future algorithms.
  - This is not changed here.

## Next evidence-based decision

1. **Don't promote.** Leave ADR-0013 Proposed, and don't implement the new modes. v4 gives
   no evidence that agents pick a navigation mode naturally, or that it improves answers,
   on tasks grep can mostly answer.
2. **The open question is narrower than "one tool or two".** It is whether natural use
   appears on tasks that are hard for grep (v3-type dispatch and multi-hop impact):
   - v3 showed utility there, and 67% natural use with a dedicated tool.
   - v4 showed neither on easier tasks.
   - A follow-up should select tasks by v3's rule (or by B's measured failure on a
     disjoint pilot set). It should force the *mode* in its diagnostic, not just the
     tool, and compare S and T with enough repetitions to separate them.
3. **For NetworkX,** decide whether `Dispatch.subclasses` stays on NetworkX (the measured
   cost is above).

## Limitations

- **Stage 1 only:** 8 runs per arm, so single-run noise is large, and the shapes can't be
  ranked.
- **One client and one model:** OpenCode with gpt-6-luna; the tool views are AGC v2's,
  not a `Repository.impact` implementation.
- **The cost proxy and grading are v2's.** TypeScript citations needed a widened citation
  pattern (`trial.py` `answers`/`owner`).
