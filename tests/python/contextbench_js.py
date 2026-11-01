"""JavaScript scorer adapter; historical TypeScript tasks/corpora stay unchanged.

Reuse the independently written TypeScript declaration walker over the native JS
syntax tree. Inline CJS export functions are an additional outermost declaration.
The scorer and its budgets/gold integrity rules are ContextBench's unchanged ones.
"""

from __future__ import annotations

import contextbench_ts as ts
import tree_sitter_javascript
from tree_sitter import Language, Parser, Query, QueryCursor

LANGUAGE = Language(tree_sitter_javascript.language())
_ASSIGNMENTS = Query(
    LANGUAGE,
    "(assignment_expression right: [ "
    "(function_expression) (arrow_function) (generator_function) (class)]) @assignment",
)


def module_lines(data: bytes) -> set[int]:
    root = Parser(LANGUAGE).parse(data).root_node
    declarations = [node for _, node in ts.declarations(root)]
    for node in QueryCursor(_ASSIGNMENTS).captures(root).get("assignment", []):
        left = node.child_by_field_name("left")
        text = ts._text(left)
        if text == "module.exports" or text.startswith(("exports.", "module.exports.")):
            declarations.append(node)
    inside = {
        n for node in declarations for n in range(node.start_point[0] + 1, node.end_point[0] + 2)
    }
    return set(range(1, data.count(b"\n") + 2)) - inside


class Lines(ts.Lines):
    def module(self, path: str) -> set[int]:
        if not path.endswith((".js", ".jsx", ".mjs", ".cjs")):
            return super().module(path)
        if path not in self.modules:
            try:
                self.modules[path] = module_lines(self.file(path))
            except OSError:
                self.modules[path] = set()
        return self.modules[path]
