# Julia-1 evidence judge — development protocol (written before any Julia output on OXIDE pools)

Frozen: 2026-09-27, before julia_score.py ran on any OXIDE candidate.

## Data (all OLD; development only)
- selection split S: cb (21, human line gold), heldout (63, edit-locus gold), dev (70, symbol-span gold)
- confirmation split C: bcd (37, symbol), ca (19, symbol), c5 = ch5 fresh set (61, edit-locus)
- Pools: ch0 (production) trace pool = post-dedup `kept`, pre-floor; snippet = 350-token capped render.

## Formulations (exactly these; wording fixed in julia_score.py)
- J1 noul P(true), J2 choice over pool (hash order, <=20/call, 96-token snippets), J3 score rubric E[s]/3.
- Task text cut to 256 Julia tokens for all forms.

## Candidate labels (automatic, from gold only; no manual labels are made)
- gold-bearing (snip): capped snippet span ∩ gold lines ≠ ∅  (primary label)
- gold-bearing (sym): full symbol span ∩ gold lines ≠ ∅
- non-gold same-file-as-gold (possible support) / non-gold other-file

## Integration rules (research arms in context.rs)
- R1 (ch 8): within role, order by Julia score desc, then OXIDE score, then id.
- R2 (ch 9): drop candidates with Julia score < tau (if all would drop, keep all).

## Selection rule (decided now)
1. For each form, run R1 on S. Primary: mean Δ gold-line coverage vs ch0; must not lower
   mean rel/used efficiency by >5 % relative. Supporting: within-task AUROC vs OXIDE score order.
2. Pick the form with the highest R1 Δ coverage on S (ties -> higher within-task AUROC).
3. tau for R2 on the chosen form: pick from the grid {0.05, 0.1, 0.2, 0.3, 0.5} (score scale of
   the form) the value maximizing Δ coverage on S subject to efficiency ≥ −5 %; if none improves
   coverage, R2 is dropped.
4. Check the chosen form + rule on C. Report both. Freeze before any fresh data is generated.
If no form/rule improves S final-pack coverage, the dev screen is negative and is reported as such.
