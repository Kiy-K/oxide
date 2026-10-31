"""Classify agent search patterns (agent_searches.py output) by what could answer them.

Each pattern is split into its top-level regex alternatives (branches), and each branch
is reduced to a skeleton: regex groups→`⟨…⟩`, `\\(`→`(`, `\\.`→`.`, `\\s*`/`\\s+`/`\\s`→` `, `\\b`/`^`/`$`
dropped, `.*`/`.+`→`…`. A skeleton is, first match wins:

- definition: `def X` / `class X` / `function X` / `interface X` ... → OXIDE entity
- heritage:   `class …(X` → OXIDE heritage `references`
- import:     `import X` / `from X` → OXIDE `imports`
- call:       `X(` or `.X(` with nothing after the parenthesis → OXIDE `calls`
- name:       a bare identifier or dotted name → entity name / lexical search
- syntactic:  code in a syntax role OXIDE stores no fact for: assignment or keyword
              argument `X =`, a call constrained by its arguments `X(…Y`, a decorator
              `@X`, or `raise|except|return|yield|await|assert|with|lambda|isinstance X`
- cooccur:    two identifiers joined by `…` (one line; may or may not be structural)
- text:       anything else (messages, prose, docs, punctuation)

A search is `syntactic` if any branch is; else `cooccur` if any branch is; else its
first branch class in ORDER. Rules were written looking at the dev split only.

usage: classify.py SEARCHES_JSONL OUT_JSONL
"""

from __future__ import annotations

import json
import re
import sys

ID = r"[A-Za-z_][A-Za-z0-9_]*"
DOTTED = rf"{ID}(?:\.{ID})*"
NAMEISH = r"[A-Za-z0-9_.⟨⟩|…\[\]\\+*?-]+"  # a name, possibly with groups or wildcards
KEYWORDS = "raise|except|return|yield|await|assert|with|lambda|while|elif|isinstance|issubclass"

RULES = [
    (
        "definition",
        re.compile(rf"(?:async )?(?:def|class|function|interface|type|enum) +{NAMEISH}(?: ?\()?"),
        "full",
    ),
    ("heritage", re.compile(rf"class +{NAMEISH}\({DOTTED}"), "start"),
    ("import", re.compile(rf"(?:from|import) +{DOTTED}"), "start"),
    ("call", re.compile(rf"\.?{DOTTED}\("), "full"),
    ("name", re.compile(r"\.?[A-Za-z_][A-Za-z0-9_.⟨⟩|]*"), "full"),
    (
        "syntactic",
        re.compile(
            rf"{DOTTED} *=(?!=)"  # assignment or keyword argument
            rf"|{DOTTED}\((?=[^)\s])"  # call constrained by an argument
            rf"|{DOTTED}\["  # subscript
            rf"|{ID} ?[*+/%] ?{ID}|[*+/%]{ID}"  # operator applied to a name
            rf"|^{DOTTED}[,)]$"  # name in argument position
            rf"|^ *@{DOTTED}"  # decorator
            rf"|\b(?:{KEYWORDS})\b[ (]+[A-Za-z_(]"
        ),
        "search",
    ),
    ("cooccur", re.compile(rf"{ID}…+.*{ID}"), "search"),
]
ORDER = ["syntactic", "cooccur", "definition", "heritage", "import", "call", "name", "text"]


def branches(pattern: str) -> list[str]:
    """Top-level `|` alternatives, ignoring escaped bars and bars inside groups."""
    out, depth, current, i = [], 0, "", 0
    while i < len(pattern):
        c = pattern[i]
        if c == "\\" and i + 1 < len(pattern):
            current += pattern[i : i + 2]
            i += 2
            continue
        if c in "([":
            depth += 1
        elif c in ")]":
            depth = max(0, depth - 1)
        if c == "|" and depth == 0:
            out.append(current)
            current = ""
        else:
            current += c
        i += 1
    out.append(current)
    return [b for b in out if b]


def skeleton(branch: str, fixed: bool) -> str:
    if fixed:
        return branch.strip()
    s = re.sub(r"\\s[*+?]?|\[ \\t\][*+?]?|\[ \][*+?]?", " ", branch)
    s = re.sub(r"\.[*+]\??", "…", s)
    s = re.sub(r"(?<!\\)\((?:\?:)?", "⟨", s)  # regex groups, not literal parentheses
    s = re.sub(r"(?<!\\)\)", "⟩", s)
    s = s.replace("\\b", "").replace("^", "").replace("$", "")
    s = re.sub(r"\\(.)", r"\1", s)
    return re.sub(r" +", " ", s).strip()


def branch_class(branch: str, fixed: bool) -> str:
    s = skeleton(branch, fixed)
    for name, rule, mode in RULES:
        hit = {"full": rule.fullmatch, "start": rule.match, "search": rule.search}[mode](s)
        if hit:
            return name
    return "text"


def classify(pattern: str, fixed: bool) -> tuple[str, list[str]]:
    classes = [branch_class(b, fixed) for b in ([pattern] if fixed else branches(pattern))]
    return (min(classes, key=ORDER.index) if classes else "text"), classes


def main() -> None:
    rows = [json.loads(line) for line in open(sys.argv[1])]
    with open(sys.argv[2], "w") as out:
        for row in rows:
            row["class"], row["branches"] = classify(row["pattern"], row["fixed"])
            out.write(json.dumps(row) + "\n")


if __name__ == "__main__":
    main()
