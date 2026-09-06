#!/usr/bin/env python3
"""Pairwise per-task diff of two `ranking_metrics.py` per-task sinks.

Answers what the aggregate table cannot: for each pinned task, did the
candidate win, stay flat, or regress against the baseline — and at which
stage did the gold file survive or disappear.

Evidence discipline. Every label this script prints is derived from a
recorded stage, never inferred from plausibility:

  * `lexical`, `vec` and `hybrid` are recorded as **top-10 ranked file
    lists** (`ranking_metrics.py` calls `oxide search --limit 10`). So
    "absent from lexical" can only ever mean "absent from lexical's top 10"
    — a gold file at lexical rank 11 is indistinguishable here from one
    never retrieved. Labels say `@10` wherever that is the actual claim.
    `budgeted` is different in kind: it is the finished pack from `oxide
    context --budget-tokens 4096`, whose length is set by the budget and
    the caps, not by a `--limit`. It is read only at `[:5]`, matching the
    scored `R@5` predicate, and is never described as a top-10.
  * No label attributes *causation* to a retrieval arm. Gold appearing in
    lexical@10 is consistent with the lexical arm supplying a hybrid hit,
    but does not establish it — RRF fuses both arms and structural
    expansion contributes too, and nothing recorded says which one put the
    file in hybrid's top 5. Labels therefore report **where gold was
    observed**, and say outright that the cause is not isolated. The same
    applies in reverse: a gold file in hybrid but in neither recorded
    top-10 came from expansion *or* from a sub-rank-10 hit on either arm,
    and nothing recorded separates those either.
  * Stage claims about the budgeted pack require `kept_pool_probe.py`
    output (`--kept-*`). The pack is not a truncation of hybrid, so no
    stage can be read off the ranked lists alone. The probe supplies two
    things: pool membership at the dump point — which is **before the
    relevance floor**, so pool absence can never be blamed on the floor —
    and, decisively, `ContextPack.omitted`'s own per-stage `why` for
    gold-file candidates. Where a `why` exists it is quoted verbatim
    instead of being paraphrased into a guess.

    Joining the two is checked rather than assumed, and the check's reach is
    worth stating exactly. The probe re-runs `oxide context` rather than
    reusing the scoring run's pack, and a fixed index alone would not make
    those packs equal: the query embedding is recomputed by the live provider
    each time, and neither a remote server's floating-point behaviour nor its
    batching is guaranteed reproducible. So per task the probe's full
    `pack_ranked` must equal the sink's scored `budgeted` list, and its gold
    set must equal the sink's; any disagreement, or a scored list that fills
    the recorded 10-file cap (where truncation makes equality unknowable),
    discards that task's stage evidence.

    What that establishes is that the two invocations produced the same
    scored result on the same question — strong evidence they ran the same
    pipeline over the same state. It does not *prove* the intermediates were
    identical: only the final pack is recorded on both sides, so in principle
    a differing pre-floor pool or `omitted[].why` could still land on the
    same pack. Stage labels are read with that residual in mind. The probe's
    `gold_in_pack_any` is over its full pack, not a top-10 slice; the sink's
    `budgeted` list is what R@5 scores.

Reads only JSONL evidence; runs no retrieval and touches no index, so it is
safe to re-run against archived evidence at any time.

Usage:
  compare_per_task.py BASELINE.jsonl CANDIDATE.jsonl [--metric r@5]
                      [--kept-baseline K.jsonl] [--kept-candidate K.jsonl]
"""
import argparse
import json
from collections import defaultdict

# Every ranked list in the sink is a top-10 (search --limit 10); saying "@10"
# in a label is therefore the strongest honest claim about absence.
RANK_DEPTH = 10


