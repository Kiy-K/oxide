"""Score arms G, C, I, P against results/truth.json (preregistration § Arms, § Metrics).

TIKTOKEN_CACHE_DIR=... uv run --with tiktoken python arms.py
"""

import json
import statistics
import subprocess
import time
from collections.abc import Callable
from typing import Any

import tiktoken
from common import CORPORA, DATA, RESULTS, Enclosing, checkout, open_repo

ENC = tiktoken.get_encoding("o200k_base")
CROSS = ("calls", "references", "imports")


def tokens(text: str) -> int:
    return len(ENC.encode(text, disallowed_special=()))


def timed(fn: Callable[[], Any]) -> tuple[Any, float]:
    times, value = [], None
    for _ in range(3):
        t = time.perf_counter()
        value = fn()
        times.append((time.perf_counter() - t) * 1000)
    return value, statistics.median(times)


def prf(claims: set[str], truth: set[str]) -> dict[str, float]:
    hit = len(claims & truth)
    p = hit / len(claims) if claims else 1.0
    r = hit / len(truth) if truth else 1.0
    return {"p": p, "r": r, "f1": 2 * p * r / (p + r) if p + r else 0.0}


class Grep:
    def __init__(self, corpus: str, enclosing: Enclosing) -> None:
        self.root, self.enclosing = checkout(corpus), enclosing
        self.lines: dict[str, list[int]] = {}

    def line_start(self, path: str, line: int) -> int:
        if path not in self.lines:
            data = (self.root / path).read_bytes()
            self.lines[path] = [0] + [i + 1 for i, b in enumerate(data) if b == 10]
        return self.lines[path][line - 1]

    def run(self, key: str) -> tuple[set[str], str]:
        name = self.enclosing.info[key][3]
        out = subprocess.run(
            ["rg", "-nw", "--column", "--no-heading", "-t", "py", "-F", name, "."],
            cwd=self.root,
            capture_output=True,
            text=True,
            check=False,
        ).stdout
        owners = set()
        for row in out.splitlines():
            path, line, column, _ = row.split(":", 3)
            path = path.removeprefix("./")
            if path not in self.enclosing.file:
                continue
            owner = self.enclosing.at(path, self.line_start(path, int(line)) + int(column) - 1)
            if owner and owner != key:
                owners.add(owner)
        return owners, out


def overlaps(entity: list, items: list) -> bool:  # type: ignore[type-arg]
    _, category, _, _, path, start, end, _ = entity
    for item in items:
        if item.source.path == path and (
            category == "file" or (item.source.start < end and start < item.source.end)
        ):
            return True
    return False


def file_key(repo: Any, entity: Any) -> str:
    """Module hops (importers name modules) count as their file, the oracle's granularity."""
    if entity.category == "module" and entity.source is not None:
        return f"file:{entity.source.path}"
    return str(entity.key)


def witness(repo: Any, hop: Any, roots: tuple[str, ...]) -> list[Any]:
    return [
        rel.evidence
        for rel in repo.relations(hop.entity.key)
        if rel.evidence is not None
        and ((rel.kind in CROSS and rel.target in roots) or rel.kind == "tested_by")
    ] + [
        rel.evidence
        for root in roots
        for rel in repo.relations(root)
        if rel.kind == "tested_by" and rel.target == hop.entity.key and rel.evidence is not None
    ]


def line_of(repo: Any, path: str, offset: int) -> int:
    ref = repo._view().files[path]
    from oxide.models import SourceRef

    data = repo._view().source(SourceRef(path, 0, ref.size, ref.digest))
    return data.count(b"\n", 0, offset) + 1


def render(repo: Any, hops: list[Any]) -> str:
    rows = []
    for h in hops:
        source = h.entity.source
        where = f"{source.path}:{line_of(repo, source.path, source.start)}" if source else ""
        rows.append(f"{h.depth} {h.proven_depth} {h.via} {where}")
    return "\n".join(rows)


