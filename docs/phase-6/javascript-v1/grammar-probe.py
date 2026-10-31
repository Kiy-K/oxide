"""Run with each candidate grammar installed; output is retained in grammar-selection.json."""

import json
from importlib.metadata import version

import tree_sitter
import tree_sitter_javascript
from tree_sitter import Language, Parser

language = Language(tree_sitter_javascript.language())
sources = [
    'const App = () => <div class="app"/>;',
    'const App = () => <label for="id"/>;',
    'import x from "./data.json" with { type: "json" };',
    "class C { static { this.x = 1; } #x = 1; f = () => this.#x; }",
    "const x = a?.b ?? 1;",
    'export * as api from "./a.js";',
]
print(
    json.dumps(
        {
            "grammar": version("tree-sitter-javascript"),
            "binding": version("tree-sitter"),
            "grammar_abi": language.abi_version,
            "binding_abi_range": [
                tree_sitter.MIN_COMPATIBLE_LANGUAGE_VERSION,
                tree_sitter.LANGUAGE_VERSION,
            ],
            "cases": [
                {
                    "source": source,
                    "has_error": Parser(language).parse(source.encode()).root_node.has_error,
                }
                for source in sources
            ],
        },
        indent=1,
    )
)