def load(path):
    """Rows keyed by task, plus the single provider that produced them.

    Fails closed on mixed providers rather than labelling the file with
    whichever row happened to come last: this whole comparison is a claim
    about two specific embedding spaces, so a results file silently holding
    two of them would make every number in the report unattributable.

    Note the strength of this check. `ranking_metrics.py` records `model` as
    a *label* — for a native profile it is the index's own embedder string,
    but in HTTP mode it is `$OXIDE_EMBED_MODEL`, which omits the endpoint and
    carries no fingerprint. So this rejects two obviously different
    providers; it cannot detect two runs that share a label while differing
    in quantization, prompt convention or endpoint. The authoritative
    per-worktree identity is `PROVENANCE.tsv` from
    `capture_corpus_manifests.py`, which records `embedding_fingerprint` as
    the index itself stored it, and `kept_pool_probe.py` records the same
    fingerprint per row.
    """
    rows, models = defaultdict(dict), set()
    for line in open(path):
        r = json.loads(line)
        rows[r["task"]][r["condition"]] = r
        models.add(r["model"])
    if len(models) > 1:
        raise SystemExit(
            f"{path} mixes embedding providers {sorted(models)} — refusing to "
            f"report mixed-provider evidence under one label"
        )
    return rows, models.pop() if models else None


def load_kept(path, expect_model, ranking_path):
    """Kept-pool evidence, keyed by task, checked against the ranking run.

    Joining on task id alone would happily pair a candidate's stage evidence
    with a baseline's ranked lists, or with a stale probe run from an earlier
    provider — and every stage label would then be attributed to the wrong
    side while looking perfectly well-formed. So the provider label must
    match the ranking file's.

    Completeness is deliberately *not* required: stage evidence is per task
    and optional, a task without it simply reports `pool:?` and gets no
    stage-level label. `main` prints how many scored tasks ended up with
    usable stage evidence, so partial coverage is visible rather than
    mistaken for full coverage.
    """
    if not path:
        return {}
    rows = {}
    models = set()
    for line in open(path):
        r = json.loads(line)
        rows[r["task"]] = r
        models.add(r["model"])
    if len(models) > 1:
        raise SystemExit(f"{path} mixes embedding providers {sorted(models)}")
    got = models.pop() if models else None
    # `ranking_metrics.py` labels HTTP runs with $OXIDE_EMBED_MODEL while the
    # probe records the index's full embedder string, so the two agree
    # exactly for native profiles and only by containment for HTTP ones —
    # an HTTP label carries no endpoint and no fingerprint, so this check
    # cannot by itself distinguish two servers sharing a model name. The
    # per-task pack comparison in main() is what closes that gap: two
    # different providers would have to produce identical packs on every
    # task to slip through both.
    if got and expect_model and expect_model not in (got, got.split("@")[0].removeprefix("http:")):
        raise SystemExit(
            f"{path} was produced under provider {got!r}, which does not match "
            f"{ranking_path}'s {expect_model!r} — refusing to attach its stage "
            f"evidence to another run's rankings"
        )
    return rows


def verdict(base, cand, eps=1e-9):
    if cand > base + eps:
        return "win"
    if cand < base - eps:
        return "regression"
    return "flat"


def stages(c, kept=None):
    """Where the gold file was actually observed, stage by stage.

    Pure observation, no causal claim. `None` for the pool means it was not
    captured for this run, reported as `?` rather than as absence. `pool` is
    membership at the dump point, which precedes the relevance floor.
    """
    gold = set(c["vec"]["gold"])
    # Counts, not booleans. A task can pin several gold files, and a boolean
    # per stage cannot distinguish "the same file survived every stage" from
    # "a different gold file happened to appear at each stage" — an earlier
    # version's summary label asserted the former from evidence that only
    # supported the latter.
    hit = lambda cond, k: len(set(c[cond]["ranked"][:k]) & gold)
    return {
        "n_gold": len(gold),
        "vec@10": hit("vec", RANK_DEPTH),
        "lex@10": hit("lexical", RANK_DEPTH),
        "hyb@5": hit("hybrid", 5),
        "pack@5": hit("budgeted", 5),
        "pool": kept["gold_in_pool_pre_floor"] if kept else None,
    }


def signature(st):
    n = st["n_gold"]
    cells = [f"{k}:{st[k]}/{n}" for k in ("vec@10", "lex@10", "hyb@5", "pack@5")]
    cells.append("pool:" + ("?" if st["pool"] is None else ("+" if st["pool"] else "-")))
    return " ".join(cells)


