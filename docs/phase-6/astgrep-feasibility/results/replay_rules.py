"""Builds replay_rules.json: one ast-grep rule per replayed held-out search, written from
the agent's pattern alone, before replay.py ran (preregistration § Study A.3).

Each rule encodes the whole search, branch by branch, as its AST counterpart: a name
becomes code identifiers of that name, text becomes string literals containing it, a
keyword-argument search (`x=`) becomes keyword arguments and parameter defaults named
x, `a.*b` on one line becomes the syntactic relation the branch evidently means.
Branches with no code counterpart (version numbers in non-Python files) are noted.

    replay_rules.py REPLAY_KEYS_JSON OUT_JSON
"""

import json
import sys
from pathlib import Path


def name(n: str) -> dict:  # identifiers named n (definitions, uses, attributes, kwargs)
    return {"kind": "identifier", "regex": f"^{n}$"}


def text(rx: str) -> dict:  # string literals whose text matches rx
    return {"kind": "string", "regex": rx}


def kw(n: str, value: str | None = None) -> dict:  # `n=` in a call or a parameter default
    has_name = {"has": {"field": "name", "regex": f"^{n}$"}}
    kinds = ("keyword_argument", "default_parameter", "typed_default_parameter")
    forms: list[dict] = [{"kind": k, **has_name} for k in kinds]
    if value is not None:
        forms = [{"all": [f, {"has": {"field": "value", "regex": value}}]} for f in forms]
    return {"any": forms}


def assign(n: str) -> dict:  # an assignment to n (or to obj.n)
    return {"kind": "assignment", "has": {"field": "left", "regex": rf"(^|\.){n}$"}}


def defn(rx: str) -> dict:
    return {"kind": "function_definition", "has": {"field": "name", "regex": rx}}


def cls(rx: str) -> dict:
    return {"kind": "class_definition", "has": {"field": "name", "regex": rx}}


def call(fn_rx: str, arg: dict | None = None) -> dict:  # a call of fn, optionally with arg
    rule: dict = {"kind": "call", "has": {"field": "function", "regex": fn_rx}}
    if arg is not None:
        rule = {"all": [rule, {"has": {"field": "arguments", "has": {**arg, "stopBy": "end"}}}]}
    return rule


def attr(n: str) -> dict:  # `.n` attribute access
    return {"kind": "attribute", "has": {"field": "attribute", "regex": f"^{n}$"}}


def subscript(value_rx: str, key_rx: str | None = None) -> dict:
    rule: dict = {"kind": "subscript", "has": {"field": "value", "regex": value_rx}}
    if key_rx is not None:
        rule = {"all": [rule, {"has": {"field": "subscript", "regex": key_rx}}]}
    return rule


def pat(p: str) -> dict:
    return {"pattern": p}


def any_(*rules: dict) -> dict:
    return {"rule": {"any": list(rules)}}


def except_(n: str) -> dict:
    return {"kind": "except_clause", "regex": rf"^except\s+{n}\b"}


DEPRECATED = call(r"(^|\.)deprecated$", name("FutureWarning"))
WARN_FUTURE = call(r"(^|\.)warn$", name("FutureWarning"))
WHERE_ATTRS = call(r"(^|\.)where$", name(r"keep_attrs|attrs"))
CONVERTER_INPUT = call(r"^converter$", name("input_"))

