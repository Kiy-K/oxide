# typescript-v6: JavaScript component identity amendment

The new `javascript` (`oxide-javascript-v1`) and `javascript-parser`
(`tree-sitter-javascript==0.23.1`) derivation components invalidate existing
indices, including captures with no JavaScript. Their cached facts container also
names `oxide-javascript-facts-v1`; old caches are discarded and safely rebuilt.
Python/TypeScript slice versions and facts encodings are unchanged.

Measured against [typescript-v5](../typescript-v5/README.md): results are identical except
`versions.derivation`; metrics are byte-identical. Entries, routes, payload hashes,
gold audit, losses, corpora, tasks, budgets, scorer, tolerance and gates are unchanged.
Manifest changes are baseline/protocol/source labels and derivation identities only.
This is an identity amendment, not a quality change. All historical artifacts remain
untouched. The independent JavaScript diagnostic is in
[../javascript-v1/README.md](../javascript-v1/README.md).
