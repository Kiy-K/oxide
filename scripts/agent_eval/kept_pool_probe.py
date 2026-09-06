#!/usr/bin/env python3
"""Per-task stage-attribution probe for the pinned Tier A set.

Answers the one question a ranked-file list cannot: when the gold file is
missing from a budgeted pack, which stage is that absence attributable to?

Note what pool absence alone does and does not mean. The pool is dumped
*after* subsumption/dedup, so a gold candidate missing from it may have been
retrieved and then subsumed — "absent from the pool" is therefore
"absent from the post-subsumption pool", never "never retrieved". The
`omitted[].why` evidence below is what actually separates those.

Two independent evidence sources, both emitted by OXIDE itself:

  * `OXIDE_DEBUG_DUMP_KEPT` writes `build_context`'s candidate pool at the
    point it is dumped. **That point is after subsumption/dedup but BEFORE
    the relevance floor** (`src/context.rs`: the dump write sits at the
    `OXIDE_DEBUG_DUMP_KEPT` block, and `---- relevance floor ----` runs
    afterwards, as do rerank, role ordering, the diversity caps and the
    budget fill). Note this contradicts `docs/embedding-profile-comparison/
    README.md`, which describes the pool as "post relevance-floor" — that
    description is wrong against the current source, and reading it the
    wrong way round would blame the floor for pool absences it cannot
    cause, and would blame ordering/budget for floor drops.
  * `ContextPack.omitted` records every dropped candidate with the stage
    that dropped it (`why`): "module subsumed by concrete symbols",
    "subsumed by overlapping symbol" (both before the dump), then "below
    relevance floor", "per-file diversity cap", "beyond primary cap",
    "beyond test cap", "over token budget" (all after it). Recording these
    for gold-file candidates makes the stage attribution direct evidence
    rather than an inference from pool membership.

This closes the methodology gap named in docs/embedding-profile-comparison/
README.md: an earlier version of this probe tested set membership over the
whole final pack, but the metric that shows regressions (R@5) is rank
position over the first five unique files — a gold file at position 6 of a
7-item pack counted as a hit there and a miss in the metric. Here
`ranked_files(...)[:5]` is replicated exactly, so `gold_in_pack_top5` below
is the same predicate `ranking_metrics.py` scores.

Read-only with respect to the index: runs `oxide context` only, never
`oxide index`, so it must be run while the index is still in the provider
state being probed.

Usage: kept_pool_probe.py --out results.jsonl
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "eval-agent"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import contextbench_run as cb  # noqa: E402
from capture_corpus_manifests import index_meta  # noqa: E402

PIN = cb.ROOT / "eval-agent/results/tier_a_instances.txt"


def ranked_files(items):
    """Byte-for-byte the same first-occurrence dedup ranking_metrics.py uses."""
    seen, out = set(), []
    for it in items:
        f = it["file"]
        if f not in seen:
            seen.add(f)
            out.append(f)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    allow = {i.strip() for i in PIN.read_text().splitlines() if i.strip()}
    tasks = [t for t in cb.load_tasks() if t["instance_id"] in allow]
    assert tasks, "pin matched no tasks"
    ox = str(cb.ROOT / "target/release/oxide")
    env = {"OXIDE_EMBED_URL": os.environ.get("OXIDE_EMBED_URL", "")}
    want_native = bool(os.environ.get("OXIDE_EMBED_NATIVE", ""))
    # Per worktree, not once for the first one. The pin spans several repos,
    # each with its own index.db and its own recorded embedder; verifying only
    # the first and then stamping its identity onto every row would attach a
    # provider label to tasks whose index was never checked — the precise
    # mislabeling this probe's `model` field exists to rule out.
    verified: dict[str, str] = {}
    staleness: dict[str, str] = {}
    model = None
    with open(a.out, "w") as sink:
        for row in tasks:
            # Fails closed on a wrong checkout, same as every other caller.
            repo = cb.ensure_repo_checkout(row["repo_url"], row["base_commit"])
            if repo.name not in verified:
                # Three checks, each catching a different failure, and none
                # claiming more than it tests.
                #
                # 1. `verify_embedder_took_effect`: wrong *kind* of provider —
                #    a binary built without --features native-embed silently
                #    falls back to the hashed embedder.
                # 2. `oxide status`'s `embedder_current`: the index is stale
                #    with respect to the provider configured right now. This
                #    is a NAME-level comparison and network-free by design
                #    (`service.rs::status`), so it catches a stale index, not
                #    a same-name/different-fingerprint one.
                # 3. The recorded fingerprint: that a space is recorded at
                #    all, and that every worktree records the same one.
                #
                # What none of them do is compare the stored fingerprint
                # against a live provider's — that needs constructing the
                # provider (a network round trip for HTTP, a model load for
                # native), which this read-only probe deliberately avoids.
                # Fingerprint-level agreement between index and provider is
                # instead guaranteed upstream, by the scoring run's own
                # `index_repo`: `update_index` re-embeds whenever the
                # fingerprint differs, name and dim unchanged.
                cb.verify_embedder_took_effect(repo, want_native=want_native)
                st = cb.sh([ox, "status", "--json"], cwd=repo, env=env)
                status = json.loads(st.stdout) if st.stdout.strip() else {}
                if st.returncode == 0 and "embedder_current" in status:
                    if not status["embedder_current"]:
                        raise RuntimeError(
                            f"{repo.name} index was built by {status.get('embedder')!r}, which is "
                            f"not the provider configured now — refusing to attach stage evidence "
                            f"to a stale index"
                        )
                    staleness[repo.name] = "ok"
                else:
                    # A *negative* answer is fatal above; an *unavailable* one
                    # is recorded, not invented either way. `oxide status`
                    # fails outright on a repo containing a non-UTF-8 source
                    # file with an indexed extension — `current_file_hashes`
                    # uses `read_to_string` while the scanner's binary sniff
                    # only rejects NUL bytes, so e.g. pylint's Latin-1
                    # `tests/functional/i/implicit/implicit_str_concat_latin1.py`
                    # takes it down even though `oxide index` and `oxide
                    # context` handle the same repo fine. Crashing the probe
                    # over an unrelated CLI bug would discard sound evidence;
                    # silently passing would claim a freshness check that
                    # never ran. Both are avoided by recording the gap.
                    reason = (status.get("error", {}).get("message")
                              or st.stderr.strip()[:120] or f"exit {st.returncode}")
                    staleness[repo.name] = f"unavailable: {reason}"
                    print(f"  note: freshness check unavailable for {repo.name} ({reason})",
                          flush=True)
                meta = index_meta(repo)
                if not meta["embedder"] or not meta["embedding_fingerprint"]:
                    raise RuntimeError(
                        f"{repo.name} has no recorded embedder/fingerprint — provider "
                        f"identity unknowable, refusing to attach stage evidence to it"
                    )
                verified[repo.name] = (meta["embedder"], meta["embedding_fingerprint"])
                distinct = set(verified.values())
                if len(distinct) > 1:
                    raise RuntimeError(
                        f"worktrees disagree on embedding-space identity: "
                        f"{sorted(e for e, _ in distinct)} — refusing to record one "
                        f"provider label across mixed indexes"
                    )
            model, fingerprint = verified[repo.name]
            gold = set(cb.Gold({
                "init_ctx": json.loads(row["gold_context"]),
                "repo_url": row["repo_url"],
                "commit": row["base_commit"],
            }).files())
            with tempfile.TemporaryDirectory() as td:
                dump = Path(td) / "kept.json"
                r = cb.sh(
                    [ox, "context", "--task", row["problem_statement"],
                     "--budget-tokens", "4096", "--json"],
                    cwd=repo, env={**env, "OXIDE_DEBUG_DUMP_KEPT": str(dump)},
                )
                if r.returncode != 0:
                    raise RuntimeError(f"context failed for {row['instance_id']}: {r.stderr[:300]}")
                pack = json.loads(r.stdout)
                # Fail closed. `build_context` writes this file whenever the
                # env var is set — an empty pool still serializes as "[]" —
                # so a *missing* file means the instrumentation did not run
                # (binary without the dump site, unwritable path, crash),
                # not that the pool was empty. Treating the two alike would
                # manufacture "gold never reached the pool" verdicts, i.e.
                # invent the exact mechanism this probe exists to establish.
                if not dump.exists():
                    raise RuntimeError(
                        f"OXIDE_DEBUG_DUMP_KEPT produced no file for "
                        f"{row['instance_id']} at {dump} — refusing to record "
                        f"an absent dump as an empty candidate pool"
                    )
                kept = json.loads(dump.read_text())
            # `kept` entries nest the symbol; pack items are serde-flattened.
            kept_files = [k["symbol"]["file"] for k in kept]
            pack_ranked = ranked_files(pack["items"])
            # Omitted ids are "<file>#<qualified_name>" and the scored metric
            # is file-level, so gold candidates match on the file part. Tested
            # by prefix rather than by splitting on "#": a left split breaks on
            # a path containing "#", a right split breaks on a qualified name
            # containing one, and neither delimiter is guaranteed absent. A
            # "<gold path>#" prefix test needs no such guarantee.
            gold_omitted = sorted({
                o["why"] for o in pack.get("omitted", [])
                if any(o["id"].startswith(f"{g}#") for g in gold)
            })
            rec = {
                "task": row["instance_id"],
                "repo": row["repo"],
                "model": model,
                "embedding_fingerprint": fingerprint,
                # "ok" when `oxide status` confirmed the index matches the
                # configured provider; "unavailable: ..." when that check
                # could not run. Never silently absent.
                "staleness_check": staleness[repo.name],
                # The question this row is about. The task id alone does not
                # pin it — a pin edited or a dataset revision bumped between
                # runs keeps the id and changes the text. `ranking_metrics.py`
                # records no query, so nothing can cross-check this today; it
                # is recorded so a future run can, and so a stale evidence
                # file is at least self-describing.
                "query_sha256": hashlib.sha256(
                    row["problem_statement"].encode()
                ).hexdigest(),
                "gold": sorted(gold),
                "kept_files": sorted(set(kept_files)),
                "kept_count": len(kept),
                "pack_ranked": pack_ranked,
                # Pool membership is PRE-relevance-floor (see module docstring).
                "gold_in_pool_pre_floor": bool(gold & set(kept_files)),
                "gold_in_pack_any": bool(gold & set(pack_ranked)),
                "gold_in_pack_top5": bool(gold & set(pack_ranked[:5])),
                "gold_omitted_reasons": gold_omitted,
            }
            sink.write(json.dumps(rec) + "\n")
            sink.flush()
            print(f"{row['instance_id']}: pool={rec['kept_count']} "
                  f"gold_in_pool={rec['gold_in_pool_pre_floor']} "
                  f"top5={rec['gold_in_pack_top5']} "
                  f"dropped_by={rec['gold_omitted_reasons'] or '-'}",
                  flush=True)


if __name__ == "__main__":
    main()
