//! Bounded impact neighborhood for a set of retrieval seeds: "if I change
//! this, what else is involved?"
//!
//! Built entirely on relations that already exist — `RelationGraph`'s
//! reverse indexes over the persisted `symbol_relations` table
//! (`structural_relations.rs`) plus its `related_tests`. No new graph, no
//! new store, no query-time AST work, and nothing here touches lexical or
//! semantic scoring.
//!
//! ## Why this needs its own bound
//!
//! `RelationGraph::callers_of`/`implementors_of` are repo-wide by
//! construction, and AGENTS.md requires every consumer to intersect their
//! result with an explicit bounded file scope before it reaches output — a
//! 902-file synthetic repo measured 60x more results unfiltered than the
//! same lookup scoped to a seed pool. `context.rs`'s expansion satisfies
//! that with the seed pool's own files, which is right for *expansion*:
//! it is looking for more of the same neighborhood.
//!
//! Blast radius cannot use that scope, because reaching code the seed
//! search did *not* surface is the entire point — a caller in an unrelated
//! file is exactly the thing a person asks this question to find. So it
//! carries its own explicit bound instead, and a stricter one: a hard cap
//! on distinct files ([`BLAST_RADIUS_MAX_FILES`]), on *direct* members per
//! seed ([`BLAST_RADIUS_PER_SEED`]), on the single transitive hop
//! ([`BLAST_RADIUS_TRANSITIVE_MAX`], budgeted separately — so one seed's
//! worst case is the sum of those two), and, absolutely, on the whole
//! neighborhood ([`BLAST_RADIUS_MAX_ITEMS`]). The traversal is
//! deterministic — seeds in rank order, relations in a fixed order, each
//! relation's own result already sorted `(file, start_line)` by
//! `RelationGraph` — so the caps cut the same set on every run.
//!
//! It is a *lead*, on the same bare-name heuristic tier as everything else
//! in `calls`/`bases` (AGENTS.md): two same-named symbols are
//! indistinguishable, so a listed item may belong to a different `Store`
//! than the one you meant. It is never a proof of impact.

use crate::config::{
    BLAST_RADIUS_MAX_FILES, BLAST_RADIUS_MAX_ITEMS, BLAST_RADIUS_MAX_SEEDS, BLAST_RADIUS_PER_SEED,
    BLAST_RADIUS_TRANSITIVE_MAX,
};
use crate::relations::RelationGraph;
use crate::symbols::{Symbol, SymbolKind};
use std::collections::HashSet;

/// One impacted symbol, with the evidence that put it there. Deliberately
/// not a `Symbol`: this is a pointer for the caller to go read, so it
/// carries location and provenance and no body, snippet or score.
#[derive(Debug, Clone, PartialEq, serde::Serialize)]
pub struct BlastItem {
    /// `path#QualifiedName`, the same symbol identity every other JSON
    /// surface uses.
    pub id: String,
    pub file: String,
    pub qualified_name: String,
    pub kind: SymbolKind,
    pub start_line: u32,
    pub end_line: u32,
    /// How this symbol is reached: `caller`, `implementor`, `test`, or
    /// `transitive-caller` (the one hop past a direct caller).
    pub relation: &'static str,
    /// 1 for a direct relation of the seed, 2 for the transitive hop.
    pub distance: u8,
    /// Qualified name of the seed this was reached from — the readable
    /// half of the provenance.
    pub via: String,
    /// `path#QualifiedName` of that seed. Present because `via` alone is
    /// not an identity: two files can hold a `Store`, and a consumer
    /// joining a member back to the hit it belongs to on the bare name
    /// would attach it to whichever one it met first.
    pub via_id: String,
}

/// Accumulator enforcing every cap in one place, so no traversal branch can
/// forget one.
struct Bounded<'a> {
    items: Vec<(BlastItem, &'a Symbol)>,
    seen: HashSet<u64>,
    files: Vec<String>,
}

impl<'a> Bounded<'a> {
    fn new(seeds: &[&Symbol]) -> Self {
        Self {
            items: Vec::new(),
            // Seeds never appear in their own blast radius.
            seen: seeds.iter().map(|s| s.id()).collect(),
            files: Vec::new(),
        }
    }

    fn full(&self) -> bool {
        self.items.len() >= BLAST_RADIUS_MAX_ITEMS
    }

