# Phase 3.1 recommendation

Governing rule from the phase brief: **a transport earns its place only
if it improves agent utility. Protocol correctness alone is not product
value.**

## 1. B vs D (the phase's primary comparison)

| Metric | B (CLI) | D (MCP + guidance) | Winner |
|---|---|---|---|
| Bucket-A activation (muse-spark, n=12) | 50% | **83%** | D |
| Bucket-B activation (muse-spark, n=3, low-n) | **100%** | 0% | B (small-n) |
| Bucket-C false positives | 0% | 0% | tie |
| Discovery efficiency: avoidable exploration *before* activation (Bucket A, mean/run) | 0.6 | **0.1** | D |
| Coding success | 3/3, 3/3 | 3/3, 3/3 | tie |
| Persistent context cost | ~183 tokens | ~293 tokens | B |
| Client portability | Needs a Skill mechanism (Codex has none) | Self-describing via `tools/list`, works identically everywhere `rmcp` connects | D |
| Maintenance cost | Already shipped, stable since Phase 2.2 | New surface, pinned `rmcp =3.1.4`, its own upgrade contract (protocol.md §1 of the phase brief) | B |

**D wins the behavioral comparison decisively on the metric this phase
was specifically designed to discriminate on** (Bucket-A activation, not
saturated on this model) **and on discovery efficiency**, while **tying
on correctness and false positives**. B wins on raw persistent-token cost
and has a maintenance-cost edge from already being shipped. Bucket-B's
reversal (100% CLI vs 0% MCP) is real in the data but statistically thin
(n=3) and should not be allowed to outweigh Bucket-A's much larger,
cleaner signal (n=12, non-saturated, directly designed to discriminate).

**Conclusion: on this evidence, MCP (condition D) shows a real behavioral
advantage over CLI (condition B) that CLI's lower context cost and
maintenance-cost head start do not offset** — the activation gap (83% vs
50%) is large enough, on a metric chosen specifically because it wasn't
saturated, to call this a genuine finding rather than noise. This is
**"MCP wins"** per the phase brief's decision categories (§20): comparable
correctness with materially better activation and discovery efficiency.

**Caveat stated plainly, per the phase brief's anti-forcing instruction**:
this is one model family (muse-spark) on one task set. Phase 2.2/2.3's own
CLI variant-tuning work found activation rates are highly model-dependent
(gpt-5.6-luna saturated at 100% for every CLI variant tested). It is
plausible a different model would show CLI and MCP much closer together,
or even reversed. This conclusion should be re-checked before being
treated as settled across the model landscape OXIDE's users actually run.

## 2. Condition E: should both transports be exposed simultaneously?

**Yes, cautiously — the specific failure this question exists to catch
did not occur.** `transport-selection.md`: 0/55 condition-E runs called
both transports on the same task (`BOTH_REDUNDANT`: 0%,
`BOTH_USEFUL`: 0% — the two never co-occurred at all). The model that had
both available strongly preferred MCP (49% MCP_ONLY vs 5% CLI_ONLY) even
under CLI-flavored persistent instructions, but never combined them
wastefully.

