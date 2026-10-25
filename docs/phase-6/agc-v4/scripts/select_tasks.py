"""AGC v4 task selection: fixed rules over names and text, run before any agent run.

    python select_tasks.py     # writes ../results/selection.json (seeded pools)

Library code only (pytest `src/`, flask `src/`, vue-core `packages/*/src` without test,
playground and type-test packages). Targets of AGC v2 and v3 are excluded. No rule reads
OXIDE edges or any agent outcome. Five types:

- lookup: a function or method, name defined once, with a docstring (Python) or a JSDoc
  block (TypeScript); the question paraphrases it without the name.
- callers: Python, v3's dispatch rule (name defined >= 3 times, 4-20 `self.`/`cls.`/
  `super().` sites); TypeScript, a function or method name defined >= 2 times and called
  at 3-15 library sites.
- impact: v3's rule (module-level function, name defined once, 3-8 library call sites).
- callees: a Python function or method whose body makes 5-15 calls, at least 2 of them to
  names defined more than once in library code.
- overrides: a Python class whose body names an in-repo base and defines >= 3 methods, at
  least 2 of them also defined by some library class it names as a base.

Truth is written by hand down each seeded pool (preregistration § Tasks).
"""

import json
import random
import re
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "agc/scripts"))
from agc import Agc  # noqa: E402

EVAL = Path.home() / "Projects/oxide-eval-data"
REPOS = EVAL / "oxide-structural-trial/repos"
STORE = EVAL / "oxide-agc4-eval/data"
SEED = 2026102506
SKIP_PKGS = {"dts-built-test", "dts-test", "sfc-playground", "template-explorer", "runtime-test"}
USED = {"default_user_agent", "create_url_adapter", "pytest_collection_modifyitems",
        "should_bypass_proxies", "get_auth_from_url", "ensure_sync", "pytest_runtest_protocol",
        "send", "iter_markers", "_consider_importhook", "parse_header_links"}  # fmt: skip
CALL = re.compile(r"(?<![\w.])([A-Za-z_]\w*)\(|\.([A-Za-z_]\w*)\(")
NOT_CALLS = {"if", "for", "while", "return", "print", "len", "isinstance", "str", "int", "list",
             "dict", "set", "tuple", "super", "getattr", "hasattr", "type", "bool", "repr",
             "sorted", "range", "enumerate", "zip", "min", "max", "any", "all",
             "function"}  # fmt: skip


def library(path: str, repo: str) -> bool:
    parts = path.split("/")
    if repo == "vue-core":
        ok = len(parts) > 3 and parts[0] == "packages" and parts[2] == "src"
        ok = ok and parts[1] not in SKIP_PKGS and path.endswith((".ts", ".tsx"))
        return ok and "__tests__" not in parts and not path.endswith(".d.ts")
    name = parts[-1]
    return (path.startswith("src/") and path.endswith(".py") and "tests" not in parts
            and not name.startswith("test_") and name != "conftest.py")  # fmt: skip


def pools(repo: str) -> dict[str, list[dict]]:
    agc = Agc(REPOS / repo, STORE)
    code = [k for k in agc.symbols if library(agc.info[k][4], repo) and not agc.info[k][8]
            and agc.info[k][2] in ("function", "method") and "~" not in k
            and not agc.info[k][3].startswith("__")]  # fmt: skip
    classes = [k for k in agc.symbols if library(agc.info[k][4], repo)
               and agc.info[k][2] == "class" and "~" not in k]  # fmt: skip
    defined = Counter(agc.info[k][3] for k in code)
    texts = {p: (REPOS / repo / p).read_text("utf-8", "replace") for p in agc.files
             if library(p, repo)}  # fmt: skip
    py = repo != "vue-core"

    def body(k: str) -> str:
        return (REPOS / repo / agc.info[k][4]).read_bytes()[agc.info[k][5] : agc.info[k][6]].decode(
            "utf-8", "replace")  # fmt: skip

    def sites(name: str, own: bool) -> int:
        n = re.escape(name)
        pattern = (rf"\b(?:self|cls|super\(\))\.{n}\(" if own
                   else rf"(?<!def )(?<!function )(?<![\w.]){n}\(|\.{n}\(")  # fmt: skip
        return sum(len(re.findall(pattern, t)) for t in texts.values())

    out: dict[str, list[dict]] = {t: [] for t in ("lookup", "callers", "impact", "callees",
                                                   "overrides")}  # fmt: skip
    for k in code:
        name, kind, text = agc.info[k][3], agc.info[k][2], body(k)
        if name in USED:
            continue
        row = {"repo": repo, "key": k, "name": name}
        head = (REPOS / repo / agc.info[k][4]).read_bytes()[: agc.info[k][5]].decode(
            "utf-8", "replace").rstrip()  # fmt: skip
        documented = '"""' in "\n".join(text.split("\n")[1:4]) if py else head.endswith("*/")
        if defined[name] == 1 and documented:
            out["lookup"].append(row)
        if py and kind == "method" and defined[name] >= 3 and 4 <= sites(name, True) <= 20:
            out["callers"].append(row)
        if not py and defined[name] >= 2 and 3 <= sites(name, False) <= 15:
            out["callers"].append(row)
        top = "." not in Agc.qual(k)
        if kind == "function" and top and defined[name] == 1 and 3 <= sites(name, False) <= 8:
            out["impact"].append(row)
        if py:
            called = [a or b for a, b in CALL.findall("\n".join(text.split("\n")[1:]))]
            called = [c for c in called if c not in NOT_CALLS]
            if 5 <= len(called) <= 15 and sum(defined[c] > 1 for c in set(called)) >= 2:
                out["callees"].append(row)
    if py:
        names = {agc.info[c][3]: c for c in classes}
        for c in classes:
            head = body(c).split("\n", 1)[0]
            bases = [b.strip() for b in head[head.find("(") + 1 : head.rfind(")")].split(",")
                     if "(" in head]  # fmt: skip
            bases = [names[b] for b in bases if b in names]
            own = {agc.info[k][3] for k in code if k.startswith(c + ".") and k.count(".") ==
                   c.count(".") + 1}  # fmt: skip
            inherited = {agc.info[k][3] for b in bases for k in code if k.startswith(b + ".")}
            if bases and len(own) >= 3 and len(own & inherited) >= 2:
                out["overrides"].append({"repo": repo, "key": c, "name": agc.info[c][3]})
    for t in out:
        out[t].sort(key=lambda r: r["key"])
        random.Random(f"{SEED}-{repo}-{t}").shuffle(out[t])
    return out


def main() -> None:
    picked = {f"{r}/{t}": pool for r in ("pytest", "flask", "vue-core")
              for t, pool in pools(r).items() if pool}  # fmt: skip
    (HERE.parent / "results/selection.json").write_text(json.dumps(picked, indent=1) + "\n")
    for name, pool in picked.items():
        print(name, len(pool), [c["key"].split("#")[1] for c in pool[:6]])


if __name__ == "__main__":
    main()
