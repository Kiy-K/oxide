"""Cases for the TOON serialization study (preregistration.md § Outputs, § Questions).

    build.py edge     write the synthetic edge repository to DATA/edge-repo
    build.py cases    index every corpus into a private data location, write DATA/cases.json

Every arm renders the same value: ``asdict`` of the API result, normalized through
JSON. Questions and their gold come from that value only, never from a rendering.
Run with ``uv run --with toon-format --with tiktoken`` (nothing is added to the lock).
"""

from __future__ import annotations

import copy
import dataclasses
import json
import math
import random
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import toon_format

REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO / "tests/python"))
import contextbench as cb  # noqa: E402
import contextbench_ts as cbts  # noqa: E402

import oxide  # noqa: E402
from oxide.mcp_server import DEFAULT_BUDGET, DEFAULT_LIMIT, MAX_RESULTS  # noqa: E402

DATA = Path.home() / "Projects/oxide-eval-data/oxide-toon-eval"
SEED = 20261021
REAL = (
    "psf__requests",
    "mwaskom__seaborn",
    "pallets__flask",
    "coder__code-server",
    "darkreader__darkreader",
)
SEARCH_ARMS, BUNDLE_ARMS = ("C", "T", "P"), ("C", "T", "HC", "HT")


def plain(value: Any) -> Any:
    raw = json.dumps(value, ensure_ascii=False, default=dataclasses.asdict)
    return json.loads(raw)


def render(value: Any, arm: str) -> str:
    if arm == "C":
        return json.dumps(value, separators=(",", ":"), ensure_ascii=False)
    if arm == "P":
        return json.dumps(value, indent=2, ensure_ascii=False)
    if arm == "T":
        return toon_format.encode(value)
    if arm == "Ttab":
        return toon_format.encode(value, delimiter="\t")
    raise ValueError(arm)


