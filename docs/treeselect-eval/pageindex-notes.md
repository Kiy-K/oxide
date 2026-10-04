# PageIndex Research Notes for OXIDE

## Short verdict

Modern PageIndex is more relevant to OXIDE than its original “tree + LLM” framing suggests.

The useful idea is **not** copying its LLM reasoning loop. The useful idea is:

> **Build a structural hierarchy, optimize it with deterministic cost rules, let learned models propose or score evidence, and keep final policy in code.**

For OXIDE, that maps naturally to a Rust-native **TreeSelect** layer.

---

## How PageIndex works

PageIndex has three main stages:

1. **Structural indexing**
   - Builds a hierarchical tree from a document.
   - Newer **PageIndex Flash** extracts structure mostly from PDF layout/metadata heuristically.
   - LLMs are mainly used for summaries and optional expansion/refinement.

2. **Tree optimization**
   - Deterministically merges or expands nodes based on search cost.
   - A model may propose child structure, but code validates and decides whether to keep it.
   - Collapsed children can leave lightweight routing hints (`key_items`) behind.

3. **Query-time routing**
   - A reasoning model navigates the compact tree, chooses relevant nodes, then fetches only their content.
   - Production PageIndex has described value-function/MCTS-based search, but the full implementation is not open-source.

---

## Important mechanisms

### Deterministic tree cost

PageIndex models whether hierarchy is actually useful.

For node `v`:

- `S(v)`: cost of scanning the whole collapsed node/subtree.
- `R(v)`: routing cost of visiting a node.
- `S_residual(v)`: content not covered by children.

Expanded-tree cost is approximately:

```text
tree_cost(v) =
    R(v) + max(S_residual(v), max(tree_cost(child)))
```

If keeping children costs more than scanning the parent, PageIndex collapses the subtree.

This prevents hierarchy from becoming ceremony.

### Model proposes, code decides

During expansion:

```text
model / heuristics
        ↓
candidate children
        ↓
local validation
        ↓
deterministic cost comparison
        ↓
accept / reject
```

This is highly compatible with OXIDE's Jev direction.

### Routing hints

When PageIndex removes a subtree, it can preserve removed section titles as `key_items`.

OXIDE could generalize this into cheap **routing hints** for collapsed modules/files/symbol groups.

### Dynamic flattening

PageIndex's corpus-level design recognizes that some hierarchy levels are useless for a query.

Instead of always traversing every level, it can flatten uninformative parts of the tree.

For OXIDE:

```text
canonical hierarchy:
repo → module → file → symbol

query-time traversal:
repo → useful module → relevant symbol
```

The canonical tree and the traversal view do not need to be identical.

---

## What should transfer to OXIDE

| PageIndex | OXIDE TreeSelect |
|---|---|
| Document tree | Repository hierarchy |
| Section | Module / file / symbol |
| Page span | Token/context footprint |
| Title + summary | Candidate capsule |
| `key_items` | Routing hints |
| Layout-derived structure | Parser / symbol structure |
| LLM relevance | Embeddings + Jev typed evidence |
| Merge | Collapse unhelpful hierarchy |
| Expand | Inspect children / structural relations |
| Dynamic flattening | Skip useless path/module levels |
| Page fetch | Symbol/file context expansion |
| LLM policy | Deterministic Rust policy |

---

## Where OXIDE should differ

Code is not purely hierarchical.

A symbol may depend on code in unrelated files through:

- calls
- imports
- implementations
- inheritance
- tests
- references
- configuration
- change relationships

Therefore:

> **Tree for routing, graph edges for expansion.**

The graph should not necessarily remain a competing retrieval system. It can become a structural-expansion substrate for TreeSelect.

OXIDE also optimizes for **final context utility**, not PageIndex's page-scan cost.

A future TreeSelect objective is closer to:

```text
value =
    useful evidence
    - context tokens
    - latency
    - remote/API cost
    - noise
```

Possible evidence inputs:

- frozen FTS/RRF score
- embedding similarity
- structural role
- query-symbol match
- graph relationships
- coverage novelty
- Jev typed/value evidence

---

## Proposed OXIDE architecture

```text
frozen local retrieval
        ↓
candidate hierarchy
repo → module → file → symbol
        ↓
local semantic + structural evidence
        ↓
optional Jev typed/value evidence
        ↓
deterministic Rust TreeSelect
        ↓
structural/graph expansion
        ↓
budget packing
        ↓
final context
```

Rules:

- Retrieval, RRF and embeddings remain frozen initially.
- Jev supplies evidence, never authority.
- Rust owns traversal, thresholds, pruning, expansion and budget.
- Only compact candidate capsules may leave the machine.
- A fully local deterministic selector remains available.
- No PageIndex-style LLM reasoning loop is required.
- Do not implement TreeSelect until Jev experiment #40 provides evidence that justifies it.

---

## Main takeaway

The strongest PageIndex lesson for OXIDE is not “use a tree.”

It is:

> **Treat context selection as navigation over a structured state space, and make hierarchy earn its cost.**

Modern PageIndex increasingly separates learned proposals from deterministic policy. OXIDE can push that idea further with a Rust-native selector using local retrieval, embeddings, typed evidence and bounded structural expansion.

---

## Sources and status (2026-10-04)

- Value-based tree search (verified): PageIndex scores a node from its chunks' similarity scores as
  `NodeScore = Σ ChunkScore / √(N+1)`, so a node gains from more relevant chunks with diminishing
  returns (https://docs.pageindex.ai/tutorials/tree-search/hybrid). TreeSelect v0 adapts it
  (frozen candidate scores in place of chunk similarities); it is not PageIndex's production policy.
- PageIndex Flash (verified): local, layout-derived tree indexing; an LLM only summarizes located
  sections (https://pageindex.ai/blog/pageindex-flash, 2026-08-26).
- The tree-cost / `key_items` details above were not re-verified against source.
- The "wait for #40" rule above is superseded: #40 closed INCONCLUSIVE, and the TreeSelect
  experiment is local-only with no Jev input.
