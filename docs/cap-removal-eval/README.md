# #43 cap removal at matched tokens — STOPPED (unvalidated lead)

Status: **stopped before any task was scored; the lead stays unvalidated.** Production is unchanged.

- The protocol (`PROTOCOL.md`, sha256 `b8788652…`), the generation, run and score scripts, and
  `prereg.sha256` were frozen at `b400c13`, before any fresh task existed.
- Fresh-task generation was stopped by decision on 2026-10-04, during the held-out stage: raw
  candidates had been generated for httpx, requests, flask and ripgrep. No ContextBench draw, no arm
  run and no score exists. The partial cache (`~/.cache/oxide-capremoval-eval`) is machine-local.
- The lead itself is unchanged: on #42's 149 tasks (in-sample, not gated), B vs A gave +0.0527
  [+0.0137, +0.0975] coverage and +13.5 % rel/used at matched tokens, held-out-driven
  (`docs/treeselect-eval/README.md` §2).
- Why stopped: work moves to an index-time repository tree (TreeIndex/TreeRouter,
  `docs/treeselect-eval/pageindex-notes.md`). That may replace selection and allocation as they stand,
  instead of tuning the current caps.
- To resume, rerun `scripts/gen_all.sh b400c13`. It is idempotent per stage; the protocol is unchanged.
