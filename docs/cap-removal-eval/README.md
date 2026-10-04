# #43 cap removal at matched tokens

Status: **running** (fresh-task generation). Production is unchanged.

- The protocol (`PROTOCOL.md`, sha256 `b8788652…`), the generation, run and score scripts, and
  `prereg.sha256` were frozen at `b400c13`, before any fresh task existed.
- This is an independent allocator experiment: it is not TreeSelect, and it does not rerank the
  shortlist. Its result is kept as input for the future index-time tree architecture
  (TreeIndex/TreeRouter, `docs/treeselect-eval/pageindex-notes.md`).

## Interruption and resume (2026-10-04), before any task was scored

- Generation was stopped once by decision, during the held-out stage. At that point the raw
  candidates for httpx, requests, flask and ripgrep were complete (`.done`), and clap was mid-run.
- The decision was reversed, and generation resumed with the same frozen script and commit
  (`gen_all.sh b400c13`).
- `make_tasks.py` reuses an existing worktree without re-indexing it. So the two clap worktrees created
  during the interrupted run (`clap-81dea3b6`, `clap-f5ba9c51`) were removed before resuming, and the
  clap stage regenerated from an empty `raw-clap.jsonl`, as an uninterrupted run would.
- Completed stages were left untouched. No ContextBench draw, arm run or score existed at the
  interruption.
