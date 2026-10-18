"""The preregistered shape queries, each in three arms:

- `rule`:  an ast-grep-py 0.51.0 `find_all` config (a declarative rule, no code)
- `ts`:    a tree-sitter query over OXIDE's pinned grammars; `@m` is the match. `pure`
           says whether the query language alone expresses it; otherwise `check` is a
           Python predicate on the `@m` node (code an agent couldn't send to a tool)
- `regex`: the regex an agent would plausibly run with rg (`multiline` = `rg -U`),
           or None when no regex can express the query

`control` marks queries expected to be easy for regex (they balance the suite).
`python queries.py` runs every arm on synthetic fixtures with known answers.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from tree_sitter import Node

Check = Callable[[Node], bool]


@dataclass(frozen=True)
class Q:
    id: str
    lang: str  # "python" | "typescript" (tsx files use the tsx grammar)
    intent: str
    rule: dict[str, Any]
    ts: str
    pure: bool
    regex: str | None
    multiline: bool = False
    control: bool = False
    check: Check | None = None


def _ancestors(node: Node) -> list[Node]:
    out, cur = [], node.parent
    while cur is not None:
        out.append(cur)
        cur = cur.parent
    return out


def _inside_before(node: Node, kinds: set[str], stop: set[str]) -> bool:
    for a in _ancestors(node):
        if a.type in stop:
            return False
        if a.type in kinds:
            return True
    return False


def _only_comments(block: Node | None) -> bool:
    return block is not None and all(c.type == "comment" for c in block.named_children)


def _no_encoding(call: Node) -> bool:
    args = call.child_by_field_name("arguments")
    for a in args.named_children if args is not None else []:
        name = a.child_by_field_name("name") if a.type == "keyword_argument" else None
        if name is not None and name.text == b"encoding":
            return False
    return True


_FUNCS = {"function_definition", "lambda"}
_TS_FUNCS = {
    "function_declaration",
    "function_expression",
    "arrow_function",
    "method_definition",
    "generator_function_declaration",
    "generator_function",
}
_TS_LOOPS = {"for_statement", "for_in_statement", "while_statement", "do_statement"}

QUERIES: list[Q] = [
    # ---- Python
    Q(
        "py-except-pass",
        "python",
        "an except clause whose only statement is `pass`",
        {
            "rule": {
                "kind": "except_clause",
                "has": {
                    "kind": "block",
                    "all": [
                        {"has": {"kind": "pass_statement", "nthChild": 1}},
                        {"not": {"has": {"nthChild": 2}}},
                    ],
                },
            }
        },
        "(except_clause (block . (pass_statement) .)) @m",
        True,
        r"except[^\n]*:\s*\n?\s*pass\b",
        multiline=True,
    ),
    Q(
        "py-bare-except",
        "python",
        "a bare `except:` with no exception type",
        {"rule": {"kind": "except_clause", "has": {"kind": "block", "nthChild": 1}}},
        "(except_clause . (block)) @m",
        True,
        r"^\s*except\s*:",
        control=True,
    ),
    Q(
        "py-isinstance-tuple",
        "python",
        "`isinstance` whose second argument is a tuple",
        {"rule": {"pattern": "isinstance($X, $T)"}, "constraints": {"T": {"kind": "tuple"}}},
        "((call function: (identifier) @f arguments: (argument_list . (_) . (tuple) .)) @m"
        ' (#eq? @f "isinstance"))',
        True,
        r"isinstance\([^,]+,\s*\(",
    ),
    Q(
        "py-mutable-default",
        "python",
        "a parameter defaulting to a list/dict/set literal",
        {
            "rule": {
                "any": [{"kind": "default_parameter"}, {"kind": "typed_default_parameter"}],
                "has": {
                    "field": "value",
                    "any": [{"kind": "list"}, {"kind": "dictionary"}, {"kind": "set"}],
                },
            }
        },
        "[(default_parameter value: [(list) (dictionary) (set)])"
        " (typed_default_parameter value: [(list) (dictionary) (set)])] @m",
        True,
        r"def [^\n]*=\s*[\[{]",
    ),
    Q(
        "py-open-no-encoding",
        "python",
        "a call of builtin `open` without `encoding=`",
        {
            "rule": {
                "pattern": "open($$$A)",
                "not": {
                    "has": {
                        "kind": "argument_list",
                        "has": {
                            "kind": "keyword_argument",
                            "has": {"field": "name", "regex": "^encoding$"},
                        },
                    }
                },
            }
        },
        '((call function: (identifier) @f) @m (#eq? @f "open"))',
        False,
        r"(^|[^.\w])open\((?!.*encoding)",
        check=_no_encoding,
    ),
    Q(
        "py-eq-none",
        "python",
        "`== None` or `!= None` (instead of `is`)",
        {
            "rule": {
                "any": [
                    {"pattern": "$A == None"},
                    {"pattern": "$A != None"},
                    {"pattern": "None == $A"},
                    {"pattern": "None != $A"},
                ]
            }
        },
        '[(comparison_operator ["==" "!="] (none)) (comparison_operator (none) ["==" "!="])] @m',
        True,
        r"[=!]=\s*None\b|\bNone\s*[=!]=",
    ),
    Q(
        "py-raise-from-none",
        "python",
        "`raise … from None`",
        {"rule": {"pattern": "raise $E from None"}},
        "(raise_statement cause: (none)) @m",
        True,
        r"raise .* from None\b",
        control=True,
    ),
    Q(
        "py-return-in-finally",
        "python",
        "a `return` inside a `finally` block (same function)",
        {
            "rule": {
                "kind": "return_statement",
                "inside": {
                    "kind": "finally_clause",
                    "stopBy": {"any": [{"kind": "function_definition"}, {"kind": "lambda"}]},
                },
            }
        },
        "(return_statement) @m",
        False,
        None,
        check=lambda n: _inside_before(n, {"finally_clause"}, _FUNCS),
    ),
    # ---- TypeScript
    Q(
        "ts-empty-catch",
        "typescript",
        "a catch clause with no statements (comments allowed)",
        # `has` also visits the unnamed `{`/`}` tokens, so they are excluded explicitly
        {
            "rule": {
                "kind": "catch_clause",
                "has": {
                    "field": "body",
                    "kind": "statement_block",
                    "not": {"has": {"not": {"any": [{"kind": "comment"}, {"regex": "^[{}]$"}]}}},
                },
            }
        },
        "(catch_clause body: (statement_block)) @m",
        False,
        r"catch\s*(\([^)]*\))?\s*\{\s*\}",
        multiline=True,
        check=lambda n: _only_comments(n.child_by_field_name("body")),
    ),
    Q(
        "ts-json-parse-unguarded",
        "typescript",
        "`JSON.parse(…)` not inside any try statement",
        {
            "rule": {
                "pattern": "JSON.parse($$$A)",
                "not": {"inside": {"kind": "try_statement", "stopBy": "end"}},
            }
        },
        "((call_expression function: (member_expression object: (identifier) @o"
        ' property: (property_identifier) @p)) @m (#eq? @o "JSON") (#eq? @p "parse"))',
        False,
        r"JSON\.parse\(",
        check=lambda n: not _inside_before(n, {"try_statement"}, set()),
    ),
    Q(
        "ts-as-any",
        "typescript",
        "a cast to `any` (`x as any`)",
        {"rule": {"pattern": "$X as any"}},
        '((as_expression (predefined_type) @t) @m (#eq? @t "any"))',
        True,
        r"\bas any\b",
        control=True,
    ),
    Q(
        "ts-non-null-member",
        "typescript",
        "member access on a non-null assertion (`x!.y`)",
        {
            "rule": {
                "kind": "member_expression",
                "has": {"field": "object", "kind": "non_null_expression"},
            }
        },
        "(member_expression object: (non_null_expression)) @m",
        True,
        r"[\w\])]!\.[A-Za-z_$]",
    ),
    Q(
        "ts-await-in-loop",
        "typescript",
        "`await` inside a loop body (same function)",
        {
            "rule": {
                "kind": "await_expression",
                "inside": {
                    "any": [{"kind": k} for k in sorted(_TS_LOOPS)],
                    "stopBy": {"any": [{"kind": k} for k in sorted(_TS_FUNCS)]},
                },
            }
        },
        "(await_expression) @m",
        False,
        None,
        check=lambda n: _inside_before(n, _TS_LOOPS, _TS_FUNCS),
    ),
    Q(
        "ts-async-promise-executor",
        "typescript",
        "`new Promise(async …)`",
        {
            "rule": {"pattern": "new Promise($E)"},
            "constraints": {
                "E": {
                    "any": [{"kind": "arrow_function"}, {"kind": "function_expression"}],
                    "regex": "^async",
                }
            },
        },
        "((new_expression constructor: (identifier) @c"
        " arguments: (arguments . [(arrow_function) (function_expression)] @f .)) @m"
        ' (#eq? @c "Promise") (#match? @f "^async"))',
        True,
        r"new Promise\(\s*async",
        control=True,
    ),
    Q(
        "ts-console-log",
        "typescript",
        "a `console.log(…)` call",
        {"rule": {"pattern": "console.log($$$A)"}},
        "((call_expression function: (member_expression object: (identifier) @o"
        ' property: (property_identifier) @p)) @m (#eq? @o "console") (#eq? @p "log"))',
        True,
        r"console\.log\(",
        control=True,
    ),
    Q(
        "ts-useeffect-empty-deps",
        "typescript",
        "`useEffect(fn, [])` with empty dependencies",
        {"rule": {"pattern": "useEffect($F, [])"}},
        "((call_expression function: (identifier) @f arguments: (arguments (_) . (array) @a .))"
        ' @m (#eq? @f "useEffect") (#eq? @a "[]"))',
        True,
        r"\},\s*\[\]\)",
    ),
]
QUERIES_BY_ID = {q.id: q for q in QUERIES}

# ---- fixtures: source and, per query, the expected 0-based start lines
PY_FIXTURE = """\
try:
    a()
