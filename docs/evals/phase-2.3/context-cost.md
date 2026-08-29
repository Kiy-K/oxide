# Persistent activation-cost by variant

Same measurement convention as Phase 2.2 (`wc -c` on the actual files,
chars/4 estimate — see that phase's `context-cost.md` for why a real
tokenizer wouldn't change which variant wins at this size).

| Variant | AGENTS.md tokens | Skill frontmatter tokens (persistent) | Skill body tokens (on-demand only) |
|---|---|---|---|
| E0 (baseline) | ~63 | ~99 | ~803 |
| **E1 (winner)** | **~70** | ~99 | ~803 |
| E2 | ~95 | ~99 | ~803 |
| E3 | ~60 | ~99 | ~803 |
| E4 | ~63 | ~99 (rewritten, same length) | ~803 |

Every variant stays comfortably under the phase's ~250-token persistent-
cost ceiling (§5) — even the largest, E2, is ~194 tokens persistent
(95 + 99). The winning variant, E1, adds just **7 tokens** over the
Phase 2.2 baseline it refines (~70 vs ~63) for a measurable improvement
in activation quality (`activation-results.md` §2–3). This is about as
close to a free win as an instruction-text change gets: no new
mechanism, no new tool, a single sentence reworded.

## Why E2 and E4 don't earn their extra cost

E2 is the most expensive variant (~194 tokens persistent, plus it's the
only one with a real Bucket-C false positive — `activation-results.md`
§4) and does not lead on any quality metric (native-calls-before-oxide
1.75, behind E1's 1.42). E4's skill-description rewrite costs nothing
extra in persistent tokens (same length, different words) but also
doesn't move any metric meaningfully (2.17 native-calls-before-oxide,
essentially tied with baseline's 2.25). Neither variant should ship.

## Actual per-run token totals (informational, not the deciding metric)

Real `step_finish` token sums across the 120-run batch ranged roughly
100k–220k per run, dominated by this environment's own overhead (the same
~30 skills, `codegraph` MCP, `ponytail` plugin present in every Phase 2.2
and 2.3 run) — not a useful signal for comparing E0–E4 to each other,
same caveat as Phase 2.2's `context-cost.md`. The chars/4 table above,
not this number, is what the phase's "keep interventions tiny" gate
(§5/§9) should be checked against.
