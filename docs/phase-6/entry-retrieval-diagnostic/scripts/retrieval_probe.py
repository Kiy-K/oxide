"""Dev-only entry-point failure taxonomy: retrieval_probe.py {py|ts} OUT.json

For every verified gold span of every dev task: the best rank of an entity covering it
(a symbol containing a gold line, or its file when the span has module-level lines)
under (a) the production FTS without a cut-off, (b) an in-memory BM25 replica of the
production documents (name, signature, path terms), (c) the replica plus body terms.
Also which distinctive query terms occur in the gold's indexed document or body.
Diagnostic only: nothing here changes the product.
"""

import json
import math
import sys
import tempfile
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path.cwd() / "tests/python"))
import contextbench as cb

from oxide.core.retrieval import document, terms

STOP = set(
    ["a", "an", "and", "are", "as", "at", "be", "been", "but", "by", "can", "could", "do", "does", "for", "from", "has", "have", "how", "i", "if", "in", "into", "is", "it", "its", "me", "my", "no", "not", "of", "on", "or", "our", "should", "so", "some", "such", "than", "that", "the", "their", "them", "then", "there", "these", "they", "this", "to", "too", "use", "used", "using", "was", "we", "were", "what", "when", "where", "which", "while", "who", "will", "with", "would", "you", "your", "also", "get", "set", "new", "add", "make", "need", "now", "only", "same", "see", "via", "all", "any", "after", "before", "other", "more", "just", "like", "want", "way", "still", "because", "however", "here"]
)
K1, B = 1.2, 0.75


def stem(t: str) -> str:
    """Crude, symmetric suffix stripping (a stand-in for Porter on both sides)."""
    for suf in ("ations", "ation", "ings", "ing", "ies", "ers", "er", "ed", "es", "s"):
        if t.endswith(suf) and len(t) - len(suf) >= 3:
            return t[: -len(suf)] + ("y" if suf == "ies" else "")
    return t


def bag(text: str) -> Counter:
    return Counter(stem(t) for t in terms(text) if t not in STOP)


class BM25:
    def __init__(self, docs: dict[str, Counter]):
        self.docs = docs
        self.len = {k: sum(c.values()) for k, c in docs.items()}
        self.avg = sum(self.len.values()) / max(1, len(docs))
        self.df = Counter(t for c in docs.values() for t in c)
        self.n = len(docs)

    def ranks(self, query: list[str]) -> dict[str, int]:
        q = set(query)
        scores: dict[str, float] = {}
        for k, c in self.docs.items():
            s = 0.0
            for t in q:
                f = c.get(t)
                if f:
                    idf = math.log(1 + (self.n - self.df[t] + 0.5) / (self.df[t] + 0.5))
                    s += idf * f * (K1 + 1) / (f + K1 * (1 - B + B * self.len[k] / self.avg))
            if s:
                scores[k] = s
        order = sorted(scores, key=lambda k: (-scores[k], k))
        return {k: i for i, k in enumerate(order, 1)}