def main() -> None:
    truth = json.loads((RESULTS / "truth.json").read_text())
    rows, audit = [], []
    for corpus in CORPORA:
        enclosing = Enclosing(json.loads((DATA / f"entities-{corpus}.json").read_text()))
        grep = Grep(corpus, enclosing)
        with open_repo(corpus) as repo:
            for t in (t for t in truth if t["corpus"] == corpus):
                key, q1, q2 = t["key"], set(t["q1"]), set(t["q2"])
                row: dict[str, Any] = {"corpus": corpus, "key": key, "kind": t["kind"]}
                # G
                (g1, g1_text), g1_ms = timed(lambda grep=grep, key=key: grep.run(key))
                g2, g2_text, g2_calls = set(g1), g1_text, 1
                for owner in sorted(g1):
                    if enclosing.info[owner][2] in ("function", "method"):
                        more, text = grep.run(owner)
                        g2 |= more
                        g2_text += text
                        g2_calls += 1
                g2.discard(key)
                row["G"] = {
                    "q1": prf(g1, q1),
                    "q2": prf(g2, q2),
                    "tok1": tokens(g1_text),
                    "tok2": tokens(g2_text),
                    "calls2": g2_calls,
                    "ms1": g1_ms,
                }
                # C
                name = t["name"]
                bundle, c_ms = timed(
                    lambda name=name: repo.build_context(
                        f"What calls or depends on {name}?", 4096, symbols=[name]
                    )
                )
                items = list(bundle.items)
                row["C"] = {
                    "r1": sum(overlaps(enclosing.info[k], items) for k in q1) / len(q1),
                    "r2": sum(overlaps(enclosing.info[k], items) for k in q2) / len(q2),
                    "tok": tokens(bundle.payload),
                    "ms": c_ms,
                }
                # I and P
                for depth, q, label in ((1, q1, "q1"), (2, q2, "q2")):
                    impact, i_ms = timed(lambda key=key, depth=depth: repo.impact(key, depth=depth))
                    hops = list(impact.hops)
                    proven = [h for h in hops if h.proven_depth is not None]
                    claims = {file_key(repo, h.entity) for h in hops}
                    pclaims = {file_key(repo, h.entity) for h in proven}
                    row.setdefault("I", {})[label] = prf(claims, q)
                    row.setdefault("P", {})[label] = prf(pclaims, q)
                    row["I"][f"tok{depth}"] = tokens(render(repo, hops))
                    row["P"][f"tok{depth}"] = tokens(render(repo, proven))
                    row["I"][f"ms{depth}"] = i_ms
                    row["I"][f"n{depth}"] = len(hops)
                    row["I"]["unresolved"] = impact.unresolved
                    row["I"]["stopped_by"] = impact.stopped_by
                    audit.append(
                        {
                            "corpus": corpus,
                            "key": key,
                            "q": label,
                            "I_false": sorted(claims - q),
                            "I_missed": sorted(q - claims),
                            "G_false": sorted((g1 if depth == 1 else g2) - q),
                        }
                    )
                    if depth == 1:
                        sites = t["sites"]
                        valid = 0
                        for h in hops:
                            spans = witness(repo, h, impact.roots)
                            if any(
                                s.path == p and s.start <= o < s.end
                                for s in spans
                                for p, o, _ in sites
                            ):
                                valid += 1
                        row["I"]["path_valid"] = [valid, len(hops)]
                    else:
                        q1_claims = {file_key(repo, h.entity) for h in hops if h.depth == 1}
                        chains = [h for h in hops if h.depth == 2]
                        ok = sum(
                            file_key(repo, h.entity) in q and h.via.split(":", 2)[2] in q1
                            for h in chains
                        )
                        row["I"]["chain_valid"] = [ok, len(chains), len(q1_claims)]
                rows.append(row)
                print(
                    corpus, key, json.dumps({a: row[a].get("q1", row[a].get("r1")) for a in "GCIP"})
                )
    (RESULTS / "rows.json").write_text(json.dumps(rows, indent=1))
    (RESULTS / "audit.json").write_text(json.dumps(audit, indent=1))


if __name__ == "__main__":
    main()
