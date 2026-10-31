"""Ground truth for the shape tasks (tasks.json), independent of ast-grep and of OXIDE's
structural search: py-tree-sitter over every file OXIDE captures in the task's language,
with each task's meaning written out as plain checks over the syntax tree.

    truth.py OUT.json        # every shape task and the dev task
    truth.py --selftest      # fixtures with hand-counted answers

An item is [path, first_line, last_line] (1-based) of the matched node.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable, Iterator
from pathlib import Path

import tree_sitter_python
import tree_sitter_typescript
from tree_sitter import Language, Node, Parser

from oxide.capture import capture
from oxide.core.indexing import dialect

CACHE = Path.home() / ".cache/oxide"
REPOS = {
    "flask": CACHE / "contextbench/corpora/pallets__flask@7ee9ceb71e86",
    "seaborn": CACHE / "contextbench/corpora/mwaskom__seaborn@54cab15bdacf",
    "pytest": CACHE / "contextbench/corpora/pytest-dev__pytest@19ad5889353c",
    "requests": CACHE / "contextbench/corpora/psf__requests@0192aac24123",
    "xarray": CACHE / "contextbench/corpora/pydata__xarray@1757dffac2fa",
    "code-server": CACHE / "contextbench-ts/corpora-v2/coder__code-server@5d3c9edce436",
    "vue-core": CACHE / "contextbench-ts/corpora-v2/vuejs__core@0275dd329d24",
    "darkreader": CACHE / "contextbench-ts/corpora-v2/darkreader__darkreader@991883df4d59",
}
LANGUAGES = {
    "python": Language(tree_sitter_python.language()),
    "typescript": Language(tree_sitter_typescript.language_typescript()),
    "tsx": Language(tree_sitter_typescript.language_tsx()),
}
FUNCTIONS = {
    "function_declaration",
    "function_expression",
    "arrow_function",
    "method_definition",
    "generator_function_declaration",
    "generator_function",
}
LOOPS = {"for_statement", "for_in_statement", "while_statement", "do_statement"}


def nodes(root: Node) -> Iterator[Node]:
    stack = [root]
    while stack:
        node = stack.pop()
        yield node
        stack.extend(reversed(node.children))


def code(node: Node | None) -> list[Node]:
    """Named children that are not comments."""
    return [c for c in node.named_children if c.type != "comment"] if node else []


def text(node: Node | None) -> bytes:
    return node.text or b"" if node is not None else b""


def except_pass(n: Node) -> bool:
    if n.type not in ("except_clause", "except_group_clause"):
        return False
    body = next((c for c in reversed(n.named_children) if c.type == "block"), None)
    return [c.type for c in code(body)] == ["pass_statement"]


def isinstance_tuple(n: Node) -> bool:
    if n.type != "call" or text(n.child_by_field_name("function")) != b"isinstance":
        return False
    args = code(n.child_by_field_name("arguments"))
    return len(args) == 2 and args[1].type == "tuple"


def open_no_encoding(n: Node) -> bool:
    function = n.child_by_field_name("function")
    if n.type != "call" or function is None or function.type != "identifier":
        return False
    if text(function) != b"open":
        return False
    args = code(n.child_by_field_name("arguments"))
    return not any(
        a.type == "keyword_argument" and text(a.child_by_field_name("name")) == b"encoding"
        for a in args
    )


def mutable_default(n: Node) -> bool:
    if n.type not in ("default_parameter", "typed_default_parameter"):
        return False
    value = n.child_by_field_name("value")
    return value is not None and value.type in ("list", "dictionary", "set")


def raise_from_none(n: Node) -> bool:
    cause = n.child_by_field_name("cause")
    return n.type == "raise_statement" and cause is not None and cause.type == "none"


def await_in_loop(n: Node) -> bool:
    if n.type != "await_expression":
        return False
    a = n.parent
    while a is not None and a.type not in FUNCTIONS:
        if a.type in LOOPS:
            return True
        a = a.parent
    return False


def member_call(obj: bytes, prop: bytes) -> Callable[[Node], bool]:
    def check(n: Node) -> bool:
        function = n.child_by_field_name("function")
        if n.type != "call_expression" or function is None:
            return False
        if function.type != "member_expression":
            return False
        o, p = function.child_by_field_name("object"), function.child_by_field_name("property")
        return o is not None and o.type == "identifier" and text(o) == obj and text(p) == prop

    return check


def empty_catch(n: Node) -> bool:
    body = n.child_by_field_name("body")
    return n.type == "catch_clause" and body is not None and not code(body)


def json_parse_unguarded(n: Node) -> bool:
    if not member_call(b"JSON", b"parse")(n):
        return False
    a = n.parent
    while a is not None:
        if a.type == "try_statement":
            return False
        a = a.parent
    return True


CHECKS: dict[str, Callable[[Node], bool]] = {
    "S1": except_pass,
    "S2": isinstance_tuple,
    "S3": open_no_encoding,
    "S4": mutable_default,
    "S5": raise_from_none,
    "S6": await_in_loop,
    "S7": member_call(b"console", b"log"),
    "S8": empty_catch,
    "D1": json_parse_unguarded,
}


def matches(source: bytes, grammar: str, check: Callable[[Node], bool]) -> list[tuple[int, int]]:
    root = Parser(LANGUAGES[grammar]).parse(source).root_node
    return [(n.start_point[0] + 1, n.end_point[0] + 1) for n in nodes(root) if check(n)]


def truth(task: dict[str, str]) -> list[list[object]]:
    items: list[list[object]] = []
    for f in capture(REPOS[task["repo"]]).files:
        grammar = dialect(f.path)
        if grammar is None or (grammar == "python") != (task["lang"] == "python"):
            continue
        items += [[f.path, a, b] for a, b in matches(f.data, grammar, CHECKS[task["id"]])]
    return sorted(items, key=lambda i: (str(i[0]), int(str(i[1]))))


PY = b"""\
try:
    a()
