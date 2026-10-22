"""Cases for the controlled stage (preregistration.md § Conditions, § Questions).

    build.py cases     DATA/cases.json from the private index (run corpora.py index first)

A condition renders one API value in every arm. A cut is placed at the same point of
the value in every arm (a sentinel inside one string value, so the text before it is the
arm's own rendering), and arms differ only in format. Gold comes from the value and from
what each arm's visible text still carries, never from a model.
"""

from __future__ import annotations

import copy
import json
import math
import random
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from corpora import DATA, STORE, tasks
from render import CONTEXT_ARMS, context_text, more, plain, search_text

import oxide
from oxide.mcp_server import DEFAULT_BUDGET, DEFAULT_LIMIT, MAX_RESULTS

SEED = 20261021
AGENT_SEARCH_ARMS = ("C", "CM", "P", "E")  # CL: deterministic and live stages only
EDGE = Path.home() / "Projects/oxide-eval-data/oxide-toon-eval/edge-repo"  # TOON study's
S = "@@CUT@@"
CUTS = ("cut_entry", "cut_nested", "cut_boundary")
HEAD_STATUS = ("CM", "E")  # arms whose completeness status precedes the entries
SPECIAL = ('"', "\\", ": ", "\t", "'", "#", "`", "{")


def q(text: str, grade: str) -> dict[str, Any]:
    return {"text": text, "grade": grade}


Q_CUT = ("Was this text cut off, interrupted or otherwise damaged after the tool produced "
         "it, so that you are not seeing the tool's complete response? Answer true or false.")  # fmt: skip
Q_MORE = ("Did the tool itself report that more matching results exist beyond the ones it "
          "returned (because of its result limit)? Answer true, false, or null if the visible "
          "text does not tell.")  # fmt: skip
Q_OMIT = ("Did the tool itself report leaving out (omitting) candidate code to fit its size "
          "budget? Answer true, false, or null if the visible text does not tell.")  # fmt: skip
Q_ENTRIES = "How many result entries are completely visible?"
Q_SECTIONS = ("How many `### ` source sections are completely visible, each with its whole "
              "source text?")  # fmt: skip


# --- search ---


