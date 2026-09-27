#!/usr/bin/env python3
"""Non-intent control channel for the symbol-attached representation (issue #30).

Question it answers: is C's gain caused by developer-intent *text*, or by
adding any second, separately normalized lexical channel over symbols?
Builds two symbol rankings with one identical BM25 implementation (Python
mirror of `embeddings::tokenize`, k1=1.5, b=0.75) so only the text differs:

  Cpy-intent  kept intent records (as intent_dump.rs) → BM25 → attached symbols
  Cpy-code    every non-module symbol's own body with ALL extracted comment /
              docstring lines (kept or not) and trailing comments removed
              → BM25 → symbols

Writes a sidecar {"id", "lists": {name: [file#qualified_name, ...]}} that
score_intent.py --extra consumes as additional C-shaped arms.

usage: code_control.py <tasks.jsonl> > sidecar.jsonl
"""
import json
import math
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path

EV = Path.home() / ".cache/oxide-intent-eval/ev"
SYMBOL_TYPES = {"docstring", "leading_comment", "inline_comment"}
STOP = {"the", "and", "for", "with", "this", "that", "from", "into", "self", "none", "null", "undefined",
        "true", "false", "fn", "func", "def", "let", "var", "const", "return", "import"}
CAMEL = re.compile(r"[A-Z]+(?=[A-Z][a-z0-9])|[A-Z]?[a-z0-9]+|[A-Z]+")


def tokenize(text):
    out = []
    for raw in re.split(r"[^\w]+", text):
        for part in raw.split("_"):
            for p in CAMEL.findall(part):
                p = p.lower()
                if len(p) >= 2 and p not in STOP:
                    out.append(p)
    return out


class BM25:
    def __init__(self, docs):
        self.post = defaultdict(list)
        self.len = []
        for i, toks in enumerate(docs):
            self.len.append(len(toks))
            for t, n in Counter(toks).items():
                self.post[t].append((i, n))
        self.avg = sum(self.len) / max(1, len(self.len))

    def search(self, q, k=200):
        n = max(1, len(self.len))
        sc = defaultdict(float)
        for t in tokenize(q):
            docs = self.post.get(t)
            if not docs:
                continue
            idf = math.log1p(max(0.0, (n - len(docs) + 0.5) / (len(docs) + 0.5)))
            for d, tf in docs:
                sc[d] += idf * tf * 2.5 / (tf + 1.5 * (0.25 + 0.75 * self.len[d] / max(1.0, self.avg)))
        return [d for d, _ in sorted(sc.items(), key=lambda x: (-x[1], x[0]))[:k]]


corpora = {}


def corpus(root):
    if root in corpora:
        return corpora[root]
    name = Path(root).name
    recs = [json.loads(l) for l in open(EV / f"{name}.jsonl")]
    kept = [r for r in recs if r["keep"]]
    intent = BM25([tokenize((r["heading"] + "\n" if r["heading"] else "") + r["text"]) for r in kept])
    intent_lines = defaultdict(set)
    for r in recs:
        if r["type"] not in ("readme", "doc", "changelog"):
            intent_lines[r["file"]].update(range(r["start_line"], r["end_line"] + 1))
    con = sqlite3.connect(f"file:{root}/.oxide/index.db?mode=ro", uri=True)
    syms, docs, cache = [], [], {}
    for f, qn, kind, s, e in con.execute("select file, qualified_name, kind, start_line, end_line from symbols "
                                         "where kind not in ('module','file') order by file, start_line"):
        if f not in cache:
            try:
                cache[f] = (Path(root) / f).read_text(errors="replace").splitlines()
            except OSError:
                cache[f] = []
        body = [re.sub(r"\s(#|//).*$", "", cache[f][i - 1]) for i in range(s, min(e, len(cache[f])) + 1)
                if i not in intent_lines[f]]
        syms.append(f"{f}#{qn}")
        docs.append(tokenize("\n".join(body)))
    corpora[root] = (kept, intent, syms, BM25(docs))
    return corpora[root]


for line in open(sys.argv[1]):
    t = json.loads(line)
    kept, intent, syms, code = corpus(t["path"])
    seen, isyms = set(), []
    for i in intent.search(t["query"]):
        r = kept[i]
        if r["type"] in SYMBOL_TYPES and r["symbol"]:
            k = f"{r['file']}#{r['symbol']}"
            if k not in seen:
                seen.add(k)
                isyms.append(k)
    print(json.dumps({"id": t["id"], "lists": {"Cpy-intent": isyms,
                                               "Cpy-code": [syms[i] for i in code.search(t["query"])]}}))
