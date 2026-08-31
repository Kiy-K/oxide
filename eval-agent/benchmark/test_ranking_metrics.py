"""Regression coverage for Codex review finding 2: a native benchmark run's
reported model label must come from the provider identity verified against
the index metadata, never reconstructed from
OXIDE_EMBED_NATIVE/OXIDE_EMBED_NATIVE_QUERY_PROMPT — Rust's
NativeEmbedder::new silently ignores the query-prompt env var for any
non-Gemma profile, so reconstructing a label from it can disagree with what
was actually embedded.

Run with the project's eval venv:
    eval-agent/.venv/bin/python -m pytest eval-agent/benchmark/test_ranking_metrics.py
"""
import importlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def _import_ranking_metrics(monkeypatch, native_profile, query_prompt=None):
    """Import ranking_metrics fresh under a controlled native-profile env —
    its module-level MODEL_LABEL placeholder is computed at import time."""
    monkeypatch.setenv("OXIDE_EMBED_NATIVE", native_profile)
    monkeypatch.delenv("OXIDE_EMBED_URL", raising=False)
    if query_prompt is None:
        monkeypatch.delenv("OXIDE_EMBED_NATIVE_QUERY_PROMPT", raising=False)
    else:
        monkeypatch.setenv("OXIDE_EMBED_NATIVE_QUERY_PROMPT", query_prompt)
    sys.modules.pop("ranking_metrics", None)
    return importlib.import_module("ranking_metrics")


def test_native_placeholder_ignores_leftover_query_prompt(monkeypatch):
    # Conflicting env: a non-Gemma profile with a query-prompt variant set
    # (only meaningful for Gemma profiles in Rust). Even the pre-verification
    # placeholder must not fabricate a suffix from it.
    rm = _import_ranking_metrics(monkeypatch, "bge-small-en-v1.5", "search-result")
    assert rm.MODEL_LABEL == "bge-small-en-v1.5"


def test_resolve_model_label_prefers_verified_meta_over_conflicting_env(monkeypatch):
    rm = _import_ranking_metrics(monkeypatch, "bge-small-en-v1.5", "search-result")
    # Ground truth: what Rust's NativeEmbedder::new actually records for a
    # non-Gemma profile (no query-prompt suffix at all).
    verified = "native:bge-small-en-v1.5"
    # What the old, buggy env-reconstruction would have produced instead.
    misleading_placeholder = "bge-small-en-v1.5:search-result"
    assert rm.resolve_model_label(rm.NATIVE_PROFILE, misleading_placeholder, verified) == verified


def test_resolve_model_label_native_gemma_matches_verified_suffix(monkeypatch):
    rm = _import_ranking_metrics(monkeypatch, "embeddinggemma-300m", "search-result")
    # For a Gemma profile the suffix IS real (NativeEmbedder::new honors it),
    # so the verified meta value carries it — resolve_model_label must still
    # take the verified string verbatim, not re-derive it.
    verified = "native:embeddinggemma-300m:search-result"
    assert rm.resolve_model_label(rm.NATIVE_PROFILE, rm.MODEL_LABEL, verified) == verified


def test_resolve_model_label_http_mode_keeps_placeholder_unaffected(monkeypatch):
    # resolve_model_label's native_profile argument is independent of the
    # env the module happened to import under; use any successful native
    # import to reach the function without needing a live HTTP embedder.
    rm = _import_ranking_metrics(monkeypatch, "bge-small-en-v1.5")
    assert rm.resolve_model_label("", "qwen3-Q8_0", "http:qwen3-Q8_0@http://x") == "qwen3-Q8_0"
