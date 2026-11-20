# Storage

Derived knowledge lives in embedded LadybugDB, one database per generation, owned exclusively by `oxide.storage`.

## Layout

Each repository gets a store under the data location, keyed by a hash of its root.

[[src/oxide/storage/ladybug.py]] documents the layout: `store.lock`, an atomic `CURRENT` pointer, per-generation graph databases and a per-generation `lexical.lbug`. It is at the 500-line limit, so navigation queries live in [[src/oxide/storage/nav.py]].

## Generations and ownership

Writers publish immutable generations; readers pin them.

[[src/oxide/storage/store.py]] holds the OS lock (one owner), bulk-loads with COPY, reads back before sealing, then flips `CURRENT`. Sealed generations are read-only. [[src/oxide/storage/reader.py]] serves a non-owner the latest generation, holding a pin file ([[src/oxide/storage/pins.py]]) so the owner never deletes it in use.

## Lexical search

Full-text search uses the sha256-pinned LadybugDB FTS extension, loaded by path.

[[src/oxide/storage/fts.py]] locates it; `oxide setup` is the only network access OXIDE makes, fetching from a mirror in `docs/fts-mirror/`. Queries are built as literals because the parameterized form crashes the library. `auto_checkpoint` is off to shrink disk use.

## Pinned versions

The `ladybug` package is pinned to 0.21.0 (storage format 47) to match the FTS extension build.

Schema ids (currently `oxide-ladybug-py-v5`, with blast radius columns) change whenever the stored shape does; mismatches surface as `incompatible_index`.
