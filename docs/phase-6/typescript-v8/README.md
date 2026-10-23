# typescript-v8: Rust component identity amendment

The new `rust` (`oxide-rust-v1`) and `rust-parser`
(`tree-sitter-rust==0.24.2`) derivation components invalidate existing
indices, including captures with no Rust. Their cached facts container also
names `oxide-rust-facts-v1`; old caches are discarded and safely rebuilt.
Python/TypeScript/JavaScript/Go slice versions and facts encodings are unchanged.

Measured against [typescript-v7](../typescript-v7/README.md): results are identical except
`versions.derivation`; metrics are byte-identical. Entries, routes, payload hashes,
gold audit, losses, corpora, tasks, budgets, scorer, tolerance and gates are unchanged.
Manifest changes are baseline/protocol/source labels and derivation identities only;
timings and memory in `performance.json` are re-measured (largest absolute `max_rss_mb`
change +30 MB, vscode). This is an identity amendment, not a quality change. All
historical artifacts remain untouched. Commands and comparison:
[../rust-v1/verification/baselines.md](../rust-v1/verification/baselines.md). The
independent Rust diagnostic is in [../rust-v1/README.md](../rust-v1/README.md).