**The real cost of E is not runtime redundancy, and — corrected from an
earlier draft of this report — it is not discovery-inefficiency either.**
E carries the highest baseline token floor of any condition (~401 tokens:
CLI's ~183 + MCP's ~218, unreduced because E's AGENTS.md text was
deliberately left CLI-only and un-deduplicated against MCP's schema —
protocol.md §4's design choice, `context-economics.md`), paid whether or
not either transport ends up used (25/55 condition-E runs used neither).
**An earlier version of this report called E "the least tool-call-
efficient condition" using raw total tool-call counts (6.3 vs D's 4.0).
That was the wrong metric**: once pre-activation exploration is separated
from legitimate post-retrieval source reading (`results.md` §6), E's
*avoidable exploration before OXIDE activates* is 0.3 calls/run — nearly
as low as D's 0.1, and far below B's 0.6 or C's 1.3. E's higher total call
count is overwhelmingly the same healthy "read the file OXIDE pointed to"
behavior seen everywhere else in this phase (`failures.md`), not wasted
searching and not a second call on the other transport.

**Recommendation**: exposing both is not the redundancy hazard the phase
brief was concerned about, on this evidence, **and it is not a discovery-
efficiency hazard either** — E activates about as cleanly as D does.
Given D alone already wins the B-vs-D comparison (§1) and E does not
clearly beat D on activation (85% vs 83%, within noise at these n) or on
genuine discovery efficiency (0.3 vs 0.1, both low, difference not
material at this n), **the deciding factor is context cost, not
behavior**: E's ~401-token persistent floor vs D's ~293 is real money paid
on every session with no corresponding behavioral upside this phase could
detect. There is no evidence in this phase that adding CLI on top of MCP
improves *outcomes* enough to justify that added persistent cost, for a
harness that already has MCP available — but the case against E is
narrower than originally stated, resting on cost alone, not on any
efficiency or redundancy defect. E only clearly helps a harness that would
otherwise be CLI-only (Codex, which has no Skill mechanism at all —
client-compatibility.md) by giving it an MCP
fallback path.

## 3. Does MCP earn its complexity overall?

**MCP wins**, on this phase's evidence, against the specific bar the
brief sets: comparable-or-better correctness (tied, 100% coding success
both ways) with materially better activation (83% vs 50%), better
discovery efficiency (fewer tool calls to the same outcome), and better
client portability (self-describing, no Skill mechanism required — works
on Codex where a Skill-based CLI integration structurally cannot). CLI
retains a real edge on raw persistent-token cost (~183 vs ~293) and on
being an already-shipped, already-tuned surface with three prior
validation phases behind it (2.1-2.3) versus MCP's one prior evaluation
phase (2.1, pre-rmcp) plus this one.

This is not "MCP wins because we migrated to rmcp" — the rmcp migration
itself changed almost nothing measurable (schema size within 3 characters
of the pre-migration adapter's combined total, per protocol.md §1); the
win comes from real agent behavior on real tasks, on the metric this
phase was specifically designed not to saturate.

## 4. What should NOT be concluded from this phase

- **Not** that CLI should be removed. Codex has no Skill mechanism; some
  harnesses may not support MCP servers at all or may add their own
  friction to registering one. B is a proven, lower-cost fallback.
- **Not** that this generalizes across all models. Bucket B's reversal
  and Phase 2.3's saturated-gpt-5.6-luna finding are both live warnings
  that model choice changes these numbers, sometimes a lot.
- **Not** that condition E is harmful. It produced zero redundant
  retrieval; its cost is a real but modest context tax, not a behavioral
  failure.
- **Not** that MCP's error-handling or lifecycle behavior is fully
  validated with real agents — `failures.md` records that the agent-
  facing half of §16 (error fallback under a real agent, not the scripted
  client) was not exercised this phase.

## 5. Recommendation for the next phase

1. **Adopt MCP as a co-equal, not replacement, transport.** Ship both,
   informed by condition E's clean result — but consider trimming E's
   context cost by deduplicating the AGENTS.md text against the MCP
   schema (a combined, transport-agnostic wording) rather than the
   current unmodified-CLI-text-plus-full-MCP-schema stack, since D alone
   already gets most of the behavioral benefit at lower persistent cost
   than E.
2. **Re-run the B-vs-D comparison on at least one more model family**
   before treating "MCP wins" as settled — Bucket A's clean, non-
   saturated result on muse-spark is the strongest evidence in this
   phase, and a second model would tell us whether 83-vs-50 is a real
   effect size or an artifact of this one model's particular tool-calling
   preferences.
3. **Exercise real-agent error fallback** (deleted/corrupted index,
   non-repo path) with a real agent watching, not just the scripted
   client — the gap flagged in `failures.md`.
4. **Investigate Bucket B's reversal** with more reps (n=3 per condition
   is too thin to act on) before deciding whether MCP's guidance wording
   needs subsystem-style examples the way the CLI skill body already has.