def mechanism(c, kept=None):
    """A one-line reading of `stages`, claiming nothing it cannot support.

    Deliberately never says "rescued by lexical" or "rescued by expansion":
    presence in an arm's top-10 does not establish that arm caused the fused
    result. Stage wording for the budgeted pack comes from OXIDE's own
    `omitted[].why` where available, and otherwise names the whole span of
    stages that could be responsible rather than picking one.
    """
    st = stages(c, kept)
    n = st["n_gold"]
    vec, lex, hyb, pack, pool = (
        st["vec@10"], st["lex@10"], st["hyb@5"], st["pack@5"], st["pool"]
    )
    why = kept.get("gold_omitted_reasons") if kept else None

    # Named stages only — an earlier version called this "retained at every
    # recorded stage" while never checking lexical@10, which claimed more
    # than it tested.
    if vec and hyb and pack:
        if n == 1 or (vec == n and hyb == n and pack == n):
            return "gold held through vec@10, hybrid@5 and pack top-5"
        # Multi-gold and only partially present: the per-stage counts in the
        # signature are the claim; asserting one file's survival would not be.
        return (f"gold present at each of vec@10, hybrid@5, pack top-5, but not the "
                f"whole set ({vec}/{n}, {hyb}/{n}, {pack}/{n}) — possibly different files")

    # Budgeted-side loss. OXIDE records which stage dropped each candidate;
    # quote it rather than inferring one. Two facts can hold at once and both
    # are reported rather than one being used to rule the other out: a task
    # can have several gold files, so one may sit in the pack below rank 5
    # (a rank-position miss) while another was genuinely dropped by a stage.
    # An earlier version let `gold_in_pack_any` assert "not a drop", which
    # excluded a drop the evidence had actually recorded.
    if hyb and not pack:
        in_pack = bool(kept and kept.get("gold_in_pack_any"))
        facts = []
        if in_pack:
            facts.append("gold in the pack below rank 5")
        if why:
            facts.append(f"gold candidates dropped by: {', '.join(why)}")
        if facts:
            return "; ".join(facts)
        if pool is True:
            return ("in pool (pre-floor), absent from the pack, no recorded drop stage — "
                    "outranked within the pack")
        if pool is False:
            return "never reached the candidate pool (retrieval/expansion/subsumption)"
        return "lost between hybrid@5 and pack top-5 — stage not isolated (no pool recorded)"

    # Vector miss that hybrid nonetheless surfaced. Which arm or stage put it
    # there is not recoverable from a pair of top-10 lists.
    if not vec and hyb:
        seen = "lexical@10 also had gold" if lex else "gold in neither recorded top-10"
        return f"hybrid@5 has gold, vec@10 does not; {seen} — cause not isolated"

    if not vec and not hyb:
        facts = []
        if pack:
            # The pack is not a hybrid truncation, so it can hold gold in its
            # top 5 even with no vec@10 or hybrid@5 hit. Saying "below rank 5"
            # here would contradict the recorded pack@5 count.
            facts.append(f"gold in the pack top-5 ({pack}/{n})")
        elif kept and kept.get("gold_in_pack_any"):
            facts.append("gold in the pack below rank 5")
        if why:
            facts.append(f"gold candidates dropped by: {', '.join(why)}")
        if facts:
            return "no vec@10 or hybrid@5 hit; " + "; ".join(facts)
        lex_note = f"lexical@10 has gold ({lex}/{n})" if lex else "lexical@10 has none either"
        if pool is True:
            return f"no vec@10 or hybrid@5 hit; {lex_note}; gold did reach the candidate pool"
        if pool is False:
            return f"no vec@10 or hybrid@5 hit; {lex_note}; absent from the candidate pool"
        return f"no vec@10 or hybrid@5 hit; {lex_note}"

    return "gold in vec@10 but not hybrid@5 (fusion/ranking; cause not isolated)"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("baseline")
    ap.add_argument("candidate")
    ap.add_argument("--metric", default="r@5")
    ap.add_argument("--kept-baseline", default="")
    ap.add_argument("--kept-candidate", default="")
    a = ap.parse_args()

    base, bm = load(a.baseline)
    cand, cm = load(a.candidate)
    kb = load_kept(a.kept_baseline, bm, a.baseline)
    kc = load_kept(a.kept_candidate, cm, a.candidate)
    shared = sorted(set(base) & set(cand))
    assert shared, "no overlapping tasks"
    only_one = set(base) ^ set(cand)
    if only_one:
        print(f"# WARNING: {len(only_one)} task(s) in only one file: {sorted(only_one)}")
    print(f"# baseline={bm}  candidate={cm}  metric={a.metric}  tasks={len(shared)}")
    print(f"# stage evidence (pool + omitted[].why): baseline={'yes' if kb else 'NO'} "
          f"candidate={'yes' if kc else 'NO'} — pack-stage labels are only emitted where present")

    print(f"\n{'task':<62}{'cond':<10}{'base':>7}{'cand':>7}  verdict")
    tally = defaultdict(lambda: defaultdict(int))
    for t in shared:
        for cond in ("vec", "hybrid", "budgeted"):
            if cond not in base[t] or cond not in cand[t]:
                continue
            b, c = base[t][cond][a.metric], cand[t][cond][a.metric]
            v = verdict(b, c)
            tally[cond][v] += 1
            if v != "flat":
                print(f"{t:<62}{cond:<10}{b:>7.3f}{c:>7.3f}  {v}")

    print("\n# tally (by condition)")
    for cond in ("vec", "hybrid", "budgeted"):
        d = tally[cond]
        print(f"  {cond:<10} win={d['win']:>2}  flat={d['flat']:>2}  regression={d['regression']:>2}")

    # Refuse stage evidence for any task whose probe pack disagrees with the
    # scored pack: the two came from separate `oxide context` invocations,
    # and only equality makes the probe's stages describe the scored result.
    def agreeing(kept_rows, sink_rows, side):
        # Restricted to `shared`: a probe file may cover tasks that only one
        # of the two runs scored, and counting those would report coverage
        # against a denominator they were never part of.
        ok = {}
        for t, k in ((t, kept_rows[t]) for t in shared if t in kept_rows):
            scored = sink_rows.get(t, {}).get("budgeted", {}).get("ranked")
            if scored is None:
                continue
            # A prefix match is not enough. `gold_in_pack_any` and
            # `gold_omitted_reasons` describe the probe's *whole* pack, so two
            # packs agreeing on the sink's first ten files could still differ
            # past rank 10 and those fields would describe a pack that was
            # never scored. The sink stores `ranked` as `files[:10]`, so it is
            # the complete list only when it is shorter than that cap; at
            # exactly ten it may be truncated and equality is unknowable.
            if len(scored) >= RANK_DEPTH:
                print(f"# WARNING: {side} scored pack for {t} fills the recorded "
                      f"{RANK_DEPTH}-file cap, so full-pack equality with the probe "
                      f"cannot be established — stage evidence discarded")
                continue
            if k["pack_ranked"] != scored:
                print(f"# WARNING: {side} probe pack disagrees with the scored pack for "
                      f"{t} — stage evidence for this task is discarded")
                continue
            # Same task id is not the same question: a stale probe row could
            # carry a different gold set (dataset drift, an edited pin) and
            # every stage label would then be about different files.
            if set(k["gold"]) != set(sink_rows[t]["vec"]["gold"]):
                print(f"# WARNING: {side} probe gold set differs from the scored gold set "
                      f"for {t} — stage evidence for this task is discarded")
                continue
            ok[t] = k
        return ok

    kb, kc = agreeing(kb, base, "baseline"), agreeing(kc, cand, "candidate")
    print(f"# stage evidence usable for {len(kb)}/{len(shared)} baseline and "
          f"{len(kc)}/{len(shared)} candidate tasks after the pack-agreement check")

    print("\n# per-task stage evidence and reading (candidate, then baseline)")
    print("#   [probe] marks a label whose wording rests on kept_pool_probe.py's")
    print("#   pool/omitted evidence. That evidence comes from a separate `oxide")
    print("#   context` invocation whose final pack and gold set were checked equal")
    print("#   to the scored one; the intermediates themselves are recorded on the")
    print("#   probe side only, so the stage named is the probe's, not the scoring")
    print("#   run's own recorded stage.")
    for t in shared:
        need = ("vec", "hybrid", "budgeted", "lexical")
        if not (all(k in cand[t] for k in need) and all(k in base[t] for k in need)):
            continue
        print(f"  {t}")
        for side, rows, kept in (("candidate", cand, kc), ("baseline", base, kb)):
            k = kept.get(t)
            # Tag every label whose wording depends on probe-only evidence.
            # The docstring's caveat lives in the file; the tag carries it to
            # the reader of the output, who may never open the file.
            tag = "[probe] " if k else ""
            print(f"    {side:<10} {signature(stages(rows[t], k))}   {tag}{mechanism(rows[t], k)}")


if __name__ == "__main__":
    main()
