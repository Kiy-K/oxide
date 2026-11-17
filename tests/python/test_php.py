"""PHP slice conformance (docs/php.md): grammar, declarations, imports, resolution, evidence."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from conftest import REPO
from test_typescript import derived, resolved

from oxide.capture import capture
from oxide.core.indexing import dialect
from oxide.core.languages import php

FIXTURE = REPO / "fixtures/php_repo"


def test_dialect() -> None:
    assert dialect("a/B.php") == "php"
    assert dialect("a/b.phtml") == "php"