# index -> (rule, note); indices follow the sorted sample in replay_keys.json
RULES = {
    0: (
        any_(name("only_relation_agnostic_fields"), subscript(r"^deconstruction\[2\]$", "'to'")),
        "name; subscript deconstruction[2]['to']",
    ),
    1: (
        any_(call("^len$", name("(?i).*pipe.*")), call("^len$", attr("(?i).*pipe.*"))),
        "len() over a pipeline object",
    ),
    2: (
        any_(defn("^_binary_op$"), name("keep_attrs"), subscript("^OPTIONS$", "keep_attrs")),
        "definition; name; OPTIONS['keep_attrs']",
    ),
    3: (
        any_(defn("^where$"), kw("where"), assign("where"), call(r"(^|\.)apply_ufunc$")),
        "definition; where= / where = ; apply_ufunc calls",
    ),
    4: (any_(kw("keep_attrs", "^True$")), "keep_attrs=True"),
    5: (
        any_(name("keep_attrs"), subscript("^OPTIONS$", "keep_attrs")),
        "name; OPTIONS[...keep_attrs]",
    ),
    6: (
        any_(attr("where"), WHERE_ATTRS, call(r"(^|\.)keep_attrs$")),
        "xr.where; where(..attrs..)",
    ),
    7: (
        any_(
            name("MigrationWriter"),
            call("^sorted$", name("(?i).*import.*")),
            assign("(?i).*import.*"),
        ),
        "name; sorted(imports); imports assignment",
    ),
    8: (
        any_(defn("^fowlkes_mallows_score$"), pat("tk / np.sqrt($$$)")),
        "definition; tk / np.sqrt(...)",
    ),
    9: (
        any_(name("fowlkes_mallows_score"), pat("tk / np.sqrt($$$)")),
        "name; tk / np.sqrt(...)",
    ),
    10: (
        any_(call(r"(^|\.)sqrt$", {"kind": "binary_operator", "regex": "/"})),
        "sqrt(a / b): a division inside sqrt",
    ),
    11: (
        any_(
            name("_enforce_prediction_order"),
            pat("isinstance(predictions, list)"),
            subscript(r"(^|\.)estimators_$", "^i_label$"),
        ),
        "name; literal call; subscript",
    ),
    12: (
        any_(pat("$E.estimators_[i_label].classes_"), name("_enforce_prediction_order")),
        "literal attribute chain; name",
    ),
    13: (
        any_(pat("isinstance(predictions, list)"), name("_enforce_prediction_order")),
        "literal call; name",
    ),
    14: (
        any_(
            {
                "kind": "assignment",
                "all": [
                    {"has": {"field": "left", "regex": "^predictions$"}},
                    {"has": {"field": "right", "regex": r"^\[_enforce_prediction_order"}},
                ],
            },
            pat("isinstance(predictions, list)"),
        ),
        "assignment; literal call",
    ),
    15: (
        any_(WARN_FUTURE, kw("stacklevel", "^2$")),
        "warn(..FutureWarning..); stacklevel=2",
    ),
    16: (
        any_(DEPRECATED, WARN_FUTURE, kw("since", r"5\.1"), kw("version", r"5\.1"), text(r"5\.2")),
        "deprecated/warn with FutureWarning; since=/version= '5.1'; '5.2'",
    ),
    17: (
        any_(assign("version"), text(r"5\.1")),
        "version assignment; '5.1' (non-.py files unseen)",
    ),
    18: (
        any_(assign("version"), name("__version__"), text(r"5\.[12]")),
        "version assignment; __version__; '5.1'/'5.2'",
    ),
    19: (any_(WARN_FUTURE, kw("category", "^FutureWarning$")), "warn(..FutureWarning); category="),
    20: (any_(WARN_FUTURE, kw("category", "^FutureWarning$")), "warn(..FutureWarning); category="),
    21: (any_(assign("attrs"), name("keep_attrs")), "attrs assignment; name"),
    22: (
        any_(call(r"(^|\.)combine_attrs$", name("context")), kw("context")),
        "combine_attrs(..context..); context=",
    ),
    23: (
        any_(call(r"(^|\.)combine_attrs$", name("context")), subscript("^context$"), kw("context")),
        "combine_attrs(..context..); context[...]; context=",
    ),
    24: (any_(call(r"(^|\.)combine_attrs$"), kw("context")), "combine_attrs calls; context="),
    25: (any_(kw("context")), "context="),
    26: (any_(kw("context"), name("context")), "context= ; name"),
    27: (any_(call(r"(^|\.)merge_attrs$"), kw("keep_attrs")), "merge_attrs calls; keep_attrs="),
    28: (
        any_(call(r"(^|\.)merge_attrs$"), kw("combine_attrs")),
        "merge_attrs calls; combine_attrs=",
    ),
    29: (
        any_(call(r"(^|\.)where$", name("keep_attrs")), kw("keep_attrs", "^True$")),
        "where(..keep_attrs..); keep_attrs=True",
    ),
    30: (any_(WHERE_ATTRS), "where(..keep_attrs/attrs..)"),
    31: (
        any_(kw("where"), assign("where"), {"kind": "argument_list", "has": name("where")}),
        "where= ; where = ; where as an argument",
    ),
    32: (
        any_(
            text(r"%\(distinct\)s"),
            {
                "kind": "assignment",
                "all": [
                    {"has": {"field": "left", "regex": r"(^|\.)template$"}},
                    {"has": {"field": "right", "regex": "function"}},
                ],
            },
        ),
        "'%(distinct)s'; template = ...function",
    ),
    33: (
        any_(text(r"COUNT\(%\((distinct|function)"), text(r"%\(distinct\)s"), cls("^Aggregate$")),
        "SQL text in strings; class Aggregate",
    ),
    34: (
        any_(
            text(r"COUNT\(DISTINCT"), text(r"DISTINCT %\(expressions\)"), kw("distinct", "^True$")
        ),
        "SQL text in strings; distinct=True",
    ),
    35: (
        any_(
            text(r"COUNT\("),
            name("Count"),
            kw("distinct", "^True$"),
            cls("^Count$"),
            defn("^as_sql$"),
        ),
        "SQL text; name; distinct=True; class; def",
    ),
    36: (
        any_(
            text(r"COUNT\("),
            text("DISTINCT"),
            kw("distinct", "^True$"),
            cls("^Count$"),
            call(r"(^|\.)as_sql$"),
        ),
        "SQL text; distinct=True; class; as_sql calls",
    ),
    37: (any_(text("(?s)distinct.*CASE"), text(r"COUNT\(DISTINCT")), "SQL text only"),
    38: (
        any_(subscript(r"(^|\.)extra_context$", "'distinct'"), text("DISTINCT ' if")),
        "extra_context['distinct']; SQL text",
    ),
    39: (any_(assign("template"), text(r"%\(distinct\)s")), "template = ; '%(distinct)s'"),
    40: (any_(name("NotImplemented"), except_("ValueError")), "name; except ValueError"),
    41: (any_(CONVERTER_INPUT, name("__array_ufunc__")), "converter(input_); name"),
    42: (
        any_(name("converters_and_unit"), except_("UnitConversionError"), defn("^converters")),
        "name; except UnitConversionError; def converters*",
    ),
    43: (
        any_(defn("^__array_ufunc__$"), CONVERTER_INPUT, call(r"(^|\.)arrays\.append$")),
        "def; converter(input_); arrays.append calls",
    ),
    44: (
        any_(
            defn("^__array_ufunc__$"),
            CONVERTER_INPUT,
            name("_condition_arg"),
            name("NotImplemented"),
        ),
        "def; converter(input_); names",
    ),
    45: (
        any_(defn("^converters_and_unit$"), CONVERTER_INPUT, defn("^get_converter$")),
        "defs; converter(input_)",
    ),
    46: (
        any_(
            defn("^converters_and_unit$"),
            defn("^get_converter$"),
            {"kind": "return_statement", "regex": r"^return\s+NotImplemented\b"},
            {
                "kind": "function_definition",
                "all": [
                    {"has": {"field": "name", "regex": "^__array_ufunc__$"}},
                    {"has": {"stopBy": "end", **name("NotImplemented")}},
                ],
            },
        ),
        "defs; return NotImplemented; __array_ufunc__ containing NotImplemented",
    ),
    47: (
        any_(call(r"(^|\.)parse_url$", name("(?i).*auth.*")), name("get_auth_from_url")),
        "parse_url(..auth..); name (HISTORY.md unseen by AST)",
    ),
    48: (
        any_(text("Invalid fill method"), attr("loc"), kw("method")),
        "message string; .loc; method=",
    ),
    49: (
        any_(text("Invalid fill method"), cls(".*Loc"), attr("loc"), kw("method")),
        "message string; class *Loc; .loc; method=",
    ),
    50: (any_(attr("loc"), name("LocIndexer"), kw("method")), ".loc; name; method="),
    51: (
        any_(attr("loc"), name("LocIndexer"), kw("method"), text("fill method")),
        ".loc; name; method= ; message string",
    ),
    52: (any_(attr("loc"), cls(".*Loc"), kw("method")), ".loc; class *Loc; method="),
    53: (any_(attr("loc"), kw("method"), name("method")), ".loc; method= ; name"),
    54: (
        any_(cls(".*Loc"), attr("loc"), kw("method"), name(".*method.*")),
        "class *Loc; .loc; method= ; names containing method",
    ),
    55: (
        any_(cls("^_LocIndexer$"), name(".*LocIndexer.*"), subscript(r"(^|\.)loc$")),
        "class; name; .loc[...]",
    ),
    56: (
        any_(
            cls("^_LocIndexer$"),
            {"kind": "function_definition", "regex": r"^def __getitem__\(self, key\)"},
        ),
        "class; def __getitem__(self, key)",
    ),
    57: (
        any_(
            cls(".*Loc.*"),
            attr("loc"),
            kw("method"),
            {"kind": "argument_list", "has": name("method")},
        ),
        "class *Loc*; .loc; method= ; method as an argument",
    ),
    58: (any_(kw("method", '^"nearest"$'), kw("method")), 'method="nearest"; method='),
    59: (
        any_(name("remap_label_indexers"), call(r"(^|\.)sel$", kw("method")), attr("loc")),
        "name; .sel(..method=..); .loc",
    ),
}


def main() -> None:
    keys = json.loads(Path(sys.argv[1]).read_text())
    assert len(keys) == len(RULES) == 60
    out = [{**entry, "rule": RULES[i][0], "note": RULES[i][1]} for i, entry in enumerate(keys)]
    Path(sys.argv[2]).write_text(json.dumps(out, indent=1) + "\n")


if __name__ == "__main__":
    main()