except:
    pass
try:
    b()
except ValueError:  # ignored
    pass
try:
    c()
except Exception:
    pass
    log()
s = "except: pass"  # except: pass
x = isinstance(v, (int, str))
y = isinstance(f(a, b), (int,))
z = isinstance(v, int)
def f(a, b=[], c: dict = {}, d=None, e={1}):
    pass
g = lambda q=[]: q
open(p)
open(p, encoding="utf-8")
os.open(p)
fh.open(p)
open(
    p,
    "r",
)
if v == None or None != w or v is None:
    pass
t = "x == None"
raise KeyError(k) from None
def h():
    try:
        return 1
    finally:
        return 2
def k():
    try:
        pass
    finally:
        def inner():
            return 3
"""
PY_EXPECT = {
    "py-except-pass": [2, 6],
    "py-bare-except": [2],
    "py-isinstance-tuple": [14, 15],
    "py-mutable-default": [17, 17, 17, 19],
    "py-open-no-encoding": [20, 24],
    "py-eq-none": [28, 28],
    "py-raise-from-none": [31],
    "py-return-in-finally": [36],
}
TS_FIXTURE = """\
try { a(); } catch (e) {}
try { b(); } catch { /* ignore */ }
try { c(); } catch (e) { log(e); }
const s = "catch (e) {}"; // catch {}
const v = JSON.parse(text);
try { JSON.parse(t2); } catch (e) {}
const w = x as any;
// as any
const n = foo!.bar;
const m = a !== b;
async function run(items) {
  for (const i of items) {
    await work(i);
    items.map(async (j) => { await other(j); });
  }
  await done();
}
const p = new Promise(async (resolve) => resolve(1));
const q = new Promise((resolve) => resolve(1));
console.log("x");
logger.console.log("y");
useEffect(() => {
  start();
}, []);
useEffect(() => {}, [dep]);
"""
TS_EXPECT = {
    "ts-empty-catch": [0, 1, 5],
    "ts-json-parse-unguarded": [4],
    "ts-as-any": [6],
    "ts-non-null-member": [8],
    "ts-await-in-loop": [12],
    "ts-async-promise-executor": [17],
    "ts-console-log": [19],
    "ts-useeffect-empty-deps": [21],
}


def _selftest() -> None:
    import arms

    failures = 0
    for lang, source, expect in (
        ("python", PY_FIXTURE, PY_EXPECT),
        ("typescript", TS_FIXTURE, TS_EXPECT),
    ):
        data = source.encode()
        for q in (q for q in QUERIES if q.lang == lang):
            want = sorted(expect[q.id])
            for arm, run in (("rule", arms.astgrep), ("ts", arms.treesitter)):
                lines = sorted(m.line for m in run(q, data, lang))
                if lines != want:
                    failures += 1
                    print(f"FAIL {q.id:26} {arm:4} {lines} != {want}")
            if q.regex is not None:  # regexes are realistic, not correct: report only
                lines = sorted(m.line for m in arms.regex(q, data))
                print(f"  {q.id:26} regex {lines} (ast {want})")
    print("selftest", "FAILED" if failures else "ok")
    raise SystemExit(1 if failures else 0)


if __name__ == "__main__":
    _selftest()
