# ADR-0012: NetworkX for graph algorithms over bounded projections

## Status

**Proposed**, 2026-10-25. The maintainer asked to introduce NetworkX as OXIDE's standard
graph-algorithm dependency. The evidence is in
[docs/phase-6/networkx-v1](../phase-6/networkx-v1/README.md). This proposal amends no
existing ADR. LadybugDB stays the authoritative store ([ADR-0002](0002-ladybugdb-knowledge-store.md),
[ADR-0011](0011-python-engine-vectorless.md)).

## Context

OXIDE's graph code (audit, docs/phase-6/networkx-v1 § Audit) is almost all domain
traversal:

- `navigation.walk` (impact BFS);
- `routing.route` (TreeRouter);
- the Python dispatch C3 MRO;
- the resolvers' binding-cycle guards.

These read LadybugDB one layer at a time, enforce budgets (depth, limit, examined edges,
fan-out), and report what stopped them and the provenance of each step. None of that is a
generic algorithm, and NetworkX could only take it over by first materializing the graph
in memory.

Two places re-implement textbook algorithms:

- `navigation._cyclic`: an iterative Tarjan strongly-connected-components pass over the
  walked subgraph.
- `python_dispatch.Dispatch.subclasses`: the transitive closure of the inheritance graph
  (descendants).

Future purpose-specific work will want more of them: PageRank or personalized PageRank,
centrality, shortest paths, SCCs, community detection. graph-v1 already measured PPR and
communities for retrieval ranking and REJECTED them (6dd0817..1b11ab6). Writing each
algorithm by hand would duplicate a mature library.

## Decision

1. **NetworkX is the standard library for generic graph algorithms in `oxide.core`.**
   - Pure Python, BSD-3-Clause, no required runtime dependencies; 13 MB installed.
   - Range `networkx>=3.4,<4`.
   - `types-networkx` for mypy `--strict` (dev only).
2. **LadybugDB stays authoritative.**
   - NetworkX graphs are transient, in-memory *projections*, built per call and never
     persisted.
   - Storage traversal, budgets and provenance stay in OXIDE code.
   - No module outside `oxide.storage` touches the store, so projections are built from
     the `View` reads core already performs.
3. **Projections are bounded, never whole-repository by default.**
   - The first projection is the impact walk's own subgraph, `navigation.Walk.graph`: an
     `nx.DiGraph` in traversal direction.
   - Every edge carries `via` (`kind:in|out:key`, the step that walked it).
   - Its size is bounded by the walk's depth, limit and `MAX_EDGES`, and `Walk.stopped_by`
     says whether it is complete.
   - Building it inside the BFS costs nothing measurable, because store reads dominate.
   - An algorithm that needs a whole-repository graph must justify that in its own
     evaluation.
4. **Replace only the generic re-implementations, keeping identical output.**
   - `navigation._cyclic` → `nx.strongly_connected_components` on `Walk.graph`.
   - `Dispatch.subclasses` → `nx.descendants` on an inheritance `DiGraph` built once per
     `Dispatch`.
   - Both are covered by the existing tests, and outputs matched exactly in the
     benchmarks:
     - 2 000 real walks over 5 repositories;
     - 2 735 subclass queries while indexing the same 5;
     - 2 synthetic graphs of up to 200 000 edges.
   - Domain traversals stay custom: impact BFS, TreeRouter, C3 MRO and the resolver
     guards.
5. **Imported lazily.** `import networkx` costs about 87 ms cold (`import oxide` is about
   158 ms), so it happens inside the functions that need it. Commands that never walk or
   index Python pay nothing.
6. **No algorithm is activated for retrieval, routing or packing without evidence.**
   - PageRank, PPR, centrality, shortest paths and communities run on projections only in
     evaluation code, until a preregistered study passes per SPEC § Evaluation.
   - graph-v1's REJECT stands.

## Determinism

NetworkX iterates in insertion order. Projections are built from steps already sorted in
domain order, so the same snapshot gives the same graph. Code that exposes an algorithm's
result sorts it, or returns a set, before it leaves core. Versioned formats are unchanged:
`NAVIGATION_VERSION` is unchanged, and so are derivation components, because outputs are
identical.

## Consequences

- **Costs:**
  - One more runtime dependency.
  - +87 ms on the first call that imports it in a process.
  - On large graphs, NetworkX's SCC is about 2.7× slower and uses about 3.4× the peak
    memory of the hand-written Tarjan (synthetic: 200 000 edges, 441 ms vs 157 ms). At
    the walk's cap of 20 000 edges that is 21 ms vs 8 ms. On real walks (at most 469
    edges) it is no slower: 2.4 ms vs 2.8 ms in total over 400 xarray walks.
- **Gains:**
  - The hand-written Tarjan and inheritance closure are removed.
  - The impact walk exposes a typed projection that future algorithms can run on
    directly, with no new wrappers.
- **Rejected alternatives:**
  - Keep hand-written algorithms: each new one is more code, tested by us.
  - rustworkx or igraph: faster, but native wheels to keep green on the 4 CI platforms
    (docs/platforms.md), for no measured need.
  - Graph queries in LadybugDB (Cypher): the right tool for storage traversal, which
    already uses it, not for in-memory analytics on a projection.