def envelope(bundle: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(bundle)
    for item in out["items"]:
        del item["text"]
    return out


def texts(kind: str, value: Any, extra: dict[str, str] | None = None) -> dict[str, str]:
    out = {arm: render(value, arm) for arm in ("C", "T", "P", "Ttab")}
    if kind.endswith("bundle"):
        payload = "".join(item["text"] for item in value["items"])
        env = envelope(value)
        for arm in ("C", "T", "P", "Ttab"):
            out["H" + arm] = render(env, arm) + "\n\n" + payload
            out["E" + arm] = render(env, arm)  # envelope alone, for S1
    return out | (extra or {})


# --- questions: (text, gold, grade) with grade in int | exact | set | pair | bool ---


def q(text: str, gold: Any, grade: str) -> dict[str, Any]:
    return {"text": text, "gold": gold, "grade": grade}


def search_questions(v: dict[str, Any], rng: random.Random, edge: bool) -> list[dict[str, Any]]:
    entries = v["entries"]
    out = []
    located = [n for n, e in enumerate(entries) if e["entity"]["source"]]
    if located:
        k = rng.choice(located)
        src = entries[k]["entity"]["source"]
        out.append(q(f"What are the source path and the start byte of the entry at position "
                     f"{k + 1} (1-based, in the order listed)? Answer as [\"<path>\", <start>].",
                     [src["path"], src["start"]], "pair"))  # fmt: skip
    out.append(q("List the entity key of every entry whose entity has test = true.",
                 [e["entity"]["key"] for e in entries if e["entity"]["test"]], "set"))  # fmt: skip
    if edge:
        out.append(q("List the hint of every hint issue whose issue is not_found.",
                     [h["hint"] for h in v["hints"] if h["issue"] == "not_found"], "set"))  # fmt: skip
        out.append(q("List the entity key of every entry that has at least one seed.",
                     [e["entity"]["key"] for e in entries if e["seeds"]], "set"))  # fmt: skip
    else:
        out.append(q("How many entries have an entity whose signature is null?",
                     sum(e["entity"]["signature"] is None for e in entries), "int"))  # fmt: skip
        ranks = [e for e in entries if e["lexical_rank"] is not None]
        if ranks:
            e = rng.choice(ranks)
            out.append(q(f"What is the entity name of the entry whose lexical_rank is "
                         f"{e['lexical_rank']}?", e["entity"]["name"], "exact"))  # fmt: skip
    out.append(q("How many entries does the result contain?", len(entries), "int"))
    if not entries:
        out.append(q("What is lexical.state?", v["lexical"]["state"], "exact"))
    return out[:5]


def files_questions(v: dict[str, Any], rng: random.Random) -> list[dict[str, Any]]:
    files = v["files"]
    a, b = rng.sample(files, 2)
    language = rng.choice(sorted({f["language"] for f in files}))
    return [
        q(f"What is the size of the file {a['path']}?", a["size"], "int"),
        q(f"What is the digest of the file {b['path']}?", b["digest"], "exact"),
        q(
            f"How many files have language {language}?",
            sum(f["language"] == language for f in files),
            "int",
        ),  # fmt: skip
        q(
            "How many files have coverage unsupported?",
            sum(f["coverage"] == "unsupported" for f in files),
            "int",
        ),  # fmt: skip
        q(
            "List the path of every file with at least one diagnostic.",
            [f["path"] for f in files if f["diagnostics"]],
            "set",
        ),  # fmt: skip
    ]


def relations_questions(v: dict[str, Any], rng: random.Random) -> list[dict[str, Any]]:
    rels = v["relations"]
    kinds = Counter(r["kind"] for r in rels)
    top = kinds.most_common(1)[0][0]
    out = [q(f"How many relations have kind {top}?", kinds[top], "int")]
    small = [k for k in sorted(kinds) if len({r["source"] for r in rels if r["kind"] == k}) <= 10]
    if small:
        k2 = rng.choice(small)
        out.append(q(f"List the distinct source of every relation with kind {k2}.",
                     sorted({r["source"] for r in rels if r["kind"] == k2}), "set"))  # fmt: skip
    out.append(q("List the distinct name of every relation whose resolution is unresolved.",
                 sorted({r["name"] for r in rels if r["resolution"] == "unresolved" and r["name"]}),
                 "set"))  # fmt: skip
    evid = [n for n, r in enumerate(rels) if r["evidence"]]
    if evid:
        i = rng.choice(evid)
        e = rels[i]["evidence"]
        out.append(q(f"What are the evidence path and evidence start of the relation at "
                     f"position {i + 1} (1-based, in the order listed)? Answer as "
                     f"[\"<path>\", <start>].", [e["path"], e["start"]], "pair"))  # fmt: skip
    pairs = Counter((r["site"], r["kind"]) for r in rels)
    unique = [r for r in rels if pairs[(r["site"], r["kind"])] == 1 and r["target"]]
    if unique:
        r = rng.choice(unique)
        out.append(q(f"What is the target of the relation whose site is {r['site']} and kind "
                     f"is {r['kind']}?", r["target"], "exact"))  # fmt: skip
    return out


SPECIAL = ('"', "\\", ": ", "\t", "'", "#", "`", "{")


def copy_line(v: dict[str, Any], root: Path, rng: random.Random) -> dict[str, Any] | None:
    choices: list[tuple[bool, str, int, str]] = []
    for item in v["items"]:
        path, (first, last) = item["source"]["path"], item["lines"]
        body = item["text"].split("\n", 1)[1].split("\n")[: last - first + 1]
        try:
            lines = (root / path).read_bytes().decode().split("\n")
        except (OSError, UnicodeDecodeError):
            continue
        for n, text in zip(range(first, last + 1), body, strict=False):
            if text.strip() and n <= len(lines) and lines[n - 1].rstrip() == text.rstrip():
                choices.append((any(s in text for s in SPECIAL), path, n, text.rstrip()))
    if not choices:
        return None
    special = [c for c in choices if c[0]]
    _, path, n, text = rng.choice(special or choices)
    return q(f"Copy exactly the source text of line {n} of the file {path}, as it appears in "
             f"the source (one line, without its line break).", text, "exact")  # fmt: skip


def bundle_questions(v: dict[str, Any], root: Path, rng: random.Random) -> list[dict[str, Any]]:
    out = [
        q(
            "What are used and budget? Answer as [<used>, <budget>].",
            [v["used"], v["budget"]],
            "pair",
        ),  # fmt: skip
        q(
            "How many omissions have stage packing?",
            sum(o["stage"] == "packing" for o in v["omissions"]),
            "int",
        ),  # fmt: skip
    ]
    if v["items"]:
        i = rng.randrange(len(v["items"]))
        item = v["items"][i]
        out.append(q(f"What are the source path and the first and last line of the item at "
                     f"position {i + 1} (1-based)? Answer as [\"<path>\", <first>, <last>].",
                     [item["source"]["path"], *item["lines"]], "pair"))  # fmt: skip
    out.append(q("List the key of every item entity whose complete is false.",
                 sorted({e["key"] for i in v["items"] for e in i["entities"] if not e["complete"]}),
                 "set"))  # fmt: skip
    out.append(q("What are route.stopped_by and route.deferred? Answer as [<stopped_by>, "
                 "<deferred>], with null if stopped_by is null.",
                 [v["route"]["stopped_by"], v["route"]["deferred"]], "pair"))  # fmt: skip
    line = copy_line(v, root, rng)
    return out + ([line] if line else [])


# --- truncation ---


def cut(text: str, arm: str, key: str) -> str:
    """``text`` up to the start of the entry whose entity key is ``key``."""
    at = text.index(json.dumps(key, ensure_ascii=False))
    if arm.startswith("T"):
        start = text.rfind("- entity:", 0, at)
        return text[: text.rfind("\n", 0, start) + 1]
    return text[: text.rfind("{", 0, text.rfind('"entity"', 0, at))]


def truncated(case: dict[str, Any]) -> dict[str, Any]:
    v, entries = case["value"], case["value"]["entries"]
    m = math.ceil(len(entries) / 2)
    key = entries[m]["entity"]["key"]
    rng = random.Random(f"{SEED}:{case['id']}:cut")
    k = rng.choice([n for n in range(m) if entries[n]["entity"]["source"]])
    src = entries[k]["entity"]["source"]
    return {
        "id": case["id"] + ":cut",
        "kind": "truncated",
        "corpus": case["corpus"],
        "value": v,
        "shown": m,
        "texts": {arm: cut(case["texts"][arm], arm, key) for arm in ("C", "T", "P", "Ttab")},
        "arms": SEARCH_ARMS,
        "questions": [
            q("How many entries are completely shown in this output?", m, "int"),
            q("Is this output cut off before its end? Answer true or false.", True, "bool"),
            q(
                f"What are the source path and the start byte of the entry at position {k + 1} "
                f'(1-based, in the order listed)? Answer as ["<path>", <start>].',
                [src["path"], src["start"]],
                "pair",
            ),  # fmt: skip
        ],
    }


# --- corpora ---


def real_tasks() -> list[tuple[dict[str, Any], Path]]:
    out = []
    for module in (cb, cbts):
        tasks = module.load_tasks()
        for task in tasks["tasks"]:
            corpus = tasks["corpora"][task["corpus"]]
            if task["split"] == "dev" and task["corpus"].split("@")[0] in REAL:
                out.append((task, module.corpus_root(task["corpus"], corpus)))
    return out


def relation_target(repo: oxide.Repository, result: Any) -> str:
    keys = [e.entity.key for e in result.entries[:10] if e.entity.category == "symbol"]
    return max(keys, key=lambda k: (len(repo.relations(k)), k))


def outputs(repo: oxide.Repository, prefix: str, corpus: str, root: Path, query: str,
            edge: bool, **hints: Any) -> list[dict[str, Any]]:  # fmt: skip
    rng = random.Random(f"{SEED}:{prefix}")
    s20 = repo.search(query, limit=DEFAULT_LIMIT, **hints)
    found: list[tuple[str, Any, str | None]] = [
        ("search20", s20, None),
        ("files", {"files": plain(repo.files())}, None),
    ]
    if not edge:
        found.append(("search100", repo.search(query, limit=MAX_RESULTS), None))
    key = relation_target(repo, s20)
    found.append(("relations", {"entity": key, "relations": plain(repo.relations(key))}, None))
    bundle = repo.build_context(query, DEFAULT_BUDGET, **hints)
    head = (
        f"# oxide context: {bundle.used}/{bundle.budget} {bundle.units}, "
        f"{len(bundle.items)} items, {len(bundle.omissions)} omitted, "
        f"snapshot {bundle.snapshot.snapshot_id}\n"
    )
    mcp = head + "".join(f"# degraded: {d}\n" for d in bundle.degradations) + bundle.payload
    found.append(("bundle", bundle, mcp))
    cases = []
    for kind, raw, mcp_text in found:
        v = plain(raw)
        kind = ("edge_" if edge else "") + kind
        if kind.endswith("bundle"):
            questions = bundle_questions(v, root, rng)
        elif kind.endswith("files"):
            questions = files_questions(v, rng)
        elif kind.endswith("relations"):
            questions = relations_questions(v, rng)
        else:
            questions = search_questions(v, rng, edge)
        cases.append({
            "id": f"{prefix}:{kind}",
            "kind": kind,
            "corpus": corpus,
            "value": v,
            "texts": texts(kind, v, {"M": mcp_text} if mcp_text else None),
            "arms": BUNDLE_ARMS if kind.endswith("bundle") else SEARCH_ARMS,
            "questions": questions,
        })  # fmt: skip
    return cases


EDGE = {
    "src/données/模块.py": '''"""Données: parsing helpers — «guillemets» and ‘quotes’."""
import re

PATTERN = re.compile(r"^\\s*(?P<key>[\\w-]+):\\s*\\"(?P<value>[^\\"]*)\\"$")
SEPARATOR = "\\t|\\t"
WINDOWS_PATH = "C:\\\\Users\\\\naïve\\\\données"


def parse_ligne(ligne: str) -> dict[str, str]:
    """Parse `key: "value"` lines; '- item' and '# comment' lines are skipped."""
    if ligne.startswith("- ") or ligne.startswith("#"):
        return {}
    match = PATTERN.match(ligne)
    return {match["key"]: match["value"]} if match else {}


class Größe:
    """Size in bytes: [min, max] with {unit}."""

    def __init__(self, valeur: int, unité: str = "o") -> None:
        self.valeur = valeur
        self.unité = unité

    def format(self) -> str:
\treturn f"{self.valeur}\\t{self.unité}: {{ok}} \\"quoted\\" \\\\ end"
''',
    "src/naïve café.py": '''from données.模块 import Größe, parse_ligne


def lire_config(text: str) -> list[dict[str, str]]:
    """Read config text: key: "value" per line, - and # lines skipped."""
    return [parse_ligne(line) for line in text.splitlines() if line.strip()]


def taille_totale(tailles: list[Größe]) -> int:
    return sum(t.valeur for t in tailles)
''',
    "web/ünï/コード.ts": """export const GREETING = `Hello, ${"wörld"}: "quoted" \\\\ back`;

export interface Größe {
  min: number;
  max: number;
  unit?: "o" | "ko";
}

export function formatSize(s: Größe): string {
  return `[${s.min}, ${s.max}] ${s.unit ?? "o"}: #size`;
}

export class コード {
  constructor(private readonly name: string) {}

  render(): string {
    return `- ${this.name}: "#tag" \\\\`;
  }
}
""",
    "tests/test_模块.py": """from données.模块 import parse_ligne


def test_parse_ligne_skips_comment():
    assert parse_ligne('# x: "y"') == {}
    assert parse_ligne('key: "value"') == {"key": "value"}
""",
}


def edge() -> None:
    root = DATA / "edge-repo"
    for path, text in EDGE.items():
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_text(text, encoding="utf-8")


def cases() -> None:
    data = DATA / "oxide-data"
    data.mkdir(parents=True, exist_ok=True)
    out: list[dict[str, Any]] = []
    nav: list[dict[str, Any]] = []
    for task, root in real_tasks():
        with oxide.open_repository(root, data_dir=data) as repo:
            repo.index()
            found = outputs(repo, task["id"], task["corpus"], root, task["query"], edge=False)
        out += found
        gold = sorted({g["file"] for g in task["gold"] if g.get("verified", True)})
        nav.append({
            "id": f"{task['id']}:nav", "kind": "nav", "corpus": task["corpus"],
            "issue": task["query"], "gold_files": gold, "texts": found[0]["texts"],
            "arms": SEARCH_ARMS, "questions": [],
        })  # fmt: skip
        print(task["id"], len(found), file=sys.stderr)
    root = DATA / "edge-repo"
    with oxide.open_repository(root, data_dir=data) as repo:
        repo.index()
        query = "parse ligne config größe format quoted separator"
        hints = {"symbols": ("Größe", "DoesNotExist"), "paths": ("src/naïve café.py",)}
        edges = outputs(repo, "edge", "edge", root, query, edge=True, **hints)
        v = plain(repo.search("zzqqxxvv", limit=DEFAULT_LIMIT))
        edges.append({
            "id": "edge:edge_empty", "kind": "edge_empty", "corpus": "edge", "value": v,
            "texts": texts("edge_empty", v), "arms": SEARCH_ARMS,
            "questions": search_questions(v, random.Random(f"{SEED}:empty"), edge=True),
        })  # fmt: skip
    out += edges
    big = next(c for c in out if c["kind"] == "search100" and "requests" in c["corpus"])
    out += [truncated(big), truncated(next(c for c in edges if c["kind"] == "edge_search20"))]
    (DATA / "cases.json").write_text(json.dumps(out + nav, ensure_ascii=False, indent=1) + "\n")
    print(len(out), "cases,", len(nav), "navigation", file=sys.stderr)


if __name__ == "__main__":
    {"edge": edge, "cases": cases}[sys.argv[1]]()