except ValueError:  # ignored
    pass
try:
    b()
except Exception:
    # nothing to do
    pass
except KeyError:
    pass
    log()
s = "except: pass"
x = isinstance(v, (int, str))
y = isinstance(v, int)
open(p)
open(p, encoding="utf-8")
os.open(p)
def f(a, b=[], c: dict = {}, d=None, e={1}):
    raise KeyError(
        a
    ) from None
g = lambda q=[]: q
"""
TS = b"""\
try { a(); } catch (e) {}
try { b(); } catch { /* ignore */ }
try { c(); } catch (e) { log(e); }
const s = "catch (e) {}"; // console.log(1)
async function run(items) {
  for (const i of items) {
    await work(i);
    items.map(async (j) => { await other(j); });
  }
  while (await more()) {}
  await done();
}
console.log("x");
logger.console.log("y");
const v = JSON.parse(t);
try { f(() => JSON.parse(u)); } catch (e) {}
"""


def selftest() -> None:
    def lines(src: bytes, grammar: str, task: str) -> list[int]:
        return [a for a, _ in sorted(matches(src, grammar, CHECKS[task]))]

    assert lines(PY, "python", "S1") == [3, 7], lines(PY, "python", "S1")
    assert lines(PY, "python", "S2") == [14]
    assert lines(PY, "python", "S3") == [16]
    assert lines(PY, "python", "S4") == [19, 19, 19, 23]
    assert sorted(matches(PY, "python", CHECKS["S5"])) == [(20, 22)]
    assert lines(TS, "typescript", "S6") == [7, 10]
    assert lines(TS, "typescript", "S7") == [13]
    assert lines(TS, "typescript", "S8") == [1, 2, 16]
    assert lines(TS, "typescript", "D1") == [15]
    print("selftest ok")


def main() -> None:
    if sys.argv[1] == "--selftest":
        selftest()
        return
    tasks = json.loads((Path(__file__).parents[1] / "tasks.json").read_text())
    shape = [t for t in [*tasks["tasks"], tasks["dev"]] if t["kind"] == "shape"]
    out = {t["id"]: truth(t) for t in shape}
    Path(sys.argv[1]).write_text(json.dumps(out, indent=1) + "\n")
    print({k: len(v) for k, v in out.items()})


if __name__ == "__main__":
    main()
