#!/usr/bin/env python3
"""Generate a deterministic synthetic repository for OXIDE perf runs.

Usage: gen_bench_repo.py <dest> [modules_per_lang]
Cross-file imports and name references are included so indexing exercises the
full pipeline (parse, references, import resolution, embeddings).
"""
import sys
from pathlib import Path


def py_module(i: int) -> str:
    return f'''"""Synthetic module {i}: retry/cache/auth flavored service."""

import json

from .helpers import normalize_key
from .retry import RetryPolicy


class Service{i}:
    """Handles workload {i} with bounded retries."""

    def __init__(self, policy=None):
        self.policy = policy or RetryPolicy(max_attempts={i % 5 + 1})
        self._items = {{}}

    def handle_{i}(self, key, payload):
        """Process request {i}; normalizes and caches the payload."""
        clean = normalize_key(key)
        if clean in self._items:
            return self._items[clean]
        result = {{"module": {i}, "key": clean, "size": len(json.dumps(payload or ""))}}
        self._items[clean] = result
        return result

    def flush(self):
        dropped = len(self._items)
        self._items.clear()
        return dropped


def helper_{i}(value):
    return normalize_key(value) or "empty-{i}"
'''


def ts_module(i: int) -> str:
    return f'''import {{ RetryPolicy }} from './retry';

export interface Config{i} {{
  retries: number;
  endpoint: string;
}}

/** Synthetic service {i}. */
export class Handler{i} {{
  private cache = new Map<string, string>();

  constructor(public policy: RetryPolicy = {{ maxAttempts: {i % 4 + 1}, backoffMs: () => 0 }}) {{}}

  async process{i}(input: string): Promise<{{ ok: boolean; id: string }}> {{
    if (this.cache.has(input)) return {{ ok: true, id: this.cache.get(input)! }};
    const id = `{{input}}-{i}`;
    this.cache.set(input, id);
    return {{ ok: true, id }};
  }}

  reset(): void {{
    this.cache.clear();
  }}
}}

export const defaultConfig{i}: Config{i} = {{ retries: {i % 3}, endpoint: '/api/v{i}' }};
'''


def rs_module(i: int) -> str:
    return f'''use crate::retry::RetryPolicy;
use std::collections::HashMap;

/// Synthetic service {i}.
pub struct Handler{i} {{
    policy: RetryPolicy,
    cache: HashMap<String, String>,
}}

pub trait Handles{i} {{
    fn handle(&mut self, key: &str) -> String;
}}

impl Handler{i} {{
    pub fn new(policy: RetryPolicy) -> Self {{
        Handler{i} {{ policy, cache: HashMap::new() }}
    }}

    fn normalize(key: &str) -> String {{
        key.trim().to_lowercase()
    }}
}}

impl Handles{i} for Handler{i} {{
    fn handle(&mut self, key: &str) -> String {{
        let clean = Handler{i}::normalize(key);
        if let Some(hit) = self.cache.get(&clean) {{
            return hit.clone();
        }}
        let id = format!("{{clean}}-{i}");
        self.cache.insert(clean, id.clone());
        id
    }}
}}

pub const DEFAULT_RETRIES_{i}: u32 = {i} % 4;
'''


def go_module(i: int) -> str:
    return f'''package service{i}

import (
\t"strings"
)

// Handler{i} is a synthetic service.
type Handler{i} struct {{
\tBase
\tcache map[string]string
}}

type Base struct {{
\tname string
}}

type Handles{i} interface {{
\tHandle(key string) string
}}

func New{i}() *Handler{i} {{
\treturn &Handler{i}{{cache: map[string]string{{}}}}
}}

func (h *Handler{i}) Handle(key string) string {{
\tclean := strings.ToLower(strings.TrimSpace(key))
\tif hit, ok := h.cache[clean]; ok {{
\t\treturn hit
\t}}
\tid := clean + "-{i}"
\th.cache[clean] = id
\treturn id
}}

func (h *Handler{i}) String() string {{
\treturn "handler{i}"
}}

const DefaultRetries{i} = {i} % 4
'''


def main() -> None:
    dest = Path(sys.argv[1] if len(sys.argv) > 1 else "bench_repo")
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 200
    for base in ("src", "src/py", "src/ts", "src/rs", "src/go", "tests"):
        (dest / base).mkdir(parents=True, exist_ok=True)
    (dest / ".gitignore").write_text("__pycache__/\nnode_modules/\ndist/\n")

    helpers = (
        "def normalize_key(key):\n"
        "    \"\"\"Lowercase and strip shared helper.\"\"\"\n"
        "    return (key or '').strip().lower() or None\n"
    )
    retry = (
        "class TooManyAttemptsError(RuntimeError):\n"
        "    pass\n\n\n"
        "class RetryPolicy:\n"
        "    \"\"\"Shared retry policy used by every service module.\"\"\"\n\n"
        "    def __init__(self, max_attempts=3, base_delay_ms=50):\n"
        "        self.max_attempts = max_attempts\n"
        "        self.base_delay_ms = base_delay_ms\n\n"
        "    def should_retry(self, attempt, error):\n"
        "        return attempt < self.max_attempts\n"
    )
    (dest / "src" / "helpers.py").write_text(helpers)
    (dest / "src" / "retry.py").write_text(retry)

    ts_retry = (
        "export interface RetryPolicy {\n"
        "  maxAttempts: number;\n"
        "  backoffMs(attempt: number): number;\n"
        "}\n"
    )
    ts_helpers = "export function normalizeKey(key: string | null): string | null {\n  return key ? key.trim().toLowerCase() : null;\n}\n"
    (dest / "src" / "retry.ts").write_text(ts_retry)
    (dest / "src" / "helpers.ts").write_text(ts_helpers)

    rs_retry = (
        "/// Shared retry policy used by every service module.\n"
        "pub struct RetryPolicy {\n"
        "    pub max_attempts: u32,\n"
        "}\n\n"
        "impl RetryPolicy {\n"
        "    pub fn should_retry(&self, attempt: u32) -> bool {\n"
        "        attempt < self.max_attempts\n"
        "    }\n"
        "}\n"
    )
    (dest / "src" / "retry.rs").write_text(rs_retry)

    for i in range(n):
        pkg = dest / "src" / "py" / f"service_{i}"
        pkg.mkdir(parents=True, exist_ok=True)
        (pkg / "__init__.py").write_text("")
        (pkg / "svc.py").write_text(py_module(i))
        (dest / "src" / "ts" / f"handler_{i}.ts").write_text(ts_module(i))
        (dest / "src" / "rs" / f"handler_{i}.rs").write_text(rs_module(i))
        go_pkg = dest / "src" / "go" / f"service_{i}"
        go_pkg.mkdir(parents=True, exist_ok=True)
        (go_pkg / "svc.go").write_text(go_module(i))
        (dest / "tests" / f"test_service_{i}.py").write_text(
            f"from src.py.service_{i}.svc import Service{i}\n\n\n"
            f"def test_service_{i}_handles_key():\n"
            f"    svc = Service{i}()\n"
            f"    out = svc.handle_{i}(' Key ', None)\n"
            f"    assert out['key'] == 'key'\n"
        )


if __name__ == "__main__":
    main()
