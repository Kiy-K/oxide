"""#43 fresh task builder: #40's build.py, unmodified, with only its cache dir (J) and seed overridden.

  build_tasks.py heldout          -> ~/.cache/oxide-capremoval-eval/tasks/heldout-frozen.jsonl
  build_tasks.py cb <parquet>     -> ~/.cache/oxide-capremoval-eval/tasks/cb-frozen.jsonl

The new cache dir links `repos` and `cb-repos` to #40's clones; its `excl/` comes from #40's excl.py,
run at this protocol's frozen commit.
"""
import os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', '..', 'jev-typed-evidence-eval', 'scripts'))
sys.dont_write_bytecode = True
import build  # noqa: E402

build.J = os.path.expanduser('~/.cache/oxide-capremoval-eval')
build.SEED = 43

if __name__ == '__main__':
    build.heldout() if sys.argv[1] == 'heldout' else build.cb(sys.argv[2])
