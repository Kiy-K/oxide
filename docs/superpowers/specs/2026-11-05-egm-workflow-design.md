# Risk-tiered review (Elephant-Goldfish adaptation) — design

Date: 2026-11-05. Status: retired 2026-11-08. The Claude review workflow was removed; Cursor reviews PRs. `scripts/review_tier.py` remains local guidance only.

## Intent

Agents already work autonomously here (`AGENTS.md`: implement, `mise run verify`, commit without
asking). What is missing is *proportional independent review*: `claude-code-review.yml` reviews every
PR identically, and nothing tells an agent when a change deserves a zero-context second opinion or a
written intent first.

The Elephant-Goldfish Model (EGM) supplies the pattern: a context-rich author (Elephant) and
zero-context reviewers (Goldfish) that cannot share the author's blind spots
([architecture.md](https://github.com/google/elephant-goldfish-model/blob/main/docs/designs/architecture.md),
[blog](https://opensource.googleblog.com/2026/10/introducing-elephant-goldfish-model-plugin.html)).
EGM itself requires a human on every design and forbids autonomous PRs; OXIDE keeps independent review
proportional to risk while allowing agents to implement and commit without per-change human approval.

## Goals

- Review depth is a deterministic function of the diff's paths, not of the author's judgment.
- Reuse what exists: `mise run verify` (automated gate), `docs/review/*.md` (Goldfish rubrics),
  `docs/superpowers/specs/` (design docs), the `claude-code-review` workflow.
- Tier 0 changes cost nothing extra.

## Non-goals

No new skills, personas or EGM plugin install. No drift-report automation. No changes to `verify`, baselines or fixtures.

## Tiers

| Tier | Paths | Required |
|---|---|---|
| 0 | Documentation, excluding agent/review/spec policy | `verify` |
| 1 | Tests, ordinary source/configuration paths and unknown paths (fail upward) | tier 0 + zero-context diff review |
| 2 | Public API, capture/storage/core policy (not `core/languages/`), frozen evaluation, test harness, dependencies, CI/agent tooling/configuration, review classifier, agent instructions and architecture/review specs | tier 1 + written intent spec for features/behavior changes (not fixes restoring documented behavior) |

Tier is the maximum over the changed paths. `scripts/review_tier.py` computes it and names the
`docs/review/` rubric files that apply; `mise run tier` runs it.

## Flow

- **Goldfish diff review (tier ≥ 1):** before committing, dispatch a subagent with no conversation
  context, the diff, and the printed rubric files. Fix BLOCKER and MAJOR findings.
- **Goldfish spec review (tier 2 features):** write intent (what, why, non-goals, files) in
  `docs/superpowers/specs/`; an independent reviewer restates it, lists gaps and failure modes,
  and confirms implementation readiness. Resolve, then implement.
- **CI:** `pull_request_target` runs the classifier from the PR base's trusted script and fetches the
  PR head only as diff data; if the script is absent during rollout, the PR defaults to tier 2. The
  review job fails closed on classification/action/structured-result failure, unresolved BLOCKER/MAJOR
  findings, or a missing/inadequate tier-2 feature spec. `verify` stays the automated test gate.
- **Human:** reviews at merge. Agents may publish a scoped branch and open a PR after the required gates pass; merge remains a human decision.

## Approaches considered

- **A policy only:** cheap, but self-classification is the failure mode and CI still reviews everything.
- **B classifier + CI gating:** deterministic, but leaves agent-side review undefined.
- **C = B + two paragraphs in `workflow.md` (chosen):** one stdlib script, ~30 doc lines, no new skills.

## Risks

Path-based tiers can under-rate documentation-only behavior changes and conservatively over-rate
some core edits. Tier-2 spec adequacy and semantic review are judgment-based and need the reviewer
to be correct; structured results ensure failures and unresolved findings block, but cannot guarantee reviewer correctness.