def main():
    bench, out = sys.argv[1], Path(sys.argv[2])
    if bench == "ts":
        import contextbench_ts as ts

        tasks, frozen_dir, root_of = ts.load_tasks(), ts.BASELINE, ts.corpus_root
        index = ts._indexed
    else:
        tasks, frozen_dir, root_of = cb.load_tasks(), cb.BASELINE, cb.corpus_root

        def index(root, store):
            repo = cb.oxide.open_repository(root, data_dir=store)
            return repo, repo.index(), None

    frozen = {r["task"]: r for r in json.loads((frozen_dir / "results.json").read_text())}
    dev = [t for t in tasks["tasks"] if t["split"] == "dev"]
    report = []
    for name in dict.fromkeys(t["corpus"] for t in dev):
        root = root_of(name, tasks["corpora"][name])
        print("probing", name, file=sys.stderr)
        with tempfile.TemporaryDirectory() as d:
            repo, _, _ = index(root, Path(d))
            with repo:
                view = repo._view()
                lines = cb.Lines(root)
                keys = [r[0] for r in view._query("MATCH (e:Entity) RETURN e.key")]
                ents = view.entities(keys)
                data = {p: (root / p).read_bytes() for p in view.files if (root / p).is_file()}
                meta, body, spans = {}, {}, {}
                for k, e in ents.items():
                    meta[k] = bag(document(e))
                    if e.source is not None:
                        spans[k] = lines.span(e.source)
                        if e.category == "symbol" and e.source.path in data:
                            text = data[e.source.path][e.source.start : e.source.end]
                            body[k] = bag(text.decode("utf-8", "replace"))
                # post hoc: bodies only for leaf symbols (no other symbol nested inside)
                inner = Counter()
                for k, e in ents.items():
                    if e.category == "symbol" and e.source is not None:
                        inner[(e.source.path, e.source.start, e.source.end)] = 0
                by_path = {}
                for p_, a, b_ in inner:
                    by_path.setdefault(p_, []).append((a, b_))
                leaf = set()
                for p_, rs in by_path.items():
                    for a, b_ in rs:
                        if not any(a <= x and y <= b_ and (x, y) != (a, b_) for x, y in rs):
                            leaf.add((p_, a, b_))
                leafbody = BM25(
                    {
                        k: meta[k]
                        + (
                            body.get(k, Counter())
                            if ents[k].source is not None
                            and (ents[k].source.path, ents[k].source.start, ents[k].source.end)
                            in leaf
                            else Counter()
                        )
                        for k in meta
                    }
                )
                replica = BM25(meta)
                # diagnostic: a file's document = its path plus every member's name/signature terms
                filedoc = {}
                for k, e in ents.items():
                    if e.source is not None:
                        filedoc.setdefault(e.source.path, Counter()).update(meta[k])
                files_bm25 = BM25(filedoc)
                bodied = BM25({k: meta[k] + body.get(k, Counter()) for k in meta})
                files_with = Counter(
                    t for b in data.values() for t in set(bag(b.decode("utf-8", "replace")))
                )
                rare = max(1, 0.05 * len(data))
                for task in (t for t in dev if t["corpus"] == name):
                    q = list(bag(task["query"]))
                    qset = set(q)
                    distinct = {t for t in qset if 0 < files_with[t] <= rare}
                    hits, _ = view.lexical(" ".join(terms(task["query"])), 100000)
                    fts = {k: i for i, (k, _) in enumerate(hits, 1)}
                    r_rep, r_body = replica.ranks(q), bodied.ranks(q)
                    r_leaf = leafbody.ranks(q)
                    r_file = files_bm25.ranks(q)
                    best_entity = {}
                    for k, r in sorted(r_rep.items(), key=lambda kv: kv[1]):
                        if k in ents and ents[k].source is not None:
                            best_entity.setdefault(ents[k].source.path, len(best_entity) + 1)
                    # one preset pruning, not swept: no digit-bearing terms, none in >10% of documents
                    pq = [
                        t
                        for t in qset
                        if not any(ch.isdigit() for ch in t) and replica.df[t] <= 0.1 * replica.n
                    ]
                    r_rep_p, r_body_p = replica.ranks(pq), bodied.ranks(pq)
                    rows = []
                    for g in frozen[task["id"]]["gold"]:
                        if not g["verified"]:
                            continue
                        path, (s, e) = g["file"], g["lines"]
                        code = lines.code(path, s, e)
                        holders = {
                            k
                            for k, sp in spans.items()
                            if sp[0] == path
                            and ents[k].category == "symbol"
                            and any(sp[1] <= n <= sp[2] for n in code)
                        }
                        if g["module_lines"]:
                            holders.add(f"file:{path}")

                        def best(r):
                            return min((r[k] for k in holders if k in r), default=None)

                        doc_terms = set().union(*(meta.get(k, Counter()) for k in holders))
                        gold_text = "\n".join(
                            data.get(path, b"").decode("utf-8", "replace").split("\n")[s - 1 : e]
                        )
                        span_terms = set(bag(gold_text))
                        rows.append(
                            {
                                "file": path,
                                "lines": [s, e],
                                "entry_rank": g["entry_rank"],
                                "lost": g["lost"].get("4096", "complete"),
                                "fts": best(fts),
                                "replica": best(r_rep),
                                "body": best(r_body),
                                "replica_pruned": best(r_rep_p),
                                "body_pruned": best(r_body_p),
                                "leaf_body": best(r_leaf),
                                "file_doc_rank": r_file.get(path),
                                "file_by_entity_rank": best_entity.get(path),
                                "doc_overlap": sorted(qset & doc_terms),
                                "span_overlap": sorted(qset & span_terms),
                                "distinct_doc": sorted(distinct & doc_terms),
                                "distinct_span": sorted(distinct & span_terms),
                                "path_overlap": sorted(qset & set(bag(path))),
                            }
                        )
                    report.append(
                        {
                            "task": task["id"],
                            "corpus": name,
                            "query_terms": sorted(qset),
                            "pruned_terms": sorted(pq),
                            "distinct_query_terms": sorted(distinct),
                            "absent_query_terms": sorted(t for t in qset if files_with[t] == 0),
                            "spans": rows,
                        }
                    )
    out.write_text(json.dumps(report, indent=1) + "\n")


if __name__ == "__main__":
    main()
