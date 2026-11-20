This directory defines the high-level concepts, business logic, and architecture of this project using markdown. It is managed by [lat.md](https://www.npmjs.com/package/lat.md) — a tool that anchors source code to these definitions. Install the `lat` command with `npm i -g lat.md` and run `lat --help`.

- [[architecture]] — layers, context pipeline, freshness, navigation, optional judge
- [[domain]] — entities, relations, identity, snapshots, bundles, error contract
- [[storage]] — LadybugDB generations, ownership, lexical search, pins
- [[decisions]] — standing design rules and measured verdicts
- [[languages]] — per-language derivation slices
- [[tests]] — verification, isolation, baselines, fixtures
