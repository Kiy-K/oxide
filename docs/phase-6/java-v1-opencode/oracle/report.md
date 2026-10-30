# Gson javac oracle

- JDK: Temurin `javac 25.0.5` (downloaded to external scratch).
- Javac mode: `JavacTask.parse(); JavacTask.analyze()` only; options `-proc:none -XDshould-stop.ifError=FLOW -Xmaxerrs 100000 -sourcepath <all module src/main/java>`; modules analyzed independently with no classpath. No generation or repository code execution.
- Modules and javac errors: extras 190; gson 6002; metrics 202; proto 683; test-jpms 142; test-shrinker 128.
- Oracle records: 19,593 use positions. `gson-uses.json` is 2,769,166 bytes (SHA-256 `3c889912a30608703c5ecd5bbde964b41283b26e320396196df4058cd6fa38e6`), below the 20 MB limit.

| Kind | AGREE | DISAGREE | NO-ORACLE-USE | Precision |
|---|---:|---:|---:|---:|
| calls | 7,830 | 0 | 49 | 100.000% |
| references | 5,605 | 1 | 104 | 99.982% |
| imports | 1,042 | 0 | 43 | 100.000% |
| overall | 14,477 | 1 | 196 | 99.993% |

NO-ORACLE-USE means no in-repo attributed use was recorded at the projected identifier; this combines absent use records and compiler error/external/JDK/dependency symbols, which are deliberately outside this source-only oracle. Ambiguous edges: calls 2,078 total; javac declaration in a candidate 2,031, in none 47. The deterministic unresolved sample is SHA-256 ordered and capped at 40 per kind; all sampled records currently fall under the conservative `other/unclassified` label (40 calls, 40 references), so the finer not-modeled cause is not inferred from evidence alone.

First scoring pass: 54 disagreements (51 calls, 3 references, 0 imports). Source inspection of the first-pass mismatches found 53 scorer errors: constructor elements must attribute to their enclosing type, and qualified/generic type identifiers must be projected to the final type identifier rather than an unrelated token. The scorer/oracle projection was corrected and rerun. Final: one OXIDE disagreement, 53 corrected scorer errors, zero remaining scorer errors identified.

Final disagreement: `references`, `gson/src/test/java/com/google/gson/functional/JsonAdapterAnnotationOnClassesTest.java:221`, evidence `JsonAdapter`; OXIDE target is nested `A.JsonAdapter` while javac attributes the annotation to `gson/src/main/java/com/google/gson/annotations/JsonAdapter.java` (declaration char offset 4407). The annotation precedes class `A`; the nested class is declared inside `A`, so this is an OXIDE resolution error.

Artifacts: `Oracle.java`, `score.py`, `gson-uses.json`, `gson-oracle.json`; execution logs `javac.log`, `score-first.log`, `score-final.log`.
