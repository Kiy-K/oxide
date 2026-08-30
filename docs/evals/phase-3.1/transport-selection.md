# Phase 3.1 transport-selection behavior (condition E)

This is the phase's core novel contribution — Phase 2.1 already evaluated
MCP alone, Phase 2.2/2.3 already evaluated CLI alone; only condition E
(both transports registered simultaneously) answers "do they complement
or duplicate."

## Headline result

Across all 55 valid (non-timed-out) condition-E runs, spanning both
Bucket A/B/C navigation (muse-spark) and both coding tasks
(gpt-5.6-luna):

| Outcome | Count | % |
|---|---:|---:|
| MCP_ONLY | 27 | 49% |
| NONE (native tools only) | 25 | 45% |
| CLI_ONLY | 3 | 5% |
| **BOTH (redundant retrieval)** | **0** | **0%** |

**Zero condition-E runs called both transports on the same task, in
either model, in either task type (navigation or real coding).** Every
single run that used OXIDE at all committed to exactly one transport for
the whole session. This directly answers the phase's central question:
`BOTH_REDUNDANT` (the failure mode the phase brief specifically warns
against — "oxide context via CLI → MCP context same task → grep same
symbols") **did not occur once in 55 runs**.

## The model overwhelmingly prefers MCP over CLI, even under CLI-flavored guidance

Condition E's AGENTS.md text is E1, unmodified — literally instructs
`oxide context` / `oxide search` as bash commands, with no mention of an
MCP tool by name (protocol.md §4, deliberate design choice). Despite that,
when OXIDE was used at all, the model chose the **native MCP tool call**
over the **CLI command the instructions actually describe** by roughly
9-to-1 (27 MCP_ONLY vs 3 CLI_ONLY). The 3 CLI_ONLY cases were all in the
**coding tasks** (gpt-5.6-luna), not navigation (muse-spark) — on
navigation tasks specifically, CLI_ONLY never happened at all under
condition E (0/25 nav runs that used OXIDE):

| Task type / bucket | MCP_ONLY | NONE | CLI_ONLY |
|---|---:|---:|---:|
| nav, Bucket A (muse-spark) | 17 | 3 | 0 |
| nav, Bucket B (muse-spark) | 3 | 2 | 0 |
| nav, Bucket C (muse-spark→luna mix) | 0 | 20 | 0 |
| coding (gpt-5.6-luna) | 7 | 0 | 3 |

This is a genuine, somewhat surprising finding: **agents prefer a
first-class MCP tool call over a documented CLI invocation, independent
of which one the persistent instructions actually describe.** A tool-
calling model's native function-call mechanism appears to be a lower-
friction path than "run this shell command," even when the shell command
is the one spelled out in AGENTS.md. This held on muse-spark (navigation)
and largely held on gpt-5.6-luna (coding — CLI still won 3/10 times, more
than on navigation, but MCP still led 7/10).

## No condition-E run fell back to native tools *after* trying OXIDE unsuccessfully

Every `NONE` outcome in the table above reflects the same missed-
activation pattern already seen in conditions B/C/D (the model went
straight to native `grep`/`read` and never attempted OXIDE at all) — not
a case of trying OXIDE, getting a weak result, and falling back. This
matches the phase brief's `NO_OXIDE` category cleanly, distinct from
`BOTH_REDUNDANT`. No condition-E run exhibited the "unnecessary fallback
to grep/read after both [transports]" pattern the brief also warns about,
because no run used both in the first place.

## Classification against the phase brief's five categories

| Category | Definition met? | Count |
|---|---|---:|
| `MCP_ONLY` | Used MCP, never CLI | 27/55 |
| `CLI_ONLY` | Used CLI, never MCP | 3/55 |
| `BOTH_USEFUL` | Used both, each contributing new evidence | **0/55 — never occurred** |
| `BOTH_REDUNDANT` | Used both, overlapping, no new hypothesis | **0/55 — never occurred** |
| `NO_OXIDE` | Neither transport used | 25/55 |

`BOTH_USEFUL` is also unobserved — not just the redundant failure mode,
but the theoretically desirable "complementary" pattern the brief also
asks about. On this task set, the two transports were never combined at
all, productively or not.

## What this means for condition-E's viability

The phase brief's concern — "OXIDE must not become a Context Engine that
causes agents to retrieve the same context twice" — **is not observed in
this dataset**. Exposing both transports simultaneously did not cause
duplicate retrieval in a single run. The cost of exposing both is not
runtime redundancy; per `context-economics.md`, it is a **persistent
context tax paid on every session regardless of which transport (if
either) ends up used** — condition E carries the highest persistent
token floor of any condition (~401 tokens: CLI's ~183 + MCP's ~218),
because E's AGENTS.md is the unmodified CLI text with no deduplication
against the MCP schema that's also always present. That persistent cost
is paid whether or not either transport activates (25/55 condition-E runs
used neither).

## Caveat

This is a single task set, two model families (one per task type due to
the mid-batch switch — protocol.md §4.5), and a mid-sized sample (55
condition-E runs). "Zero redundant retrieval observed" is a strong result
on this evidence but should be read as "not observed here," not proven
impossible in general — a different, more retrieval-hungry model or a
harder task (where a first attempt via one transport genuinely fails and
a rational agent should try the other) could still produce
`BOTH_USEFUL` or `BOTH_REDUNDANT` behavior this phase's task set never
exercised.
