# TOON serialization: REJECT for this payload mix

Historical experiment; [preregistration](preregistration.md). `toon-format` 1.2.0 / spec 4.4 (spec revision `d09c3d33`); OpenCode 2.0.32 / gpt-6-luna. **Keep compact JSON:** TOON did not meet efficiency or agent-quality gates. No product serializer/dependency was added.

Reported corpus: 67 outputs, 344 comprehension questions, 12 navigation cases, 500 graded calls. Compared with compact JSON, TOON was 11.5–14.4% larger for search/files/relations, 2.3% larger for full bundles; provider input tokens rose 10.5%. The OXIDE unit counter misleadingly called it 19–24% smaller: format evaluation must use actual tokenizers/provider counts, not that proxy.

Comprehension A1 difference −0.031 (CI −0.094…+0.029); navigation hit difference −0.042, recall −0.069. Correctness checks passed: 65 output round trips and 564 specification fixtures; encoding search20 p95 1.49 ms. A working encoder is not evidence of cheaper or better agent use.

## Evidence and reproduction

Retain preregistration, results JSON and scripts together: the frozen protocol defines payload selection, questions, graders, encoding controls and commands. Raw agent replies remain external in `~/Projects/oxide-eval-data/oxide-toon-eval/runs/`; full regrading needs those files. These findings concern this nested OXIDE payload mix, client and model, not every TOON use case. Live reruns need fresh authorization; none were made during consolidation.
