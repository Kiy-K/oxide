"""PHP language slice: tree-sitter syntax facts (docs/php.md).

The full grammar (``language_php``) parses inline HTML as ``text`` nodes, so PHP-only
and mixed PHP/HTML files use the same extraction path. Resolution lives in
``php_resolve``; the cache codec in ``php_facts``.
"""

from __future__ import annotations

import tree_sitter_php
from tree_sitter import Language

VERSION = "oxide-php-v1"
FACTS_VERSION = "oxide-php-facts-v1"
LANGUAGE = Language(tree_sitter_php.language_php())


def parse(data: bytes) -> str:
    """Syntax facts, or the reason the file is unsupported (stub until facts land)."""
    return "PHP slice not yet implemented"