def sentinel(v: dict[str, Any], k: int, field: str) -> tuple[dict[str, Any], bool]:
    """A copy of ``v`` with the sentinel inside entry ``k``'s ``field``, and whether the
    cut falls inside a multi-byte character (then a partial UTF-8 sequence is kept)."""
    out = copy.deepcopy(v)
    entity = out["entries"][k]["entity"]
    if field == "name":
        name = entity["name"]
        wide = [n for n, ch in enumerate(name) if ord(ch) > 127]
        at = wide[0] if wide else max(1, len(name) // 2)
        entity["name"] = name[:at] + S + name[at:]
        return out, bool(wide)
    if field == "digest":
        digest = entity["source"]["digest"]
        entity["source"]["digest"] = digest[:17] + S + digest[17:]
    else:  # the key, the first string of the entry
        entity["key"] = S + entity["key"]
    return out, False


def boundary(text: str, at: int) -> int:
    """Just after the closing brace of the entry before the one containing ``at``."""
    return text.rfind("}", 0, at) + 1


def entry_start(text: str, at: int) -> int:
    start = boundary(text, at)
    while text[start] in ", \n":
        start += 1
    return start


def cut_text(v: dict[str, Any], arm: str, condition: str, k: int) -> str:
    if condition == "cut_entry":
        marked, wide = sentinel(v, k, "name")
        text = search_text(marked, arm)
        at = text.index(S)
        if wide:  # keep the first byte of the character the transport broke
            tail = text[at + len(S)].encode()[:1]
            return (text[:at].encode() + tail).decode(errors="replace")
        return text[:at]
    if condition == "cut_nested":
        text = search_text(sentinel(v, k, "digest")[0], arm)
        return text[: text.index(S)]
    text = search_text(sentinel(v, k, "key")[0], arm)
    return text[: boundary(text, text.index(S))]


def elided(v: dict[str, Any], arm: str, a: int, b: int) -> str:
    """Entries ``a..b-1`` replaced by a client-style marker; head and tail kept."""
    full = search_text(v, arm)
    first = search_text(sentinel(v, a, "key")[0], arm)
    last = search_text(sentinel(v, b, "key")[0], arm)
    end = boundary(first, first.index(S))
    start = entry_start(last, last.index(S))  # before the sentinel, so equal in ``full``
    gone = len(full[end:start])
    return f"{full[:end]}\n…[{gone} characters omitted]…\n{full[start:]}"


def file_question(entries: list[dict[str, Any]], visible: list[int],
                  rng: random.Random) -> tuple[dict[str, Any], str] | None:  # fmt: skip
    names = [e["entity"]["name"] for e in entries]
    choices = [n for n in visible
               if entries[n]["entity"]["source"] and names.count(names[n]) == 1]  # fmt: skip
    if not choices:
        return None
    e = entries[rng.choice(choices)]["entity"]
    text = f"What is the source file path of the entry whose entity name is `{e['name']}`?"
    return q(text, "exact"), e["source"]["path"]


def search_cases(prefix: str, corpus: str, v: dict[str, Any], kind: str,
                 conditions: tuple[str, ...]) -> list[dict[str, Any]]:  # fmt: skip
    rng = random.Random(f"{SEED}:{prefix}:{kind}")
    entries, n = v["entries"], len(v["entries"])
    k = rng.randint(math.ceil(n / 3), max(math.ceil(n / 3), (2 * n) // 3))
    located = [i for i in range(k, n) if entries[i]["entity"]["source"]]
    wide = [i for i in range(1, n) if max(map(ord, entries[i]["entity"]["name"]), default=0) > 127]
    a, b = 3, n - 3
    out = []
    for condition in conditions:
        if condition in ("full", "exhaust"):
            texts = {arm: search_text(v, arm) for arm in AGENT_SEARCH_ARMS}
            visible = list(range(n))
        elif condition == "elided":
            texts = {arm: elided(v, arm, a, b) for arm in AGENT_SEARCH_ARMS}
            visible = [*range(a), *range(b, n)]
        else:
            at = {"cut_nested": located[0], "cut_entry": wide[0] if wide else k}.get(condition, k)
            texts = {arm: cut_text(v, arm, condition, at) for arm in AGENT_SEARCH_ARMS}
            visible = list(range(at))
        status_lost = condition in CUTS
        questions = [q(Q_CUT, "bool"), q(Q_MORE, "nullable"), q(Q_ENTRIES, "int")]
        extra = file_question(entries, visible, rng)
        golds = {
            arm: [condition not in ("full", "exhaust"),
                  None if status_lost and arm not in HEAD_STATUS else more(v),
                  len(visible)] + ([extra[1]] if extra else [])
            for arm in AGENT_SEARCH_ARMS
        }  # fmt: skip
        out.append({
            "id": f"{prefix}:{kind}:{condition}", "kind": "search", "condition": condition,
            "corpus": corpus, "texts": texts, "arms": AGENT_SEARCH_ARMS,
            "questions": questions + ([extra[0]] if extra else []), "golds": golds,
            "n": n, "more": more(v),
        })  # fmt: skip
    return out


def exhausted(repo: oxide.Repository, s20: Any) -> dict[str, Any] | None:
    """A complete search that returns exactly its limit and has no more matches: an
    identifier from the first entries, searched with ``limit`` = its match count."""
    best = None
    for e in s20.entries[:10]:
        found = repo.search(e.entity.name, limit=MAX_RESULTS)
        hits = found.lexical.returned
        if not found.lexical.truncated and hits >= 3 and (best is None or hits < best[1]):
            best = (e.entity.name, hits)
    if best is None:
        return None
    v = plain(repo.search(best[0], limit=best[1]))
    assert len(v["entries"]) == best[1] and not more(v), best
    return v


# --- context ---


def copy_line(
    items: list[dict[str, Any]], root: Path, rng: random.Random
) -> tuple[str, str] | None:
    """A source line shown verbatim in ``items`` and in the file (as the TOON study's)."""
    choices: list[tuple[bool, str, int, str]] = []
    for item in items:
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
    _, path, n, text = rng.choice([c for c in choices if c[0]] or choices)
    return (f"Copy exactly the source text of line {n} of the file {path}, as it appears in "
            f"the source (one line, without its line break).", text)  # fmt: skip


def bundle_cases(prefix: str, corpus: str, v: dict[str, Any], root: Path) -> list[dict[str, Any]]:
    rng = random.Random(f"{SEED}:{prefix}:bundle")
    items, m = v["items"], len(v["items"])
    j = rng.randint(max(1, m // 3), max(1, (2 * m) // 3))
    full = {arm: context_text(v, arm) for arm in CONTEXT_ARMS}
    head = len(full["C"]) - sum(len(i["text"]) for i in items)
    start = head + sum(len(i["text"]) for i in items[:j])  # item j's heading in C
    shift = {arm: t.index(full["C"]) for arm, t in full.items()}  # E's announcing line
    body = items[j]["text"].split("\n")
    lines = [n for n, line in enumerate(body[1:], 1) if len(line.strip()) >= 10] or [0]
    n = rng.choice(lines)
    mid = start + sum(len(line) + 1 for line in body[:n]) + len(body[n]) // 2
    out = []
    for condition, texts, shown in (
        ("full", full, m),
        ("cut_line", {arm: t[: shift[arm] + mid] for arm, t in full.items()}, j),
        ("cut_boundary", {arm: t[: shift[arm] + start] for arm, t in full.items()}, j),
    ):
        line = copy_line(items[:shown], root, rng)
        questions = [q(Q_CUT, "bool"), q(Q_OMIT, "nullable"), q(Q_SECTIONS, "int")]
        gold = [condition != "full", bool(v["omissions"]), shown]
        if line:
            questions.append(q(line[0], "exact"))
            gold.append(line[1])
        out.append({
            "id": f"{prefix}:bundle:{condition}", "kind": "bundle", "condition": condition,
            "corpus": corpus, "texts": texts, "arms": CONTEXT_ARMS, "questions": questions,
            "golds": dict.fromkeys(CONTEXT_ARMS, gold), "n": m, "more": bool(v["omissions"]),
        })  # fmt: skip
    return out


# --- corpora ---


def cases() -> None:
    out: list[dict[str, Any]] = []
    search_conditions = ("full", *CUTS, "elided")
    for task, root in tasks():
        with oxide.open_repository(root, data_dir=STORE, create=False) as repo:
            s20 = repo.search(task["query"], limit=DEFAULT_LIMIT)
            found = search_cases(task["id"], task["corpus"], plain(s20), "search20",
                                 search_conditions)  # fmt: skip
            exhaust = exhausted(repo, s20)
            if exhaust:
                found += search_cases(task["id"], task["corpus"], exhaust, "exhaust",
                                      ("exhaust",))  # fmt: skip
            bundle = plain(repo.build_context(task["query"], DEFAULT_BUDGET))
            found += bundle_cases(task["id"], task["corpus"], bundle, root)
        gold = sorted({g["file"] for g in task["gold"] if g.get("verified", True)})
        for condition in ("full", "cut_boundary"):
            source = next(c for c in found if c["id"].endswith(f"search20:{condition}"))
            out.append({
                "id": f"{task['id']}:nav:{condition}", "kind": "nav", "condition": condition,
                "corpus": task["corpus"], "issue": task["query"], "gold_files": gold,
                "texts": source["texts"], "arms": AGENT_SEARCH_ARMS, "questions": [],
            })  # fmt: skip
        out += found
        print(task["id"], len(found), "exhaust" if exhaust else "-", file=sys.stderr)
    with oxide.open_repository(EDGE, data_dir=STORE) as repo:
        repo.index()
        query = "parse ligne config größe format quoted separator"
        hints = {"symbols": ("Größe", "DoesNotExist"), "paths": ("src/naïve café.py",)}
        v = plain(repo.search(query, limit=DEFAULT_LIMIT, **hints))
        out += search_cases("edge", "edge", v, "search20", ("full", "cut_entry", "cut_boundary"))
        bundle = plain(repo.build_context(query, DEFAULT_BUDGET, **hints))
        out += bundle_cases("edge", "edge", bundle, EDGE)
    (DATA / "cases.json").write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n")
    print(len(out), "cases", file=sys.stderr)


if __name__ == "__main__":
    {"cases": cases}[sys.argv[1]]()
