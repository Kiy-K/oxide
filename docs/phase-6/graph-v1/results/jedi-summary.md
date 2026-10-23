# jedi oracle: precision of resolved Python edges

Every resolved `calls`/`references` edge from a `.py` file, before (`03dc2ba`,
`oxide-python-v3`) and after (`6dd0817`, `oxide-python-v4`), checked against jedi 0.20.1
`Script.goto(follow_imports=True)` at the last identifier of the call's dotted name
(`scripts/jedi_extract.py`, `scripts/jedi_judge.py`). Agree: a jedi definition in the
target's file whose full name ends with the target's name chain, or whose name matches
and whose line lies in the target's range. New = after minus before, keyed by
(source, target, evidence span).

| Set | Corpus | Edges | Agree | Disagree | jedi finds nothing | Precision |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| before | flask | 209 | 209 | 0 | 0 | 1.000 |
| before | requests | 396 | 396 | 0 | 0 | 1.000 |
| before | pytest | 1,325 | 1,324 | 0 | 1 | 1.000 |
| after | flask | 714 | 713 | 0 | 1 | 1.000 |
| after | requests | 690 | 690 | 0 | 0 | 1.000 |
| after | pytest | 2,863 | 2,767 | 4 | 92 | 0.999 |
| new | pooled | 2,337 | 2,241 | 4 | 92 | 0.998 |

Precision = agree / (agree + disagree). The 4 disagreements, all new, are jedi errors:

- `Scope.from_user` (3 call sites in `fixtures.py`, `python.py`, `testing/test_scope.py`,
  one twice) → OXIDE `src/_pytest/scope.py#Scope.from_user`; jedi →
  `builtins.classmethod`. `from_user` is a `@classmethod` at `scope.py:61`, and jedi
  resolves the decorator rather than the method.

Of the 92 sites where jedi finds nothing, 91 are names imported from pytest's `_pytest._io`
package (`saferepr` 42, `TerminalWriter` 27, `wcswidth` 6, …). The remaining one is flask's
`examples/celery/make_celery.py`: `from task_app import create_app` → OXIDE
`examples/celery/src/task_app/__init__.py#create_app` (`def create_app` at line 7). That
is the example's own nested import root, which jedi was not given (only `<corpus>/src`),
so OXIDE is correct by inspection. Inspected: `from _pytest._io.saferepr import saferepr` with
`def saferepr` at `_io/saferepr.py:97`, which is OXIDE's target. The other `_io` sites
are assumed to fail in jedi the same way (*inferred*, not inspected one by one).
