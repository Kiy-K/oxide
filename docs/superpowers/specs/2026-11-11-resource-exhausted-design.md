# A distinct error for a full read buffer pool (intent spec, 2026-11-11)

Status: written after the implementation to meet the tier-2 rule in
`docs/agents/workflow.md` (which asks for it before implementing). It records the intent
and the behavior the code and tests must keep, and was reviewed by a zero-context agent
after it was drafted. Measurements are in
`docs/decisions.md` (row `resource-exhausted`).

## Problem

LadybugDB raises a plain `RuntimeError` ("Buffer manager exception: ... The buffer pool is
full and no memory could be freed!") when a read or an open needs more than the 128 MiB
read pool (`BUFFER_POOL`). OXIDE mapped every native read failure to `store_corrupt`
(ADR-0011), so exhaustion looked like damage: the advice was "rebuild the index", which
cannot help, and nothing told a caller that the index was intact (issue #55 hit this on
`Repository.impact`).

## Intent

1. A full read buffer pool raises a new `ResourceExhausted`, code `resource_exhausted`,
   whether it happens on a read of an open view, while opening the graph database or the
   lexical index, or inside `index()` when it opens the current generation.
2. Everything else stays as it was: any other native failure is `StoreCorrupt` and reads
   fail closed. A damaged store is not expected to match the discriminator, which is a
   message substring, so this is evidence, not a guarantee: about 240 corruption trials
   (pages overwritten or truncated) never produced `resource_exhausted` (`docs/decisions.md`,
   throwaway scripts).
3. `resource_exhausted` is not damage. The generation stays current and nothing is
   rebuilt. `index()` raises it before it publishes, from `Store.is_current` (unchanged
   worktree) or `Store.previous` (changed worktree). This is a behavior change: before,
   an intact generation that could not be opened made `previous()` return `None` and
   `index()` rebuild from scratch; now such a repository cannot be re-indexed until the
   pool problem is resolved, because a rebuild does not cure a full pool.
   `ResourceExhausted` is therefore not a `StoreCorrupt` subclass (a subclass would be
   caught by `Store.is_current` and `Store.previous`, which would start a rebuild).
4. The advice never says to re-index. It says to retry, to close and reopen a long-lived
   `Repository` first, and to report it if it recurs on a fresh open (one read or the open
   itself may need more than the pool; reopening does not cure that). The MCP server does
   not retry itself: it evicts and closes the handle, and the caller's retry reopens.
5. The public API surface is otherwise unchanged: no new exported name (only `OxideError`
   is exported from `oxide`), no store schema, derivation or payload change, no pool size
   change. Callers that catch `OxideError` or branch on `.code` see one new code. A caller that
   catches `StoreCorrupt` to mean "the store is unreadable" no longer sees exhaustion;
   that is intended. The CLI exit code (2) is unchanged.

## Design

- `oxide.models.ResourceExhausted(OxideError)` with `code = "resource_exhausted"`, and an
  `ADVICE` entry. Both clients handle it generically by `code`: MCP prints
  `error[<code>]: <advice>`; the CLI prints `error[<code>]: <message>` and a `hint:` line
  with the advice. Neither client has a test for this code.
- `oxide.storage.read_failure(directory, error, what)` is the one classifier. It matches the
  native message phrase `buffer pool is full` (ladybug 0.21) and returns `ResourceExhausted`,
  else `StoreCorrupt`. `View._query` and both open-time `except RuntimeError` blocks in
  `View.__init__` use it. (A third block there, around `LOAD EXTENSION`, only disables the
  lexical channel and is deliberately left as it was.) It lives in `oxide.storage`, not in the public `models` module,
  because it encodes a LadybugDB message.
- Related, same mechanism as #55: `View.named` passes its limit as `LIMIT $n` instead of
  inlining it (a distinct query text keeps about 1 MiB of pool). Latent today: its only
  caller passes a constant.

## Limits and non-goals

- The native message is C++ text from LadybugDB, expected to be the same on every platform;
  only Linux was exercised.
- The discriminator is a message string, not an error type; the native error is a plain
  `RuntimeError`. A reworded message falls back to `store_corrupt`, which fails closed. A
  test using the real native message (a 64 KiB pool) fails first if a ladybug upgrade
  rewords it.
- Not changed: a full pool in `publish` still escapes `index()` as a raw `RuntimeError`;
  damaged pages can still raise `UnicodeDecodeError` or abort natively; the 128 MiB pool
  size; the lexical query (its text and limit were measured not to grow the pool).
- No attempt to make exhaustion rarer beyond the #55 and `named` parameter fixes.

## Acceptance (what the tests pin)

- Real native exhaustion (pool patched to 64 KiB) at open raises `resource_exhausted`; so
  does `index()`, for an unchanged and for a changed worktree, and a later normal open still
  finds the generation current (`unchanged`, then `built` for the changed file).
- Fake native errors on a read, at the graph open and at the lexical open: the full-pool
  message gives `ResourceExhausted`; "Corrupted page" and a buffer-manager message without
  the phrase give `StoreCorrupt`. Neither class subclasses the other (both directions are
  asserted).
- The advice contains `retry` and not `oxide index`.
- 500 distinct `named` limits (and 500 distinct adjacency allowances) do not exhaust the
  default pool and return the same results.
- `mise run verify` passes; `src/oxide/storage/ladybug.py` stays at or below 500 lines.
