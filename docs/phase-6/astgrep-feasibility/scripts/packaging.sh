#!/usr/bin/env bash
# Study C: install ast-grep-py 0.51.0 next to OXIDE on every supported CPython that runs
# here (Linux x86_64 only; other platforms are PyPI metadata, pypi_matrix.py), run the
# query self-test in it, and measure installed sizes and import time.
#   packaging.sh WORK_DIR OUT_TXT
set -euo pipefail
work=$1 out=$2
here=$(cd "$(dirname "$0")" && pwd)
repo=$(cd "$here/../../../.." && pwd)
: > "$out"
for v in 3.12 3.13 3.14; do
  venv=$work/pkg-$v
  rm -rf "$venv"
  uv venv -q --python "$v" "$venv"
  VIRTUAL_ENV=$venv uv pip install -q "$repo"
  site=$("$venv/bin/python" -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')
  before=$(du -sb "$site" | cut -f1)
  VIRTUAL_ENV=$venv uv pip install -q ast-grep-py==0.51.0
  after=$(du -sb "$site" | cut -f1)
  sg=$(du -sb "$site"/ast_grep_py | cut -f1)
  selftest=$(cd "$here" && "$venv/bin/python" queries.py | tail -1)
  import_ms=$("$venv/bin/python" -c 'import time; t=time.perf_counter(); import ast_grep_py; print(round((time.perf_counter()-t)*1000,1))')
  {
    echo "python=$("$venv/bin/python" -V | cut -d' ' -f2) oxide_site_bytes=$before"
    echo "  with_astgrep_site_bytes=$after astgrep_pkg_bytes=$sg selftest=$selftest import_ms=$import_ms"
  } | tee -a "$out"
  rm -rf "$venv"
done
