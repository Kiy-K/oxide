"""Results-file provenance for contextbench_run: resuming must never mix embedders."""

import json
from pathlib import Path

# Sentinel for a results row written before per-record provenance existed.
# Never treated as matching any real provider identity — see
# check_embedder_provenance.
UNKNOWN_EMBEDDER = "<unknown, pre-provenance record>"


def load_existing_progress(results_path: Path) -> tuple[set[tuple[str, str]], set[str]]:
    """Read a results file's completed (task, condition) keys plus the set
    of embedder identities its rows were actually produced under. A row
    with no `embedder` field (written before provenance tracking existed)
    counts as UNKNOWN_EMBEDDER, not as "matches the current run" — its
    provider is genuinely unknowable, not assumed compatible.
    """
    done_keys: set[tuple[str, str]] = set()
    existing_embedders: set[str] = set()
    if results_path.exists():
        for line in results_path.read_text().splitlines():
            if line.strip():
                rec = json.loads(line)
                done_keys.add((rec["task"], rec["condition"]))
                existing_embedders.add(rec.get("embedder", UNKNOWN_EMBEDDER))
    return done_keys, existing_embedders


def check_embedder_provenance(
    existing_embedders: set[str], effective_embedder: str, results_path: Path
) -> None:
    """Hard-stop before appending into a results file whose prior rows came
    from a different (or unknown) embedding provider — resuming would
    silently mix incomparable vector spaces into one aggregate.
    """
    if existing_embedders and existing_embedders != {effective_embedder}:
        raise SystemExit(
            f"{results_path} already has results from "
            f"{sorted(existing_embedders)}, which differ from this "
            f"run's embedder {effective_embedder!r}. Resuming into a "
            "file with a different/unknown provider would mix "
            "incomparable vector spaces in one aggregate — use a "
            "different --out directory."
        )
