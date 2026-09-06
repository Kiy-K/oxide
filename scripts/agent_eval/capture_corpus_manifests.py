#!/usr/bin/env python3
"""Capture one corpus manifest per pinned-task worktree (see
`examples/corpus_manifest.rs` for what a manifest contains and why).

Run once per embedding provider, immediately after that provider's scoring
run and before switching providers, with no indexing in between. The two
`corpus/` subdirectories are then required to be byte-identical
(`diff -rq A/corpus B/corpus`).

What that does and does not establish. It establishes that the indexes
**as they stood at capture time** held the same symbols, spans, hashes and
`embed_text` — which is the confound worth ruling out, since `embed_text`
includes `Symbol::references` and those can shift between indexing runs. It
does not by itself prove the scoring run consumed that same state; that
linkage is procedural (capture immediately after scoring, nothing indexes in
between) and is corroborated by `PROVENANCE.tsv`, written beside `corpus/`,
which records each worktree's own `embedder`/fingerprint/version meta as the
index actually recorded it. `PROVENANCE.tsv` is deliberately outside
`corpus/`: it is expected to *differ* between providers, and the corpus diff
must stay a statement about the embedding input alone.

That comparison is scoped to **two runs on the same machine**, which is the
only situation it is used in — each manifest's header records the canonical
absolute worktree path as provenance, so manifests captured on different
machines (or under a different cache root) differ in that line by design and
are not comparable byte-for-byte without normalizing it away.

Never indexes and never embeds — it reads the index each run already built,
so capturing cannot itself change the state being captured.

Usage: capture_corpus_manifests.py --out DIR
"""
import argparse
import hashlib
import sqlite3
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import contextbench_run as cb  # noqa: E402

PIN = cb.ROOT / "eval-agent/results/tier_a_instances.txt"
BIN = cb.ROOT / "target/release/examples/corpus_manifest"
META_KEYS = ("embedder", "dim", "schema_version", "extraction_version", "embedding_fingerprint")


def index_meta(repo: Path) -> dict:
    """The index's own record of which provider produced it — read from the
    index, never reconstructed from the environment, which can disagree."""
    con = sqlite3.connect(repo / ".oxide" / "index.db")
    try:
        rows = dict(con.execute("SELECT key, value FROM meta"))
    finally:
        con.close()
    return {k: rows.get(k, "") for k in META_KEYS}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    assert BIN.exists(), f"{BIN} missing — cargo build --release --example corpus_manifest"
    out = Path(a.out)
    corpus = out / "corpus"
    corpus.mkdir(parents=True, exist_ok=True)

    allow = {i.strip() for i in PIN.read_text().splitlines() if i.strip()}
    tasks = [t for t in cb.load_tasks() if t["instance_id"] in allow]
    assert tasks, "pin matched no tasks"

    # Several pinned tasks share one (repo, commit) worktree; key by the
    # worktree so each corpus is captured exactly once.
    seen = {}
    for row in tasks:
        repo = cb.ensure_repo_checkout(row["repo_url"], row["base_commit"])
        if repo.name in seen:
            continue
        r = subprocess.run([str(BIN), str(repo)], capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(f"corpus_manifest failed for {repo.name}: {r.stderr[:300]}")
        # The repo path is the one line that legitimately differs between
        # machines but not between providers; it is kept in the file (it is
        # provenance) and the comparison is a whole-file diff, so nothing
        # here needs to strip it.
        (corpus / f"{repo.name}.tsv").write_text(r.stdout)
        digest = hashlib.sha256(r.stdout.encode()).hexdigest()
        symbols = r.stdout.splitlines()[1].split("\t")[1]
        meta = index_meta(repo)
        seen[repo.name] = (digest, symbols, meta)
        print(f"{repo.name:<40} symbols={symbols:>7}  sha256={digest[:16]}  "
              f"embedder={meta['embedder']}", flush=True)

    # Fails closed: one capture must describe one embedding space. A mixed
    # capture would make "the two corpora match" true while the two scoring
    # runs it is meant to underwrite were not each single-provider.
    #
    # Identity here is the (embedder, embedding_fingerprint) pair, not the
    # embedder name alone. The name is not the space: `EmbeddingSpaceFingerprint`
    # carries quantization, dimension, pooling, normalization and the query/
    # document prompt convention, and `update_index` re-embeds on a
    # fingerprint-only change with name and dim unchanged
    # (`service.rs::update_index_reembeds_on_a_fingerprint_only_change_same_name_and_dim`).
    # Empty meta is rejected rather than compared, since two blanks would
    # otherwise "match".
    blank = sorted(n for n, (_, _, m) in seen.items()
                   if not m["embedder"] or not m["embedding_fingerprint"])
    if blank:
        raise SystemExit(
            f"worktrees missing embedder/fingerprint meta: {blank} — provider "
            f"identity is unknowable for them, refusing to report a uniform capture"
        )
    providers = {(m["embedder"], m["embedding_fingerprint"]) for _, _, m in seen.values()}
    if len(providers) > 1:
        raise SystemExit(
            f"worktrees disagree on embedding-space identity: "
            f"{sorted(e for e, _ in providers)} — this capture does not "
            f"describe a single embedding space (compare PROVENANCE.tsv)"
        )

    (corpus / "INDEX.txt").write_text("".join(
        f"{name}\t{sym}\t{dig}\n" for name, (dig, sym, _) in sorted(seen.items())
    ))
    (out / "PROVENANCE.tsv").write_text(
        "# worktree\tembedder\tdim\tschema_version\textraction_version\tfingerprint\n"
        + "".join(
            "\t".join([
                name, m["embedder"], m["dim"], m["schema_version"],
                m["extraction_version"], m["embedding_fingerprint"],
            ]) + "\n"
            for name, (_, _, m) in sorted(seen.items())
        )
    )
    print(f"\n{len(seen)} worktrees captured -> {corpus}")
    # Print the whole validated identity, not just its name half: the
    # uniformity assertion above is over the (embedder, fingerprint) pair,
    # and showing only the name would state a weaker fact than the one
    # being claimed. The fingerprint is long, so it is shown as a digest
    # with the full value in PROVENANCE.tsv.
    space_name, space_fp = providers.pop()
    print(f"embedding space (uniform across worktrees): {space_name}\n"
          f"  fingerprint sha256: {hashlib.sha256(space_fp.encode()).hexdigest()[:32]}"
          f"  (full value in PROVENANCE.tsv)")
    print(f"compare with: diff -rq <other>/corpus {corpus}")


if __name__ == "__main__":
    main()