    /// Admit `sym` unless it is already present, already a seed, would take
    /// the neighborhood past its item cap, or would open a file beyond the
    /// distinct-file cap. Returns whether it was admitted, so callers can
    /// enforce their own per-seed budgets against real additions only.
    fn push(
        &mut self,
        sym: &'a Symbol,
        relation: &'static str,
        distance: u8,
        via: &Symbol,
    ) -> bool {
        if self.full() || !self.seen.insert(sym.id()) {
            return false;
        }
        if !self.files.iter().any(|f| f == &sym.file) {
            if self.files.len() >= BLAST_RADIUS_MAX_FILES {
                // Undo the `seen` claim: this symbol was never admitted, and
                // a later relation must not be told it already reported it.
                self.seen.remove(&sym.id());
                return false;
            }
            self.files.push(sym.file.clone());
        }
        self.items.push((
            BlastItem {
                id: format!("{}#{}", sym.file, sym.qualified_name),
                file: sym.file.clone(),
                qualified_name: sym.qualified_name.clone(),
                kind: sym.kind,
                start_line: sym.start_line,
                end_line: sym.end_line,
                relation,
                distance,
                via: via.qualified_name.clone(),
                via_id: format!("{}#{}", via.file, via.qualified_name),
            },
            sym,
        ));
        true
    }
}

