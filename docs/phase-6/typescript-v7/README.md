# typescript-v7: Go component identity amendment

The new `go` (`oxide-go-v1`) and `go-parser`
(`tree-sitter-go==0.25.0`) derivation components invalidate existing
indices, including captures with no Go. Their cached facts container also
names `oxide-go-facts-v1`; old caches are discarded and safely rebuilt.
Python/TypeScript/JavaScript slice versions and facts encodings are unchanged.

Measured against [typescript-v6](../typescript-v6/README.md): results are identical except
`versions.derivation`; metrics are byte-identical. Entries, routes, payload hashes,
gold audit, losses, corpora, tasks, budgets, scorer, tolerance and gates are unchanged.
Manifest changes are baseline/protocol/source labels and derivation identities only.
This is an identity amendment, not a quality change. All historical artifacts remain
untouched. The independent Go diagnostic is in
[../go-v1/README.md](../go-v1/README.md).