/// The bounded impact neighborhood of `seeds`, in traversal order.
///
/// Only the first [`BLAST_RADIUS_MAX_SEEDS`] seeds are anchored — a blast
/// radius taken from every hit of a 10-result search is a graph dump, not a
/// neighborhood. Relations are collected in a fixed order per seed
/// (callers, then implementors, then tests) so a truncated list is always
/// truncated at the least-direct evidence.
///
/// `transitive` adds one hop past the direct callers, capped separately and
/// hard at [`BLAST_RADIUS_TRANSITIVE_MAX`]: two hops of an unresolved
/// bare-name call graph is where precision falls off a cliff, so it is
/// opt-in per call rather than always on.
/// Each member is returned with the indexed `Symbol` it came from, so a
/// caller that needs the body (`context.rs` packs these as candidates) does
/// not have to look it up again by name — which would reintroduce exactly
/// the same-name ambiguity this is careful about everywhere else.
pub fn compute<'a>(
    graph: &RelationGraph<'a>,
    seeds: &[&Symbol],
    transitive: bool,
) -> Vec<(BlastItem, &'a Symbol)> {
    let anchors: Vec<&Symbol> = seeds.iter().take(BLAST_RADIUS_MAX_SEEDS).copied().collect();
    let mut out = Bounded::new(&anchors);

    for seed in &anchors {
        let mut from_seed = 0usize;
        // `implementors_of` only means anything for a type; asking it about
        // a function name is a guaranteed miss, so it is skipped rather
        // than spending a lookup and a cap slot on nothing.
        let implementors: Vec<&Symbol> =
            if matches!(seed.kind, SymbolKind::Class | SymbolKind::Interface) {
                graph.implementors_of(&seed.name)
            } else {
                Vec::new()
            };
        let groups: [(&'static str, Vec<&Symbol>); 3] = [
            ("caller", graph.callers_of(&seed.name)),
            ("implementor", implementors),
            ("test", graph.related_tests(seed)),
        ];
        for (relation, members) in groups {
            for sym in members {
                if from_seed >= BLAST_RADIUS_PER_SEED || out.full() {
                    break;
                }
                if out.push(sym, relation, 1, seed) {
                    from_seed += 1;
                }
            }
        }
    }

    if transitive {
        // Snapshot the direct callers first: `out.items` grows below, and
        // the hop must be taken from distance-1 evidence only, never from
        // its own output (that would be an unbounded walk wearing a cap).
        //
        // `Symbol::name` is taken from the symbol itself rather than sliced
        // off the tail of its qualified name: `callers_of` keys on the bare
        // declaration name, and Java's qualified names carry a parameter
        // signature (`Store.get(String)`), whose last dot-segment is
        // `get(String)` — a key that matches nothing, so the hop would have
        // silently found no Java callers at all.
        let by_id: std::collections::HashMap<&str, &Symbol> = anchors
            .iter()
            .map(|s| (s.qualified_name.as_str(), *s))
            .collect();
        let direct: Vec<(String, String)> = out
            .items
            .iter()
            .filter(|(i, _)| i.relation == "caller")
            .map(|(i, sym)| (sym.name.clone(), i.via.clone()))
            .collect();
        let mut added = 0usize;
        for (bare, via) in direct {
            if added >= BLAST_RADIUS_TRANSITIVE_MAX || out.full() {
                break;
            }
            // The hop is still attributed to the *seed*, not to the
            // intermediate caller: a reader asked what changing the seed
            // reaches, and one extra hop is still that seed's neighborhood.
            let Some(seed) = by_id.get(via.as_str()).copied() else {
                continue;
            };
            for sym in graph.callers_of(&bare) {
                if added >= BLAST_RADIUS_TRANSITIVE_MAX || out.full() {
                    break;
                }
                if out.push(sym, "transitive-caller", 2, seed) {
                    added += 1;
                }
            }
        }
    }

    out.items
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::symbols::Language;

    fn sym(file: &str, name: &str, kind: SymbolKind, calls: &[&str], bases: &[&str]) -> Symbol {
        Symbol {
            qualified_name: name.into(),
            name: name.rsplit('.').next().unwrap_or(name).into(),
            kind,
            language: Language::TypeScript,
            file: file.into(),
            start_line: 1,
            end_line: 2,
            content_hash: 0,
            signature: String::new(),
            imports: vec![],
            exported: true,
            parent: None,
            references: Vec::new(),
            calls: calls.iter().map(|s| s.to_string()).collect(),
            bases: bases.iter().map(|s| s.to_string()).collect(),
        }
    }

    #[test]
    fn direct_callers_and_implementors_are_reported_across_files() {
        // The whole reason this cannot reuse `context.rs`'s seed-pool file
        // scope: `b.ts` and `c.ts` were never retrieved, and finding them is
        // the point.
        let symbols = vec![
            sym("a.ts", "Store", SymbolKind::Class, &[], &[]),
            sym("b.ts", "useStore", SymbolKind::Function, &["Store"], &[]),
            sym("c.ts", "MemStore", SymbolKind::Class, &[], &["Store"]),
            sym("d.ts", "unrelated", SymbolKind::Function, &["other"], &[]),
        ];
        let graph = RelationGraph::build(&symbols);
        let items = compute(&graph, &[&symbols[0]], false);
        let got: Vec<(&str, &str)> = items
            .iter()
            .map(|(i, _)| (i.relation, i.qualified_name.as_str()))
            .collect();
        assert_eq!(
            got,
            vec![("caller", "useStore"), ("implementor", "MemStore")],
            "{items:?}"
        );
        assert!(items.iter().all(|(i, _)| i.distance == 1));
        assert!(items
            .iter()
            .all(|(i, s)| i.via == "Store" && s.file == i.file));
    }

    #[test]
    fn provenance_identifies_its_seed_by_path_not_by_bare_name() {
        // Two files each defining `Store`, both plausible search hits. `via`
        // alone cannot tell their neighborhoods apart, so a consumer joining
        // on it hangs one seed's callers off the other seed's hit.
        let symbols = vec![
            sym("a.ts", "Store", SymbolKind::Class, &[], &[]),
            sym("b.ts", "Store", SymbolKind::Class, &[], &[]),
            sym("ca.ts", "useA", SymbolKind::Function, &["Store"], &[]),
        ];
        let graph = RelationGraph::build(&symbols);
        let items = compute(&graph, &[&symbols[1]], false);
        assert!(!items.is_empty());
        for (item, _) in &items {
            assert_eq!(item.via, "Store");
            assert_eq!(item.via_id, "b.ts#Store", "the seed is the b.ts one");
        }
    }

    #[test]
    fn a_seed_never_appears_in_its_own_blast_radius() {
        // Recursion and same-file self-reference are both ordinary; neither
        // is impact.
        let symbols = vec![
            sym("a.ts", "walk", SymbolKind::Function, &["walk"], &[]),
            sym("b.ts", "start", SymbolKind::Function, &["walk"], &[]),
        ];
        let graph = RelationGraph::build(&symbols);
        let items = compute(&graph, &[&symbols[0]], false);
        assert_eq!(items.len(), 1);
        assert_eq!(items[0].0.qualified_name, "start");
    }

    #[test]
    fn one_seeds_direct_members_are_capped_and_the_hop_is_budgeted_separately() {
        // 40 callers in 40 files, none of them called by anything, so the
        // transitive hop contributes nothing and the direct cap is the only
        // thing cutting.
        let mut symbols = vec![sym("a.ts", "Store", SymbolKind::Class, &[], &[])];
        for i in 0..40 {
            symbols.push(sym(
                &format!("f{i:02}.ts"),
                &format!("caller{i:02}"),
                SymbolKind::Function,
                &["Store"],
                &[],
            ));
        }
        let graph = RelationGraph::build(&symbols);
        let items = compute(&graph, &[&symbols[0]], true);
        assert_eq!(items.len(), BLAST_RADIUS_PER_SEED, "{items:?}");
        let files: HashSet<&str> = items.iter().map(|(i, _)| i.file.as_str()).collect();
        assert!(files.len() <= BLAST_RADIUS_MAX_FILES);

        // Now give each of those callers a caller of its own, so the hop
        // actually fires alongside a *full* direct budget. The per-seed cap
        // covers direct members only — by design, see BLAST_RADIUS_PER_SEED
        // — so the honest bound for one seed is the sum of the two budgets,
        // and the absolute one is BLAST_RADIUS_MAX_ITEMS. The fixture above
        // could never distinguish those, which is why this half exists
        // (found by review).
        for i in 0..40 {
            symbols.push(sym(
                &format!("g{i:02}.ts"),
                &format!("outer{i:02}"),
                SymbolKind::Function,
                &[&format!("caller{i:02}")],
                &[],
            ));
        }
        let graph = RelationGraph::build(&symbols);
        let items = compute(&graph, &[&symbols[0]], true);
        let (direct, hop): (Vec<_>, Vec<_>) = items.iter().partition(|(i, _)| i.distance == 1);
        assert_eq!(direct.len(), BLAST_RADIUS_PER_SEED, "{items:?}");
        assert_eq!(hop.len(), BLAST_RADIUS_TRANSITIVE_MAX, "{items:?}");
        assert_eq!(
            items.len(),
            BLAST_RADIUS_PER_SEED + BLAST_RADIUS_TRANSITIVE_MAX,
            "one seed's worst case is the two budgets summed, not either alone"
        );
        assert!(items.len() <= BLAST_RADIUS_MAX_ITEMS, "{}", items.len());
        let files: HashSet<&str> = items.iter().map(|(i, _)| i.file.as_str()).collect();
        assert!(files.len() <= BLAST_RADIUS_MAX_FILES, "{}", files.len());
    }

    #[test]
    fn the_absolute_item_cap_holds_when_every_seed_spends_both_budgets() {
        // Three seeds, each with a full direct budget and a live hop: the
        // per-seed sums exceed BLAST_RADIUS_MAX_ITEMS, so only the absolute
        // cap can hold the line here.
        let mut symbols = Vec::new();
        for s in 0..BLAST_RADIUS_MAX_SEEDS {
            symbols.push(sym(
                &format!("seed{s}.ts"),
                &format!("Seed{s}"),
                SymbolKind::Class,
                &[],
                &[],
            ));
        }
        for s in 0..BLAST_RADIUS_MAX_SEEDS {
            for i in 0..6 {
                symbols.push(sym(
                    &format!("c{s}{i}.ts"),
                    &format!("call{s}{i}"),
                    SymbolKind::Function,
                    &[&format!("Seed{s}")],
                    &[],
                ));
                symbols.push(sym(
                    &format!("o{s}{i}.ts"),
                    &format!("outer{s}{i}"),
                    SymbolKind::Function,
                    &[&format!("call{s}{i}")],
                    &[],
                ));
            }
        }
        let seeds: Vec<&Symbol> = symbols[..BLAST_RADIUS_MAX_SEEDS].iter().collect();
        let graph = RelationGraph::build(&symbols);
        let items = compute(&graph, &seeds, true);
        assert!(items.len() <= BLAST_RADIUS_MAX_ITEMS, "{}", items.len());
        let files: HashSet<&str> = items.iter().map(|(i, _)| i.file.as_str()).collect();
        assert!(files.len() <= BLAST_RADIUS_MAX_FILES, "{}", files.len());
        for seed in &seeds {
            let direct = items
                .iter()
                .filter(|(i, _)| i.distance == 1 && i.via == seed.qualified_name)
                .count();
            assert!(direct <= BLAST_RADIUS_PER_SEED, "{direct}");
        }
        assert!(
            items.iter().filter(|(i, _)| i.distance == 2).count() <= BLAST_RADIUS_TRANSITIVE_MAX
        );
    }

    #[test]
    fn the_distinct_file_cap_holds_even_when_the_item_cap_does_not_bind() {
        // One caller per file, spread over more seeds than the file cap
        // allows, so the file cap is the only thing that can stop it.
        let mut symbols = Vec::new();
        for s in 0..BLAST_RADIUS_MAX_SEEDS {
            symbols.push(sym(
                &format!("seed{s}.ts"),
                &format!("Seed{s}"),
                SymbolKind::Class,
                &[],
                &[],
            ));
        }
        for s in 0..BLAST_RADIUS_MAX_SEEDS {
            for i in 0..BLAST_RADIUS_PER_SEED {
                symbols.push(sym(
                    &format!("c{s}_{i}.ts"),
                    &format!("call{s}_{i}"),
                    SymbolKind::Function,
                    &[&format!("Seed{s}")],
                    &[],
                ));
            }
        }
        let seeds: Vec<&Symbol> = symbols[..BLAST_RADIUS_MAX_SEEDS].iter().collect();
        let graph = RelationGraph::build(&symbols);
        let items = compute(&graph, &seeds, false);
        let files: HashSet<&str> = items.iter().map(|(i, _)| i.file.as_str()).collect();
        assert!(
            files.len() <= BLAST_RADIUS_MAX_FILES,
            "{} distinct files",
            files.len()
        );
    }

    #[test]
    fn the_transitive_hop_is_opt_in_and_separately_capped() {
        // A -> B -> C chain: without the hop C is invisible; with it C
        // appears exactly once, at distance 2, attributed to the seed.
        let symbols = vec![
            sym("a.ts", "target", SymbolKind::Function, &[], &[]),
            sym("b.ts", "mid", SymbolKind::Function, &["target"], &[]),
            sym("c.ts", "outer", SymbolKind::Function, &["mid"], &[]),
        ];
        let graph = RelationGraph::build(&symbols);
        let without = compute(&graph, &[&symbols[0]], false);
        assert_eq!(without.len(), 1, "{without:?}");

        let with = compute(&graph, &[&symbols[0]], true);
        let (outer, _) = with
            .iter()
            .find(|(i, _)| i.qualified_name == "outer")
            .expect("one hop past the direct caller");
        assert_eq!(
            (outer.relation, outer.distance, outer.via.as_str()),
            ("transitive-caller", 2, "target")
        );
    }

    #[test]
    fn the_transitive_hop_works_for_a_language_whose_names_carry_a_signature() {
        // Java qualified names are `Store.get(String)`. Deriving the bare
        // declaration name by slicing off the last dot-segment yields
        // `get(String)`, which `callers_of` keys on nothing — so the hop
        // found no Java callers at all, silently. The symbol's own `name`
        // field is the right source and is already in hand.
        let java = |file: &str, qname: &str, name: &str, calls: &[&str]| Symbol {
            qualified_name: qname.into(),
            name: name.into(),
            kind: SymbolKind::Method,
            language: Language::Java,
            file: file.into(),
            start_line: 1,
            end_line: 2,
            content_hash: 0,
            signature: String::new(),
            imports: vec![],
            exported: true,
            parent: None,
            references: Vec::new(),
            calls: calls.iter().map(|s| s.to_string()).collect(),
            bases: Vec::new(),
        };
        let symbols = vec![
            java("Target.java", "Target.run()", "run", &[]),
            java("Mid.java", "Mid.call(String)", "call", &["run"]),
            java("Outer.java", "Outer.top()", "top", &["call"]),
        ];
        let graph = RelationGraph::build(&symbols);
        let items = compute(&graph, &[&symbols[0]], true);
        let names: Vec<&str> = items
            .iter()
            .map(|(i, _)| i.qualified_name.as_str())
            .collect();
        assert_eq!(
            names,
            vec!["Mid.call(String)", "Outer.top()"],
            "the transitive hop must reach past a signature-bearing name"
        );
        assert_eq!(items[1].0.distance, 2);
    }

    #[test]
    fn traversal_is_deterministic_across_runs() {
        // `callers_of`/`implementors_of` read out of HashMaps; the sort they
        // apply is what keeps a truncated list stable, and truncation is the
        // normal case here.
        let mut symbols = vec![sym("a.ts", "Store", SymbolKind::Class, &[], &[])];
        for i in 0..30 {
            symbols.push(sym(
                &format!("f{i:02}.ts"),
                &format!("caller{i:02}"),
                SymbolKind::Function,
                &["Store"],
                &[],
            ));
        }
        let graph = RelationGraph::build(&symbols);
        let first: Vec<BlastItem> = compute(&graph, &[&symbols[0]], true)
            .into_iter()
            .map(|(i, _)| i)
            .collect();
        for _ in 0..8 {
            let graph = RelationGraph::build(&symbols);
            let again: Vec<BlastItem> = compute(&graph, &[&symbols[0]], true)
                .into_iter()
                .map(|(i, _)| i)
                .collect();
            assert_eq!(first, again);
        }
    }
}
