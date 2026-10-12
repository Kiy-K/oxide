//! Experiment aid for docs/agent-navigation-eval/, not a product surface or
//! a contract: it renders existing kernel artifacts for a coding agent. Release
//! build, one store per task:
//!
//! ```text
//! agent_nav index  <src-dir> <store-dir> <repo-id>        capture, derive, publish
//! agent_nav bundle <store-dir> <query-file> <budget> <out-dir>
//!                                   bundle.txt (B), nav.txt (C/D), run.json
//! agent_nav find   <store-dir> <name>                      exact-name lookup (D)
//! agent_nav expand <store-dir> <ref> [kind] [in|out|both]  typed relations (D)
//! agent_nav route  <store-dir> <query>                     NavigationBundle (D)
//! ```
//!
//! A `<ref>` is `path` or `path::Outer.inner` (`name#n` for the n-th
//! same-named sibling), as the output prints it. Every location is a
//! 1-based line range of the pinned snapshot's captured bytes. Nothing here
//! judges code; it only exposes routing, selection and adjacency evidence.
//! With `OXIDE_NAV_LOG` set, each call appends its cost as one JSON line.

use std::collections::{BTreeMap, BTreeSet};
use std::fmt::Write as _;
use std::io::Write as _;
use std::path::Path;
use std::time::Instant;

use oxide_kernel::context::{ContextRequest, ContextRun, baseline, build_context};
use oxide_kernel::id::{
    DerivationId, EntityId, RepoId, RepoPath, Segment, SnapshotId, SnapshotKey, SymbolId,
};
use oxide_kernel::knowledge::{Basis, Containment, Entity, RelationKind, Target};
use oxide_kernel::pack::{count, counter, lines};
use oxide_kernel::query::{ContextBudget, Query, QueryContext};
use oxide_kernel::retrieve::ChannelState;
use oxide_kernel::select::Reason;
use oxide_kernel::source::Sources;
use oxide_kernel::store::{AdjacencyRequest, Direction, KnowledgeStore, ReadView};
use oxide_runtime::capture::{Scope, capture};
use oxide_runtime::derivation::derive;
use oxide_runtime::storage::{LadybugStore, LadybugView};
use serde_json::json;

/// Locations in one NavigationBundle, relation targets shown per group,
/// edges read per location, results of `find`/`expand`.
const LOCATIONS: usize = 12;
const PER_GROUP: usize = 3;
const EDGES: usize = 64;
const RESULTS: usize = 40;
/// Edges `expand` reads before collapsing call sites.
const EXPAND_EDGES: usize = 1024;
const PREVIEW_CHARS: usize = 120;
/// The context budget `route` routes under (the B bundle's budget).
const ROUTE_BUDGET: u32 = 4096;
/// Publication of mid-size repositories needs a large pool (docs/phase-5.md).
const INDEX_POOL: u64 = 1 << 30;

fn peak_rss_kib() -> u64 {
    let status = std::fs::read_to_string("/proc/self/status").unwrap_or_default();
    let line = status.lines().find(|l| l.starts_with("VmHWM:"));
    line.and_then(|l| l.split_whitespace().nth(1)?.parse().ok())
        .unwrap_or(0)
}

fn main() {
    let started = Instant::now();
    let args: Vec<String> = std::env::args().skip(1).collect();
    let (cmd, rest) = args.split_first().expect("usage: see the module docs");
    if cmd == "index" {
        return index(Path::new(&rest[0]), Path::new(&rest[1]), &rest[2]);
    }
    let (store, view, open_ms) = open(Path::new(&rest[0]));
    let out = match cmd.as_str() {
        "bundle" => {
            let query = std::fs::read_to_string(&rest[1]).unwrap();
            let out_dir = Path::new(&rest[3]);
            bundle(&view, &query, rest[2].parse().unwrap(), out_dir, open_ms);
            String::new()
        }
        "find" => find(&view, &rest[1]),
        "expand" => expand(
            &view,
            &rest[1],
            rest.get(2).map_or("all", String::as_str),
            rest.get(3).map_or("both", String::as_str),
        ),
        "route" => navigation(&view, &context(&view, &rest[1..].join(" "), ROUTE_BUDGET)),
        _ => panic!("unknown command {cmd}"),
    };
    print!("{out}");
    drop(view);
    drop(store);
    if let Some(log) = std::env::var_os("OXIDE_NAV_LOG") {
        let line = json!({"cmd": cmd, "args": &rest[1..], "open_ms": open_ms,
            "total_ms": started.elapsed().as_secs_f64() * 1e3,
            "peak_rss_kib": peak_rss_kib(), "output_units": count(&out)});
        let mut f = std::fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(log)
            .unwrap();
        writeln!(f, "{line}").unwrap();
    }
}

fn index(src: &Path, store_dir: &Path, repo: &str) {
    let started = Instant::now();
    let source = capture(src, &Scope::default()).unwrap();
    let (manifest, batch, components) = derive(&RepoId::new(repo).unwrap(), &source).unwrap();
    let key = manifest.key.clone();
    let (entities, relations) = (batch.entities.len(), batch.relations.len());
    let _ = std::fs::remove_dir_all(store_dir);
    let mut store = LadybugStore::with_buffer_pool(store_dir, INDEX_POOL).unwrap();
    store.begin(manifest).unwrap();
    store.retain_derivation(&key, &components).unwrap();
    store.retain_source(&source).unwrap();
    store.write(&key, batch).unwrap();
    store.publish(&key).unwrap();
    drop(store);
    let k = json!([
        key.repo.as_str(),
        key.snapshot.as_str(),
        key.derivation.as_str()
    ]);
    std::fs::write(store_dir.join("key.json"), k.to_string()).unwrap();
    let report = json!({"files": source.files.len(), "skipped": source.skipped.len(),
        "entities": entities, "relations": relations,
        "index_seconds": started.elapsed().as_secs_f64(), "peak_rss_kib": peak_rss_kib(),
        "snapshot": key.snapshot.as_str(), "derivation": key.derivation.as_str()});
    println!("{report}");
}

fn open(store_dir: &Path) -> (LadybugStore, LadybugView, f64) {
    let started = Instant::now();
    let key = std::fs::read_to_string(store_dir.join("key.json")).unwrap();
    let k: Vec<String> = serde_json::from_str(&key).unwrap();
    let key = SnapshotKey {
        repo: RepoId::new(&k[0]).unwrap(),
        snapshot: SnapshotId::new(&k[1]).unwrap(),
        derivation: DerivationId::new(&k[2]).unwrap(),
    };
    let store = LadybugStore::new(store_dir).unwrap();
    let view = store.open(&key).unwrap();
    (store, view, started.elapsed().as_secs_f64() * 1e3)
}

fn context(view: &LadybugView, query: &str, tokens: u32) -> ContextRun {
    let request = ContextRequest {
        query: Query { text: query.into() },
        context: QueryContext::default(),
        budget: ContextBudget {
            tokens,
            counter: counter(),
        },
        semantic: ChannelState::Unconfigured,
    };
    build_context(view, view, &request, &baseline(), None).unwrap()
}

fn bundle(view: &LadybugView, query: &str, tokens: u32, out: &Path, open_ms: f64) {
    let started = Instant::now();
    let run = context(view, query, tokens);
    let context_ms = started.elapsed().as_secs_f64() * 1e3;
    let started = Instant::now();
    let nav = navigation(view, &run);
    let nav_ms = started.elapsed().as_secs_f64() * 1e3;
    let payload = run.bundle.payload();
    std::fs::create_dir_all(out).unwrap();
    std::fs::write(out.join("bundle.txt"), &payload).unwrap();
    std::fs::write(out.join("nav.txt"), &nav).unwrap();
    let bundle_files: BTreeSet<&str> = run
        .bundle
        .items
        .iter()
        .map(|i| i.source.file.as_str())
        .collect();
    let report = json!({
        "budget": tokens, "bundle_units": run.bundle.used_tokens, "nav_units": count(&nav),
        "bundle_items": run.bundle.items.len(), "routed": run.route.candidates.len(),
        "graph_nodes": run.graph.nodes.len(), "plan_items": run.plan.items.len(),
        "bundle_files": bundle_files,
        "open_ms": open_ms, "context_ms": context_ms, "nav_ms": nav_ms,
        "peak_rss_kib": peak_rss_kib(),
        "snapshot": run.bundle.snapshot.snapshot.as_str(),
    });
    std::fs::write(out.join("run.json"), report.to_string()).unwrap();
}

/// `path` or `path::A.b#1` for an entity; `None` for modules and the root.
fn reference(id: &EntityId) -> Option<String> {
    match id {
        EntityId::File(p) => Some(p.as_str().into()),
        EntityId::Symbol(s) => {
            let path: Vec<String> = s
                .path()
                .iter()
                .map(|g| match g.ordinal {
                    0 => g.name.clone(),
                    n => format!("{}#{n}", g.name),
                })
                .collect();
            Some(format!("{}::{}", s.file().as_str(), path.join(".")))
        }
        _ => None,
    }
}

fn parse_reference(r: &str) -> Option<EntityId> {
    let Some((file, qual)) = r.split_once("::") else {
        return Some(EntityId::File(RepoPath::new(r).ok()?));
    };
    let segment = |s: &str| match s.split_once('#') {
        Some((name, n)) => Some(Segment {
            name: name.into(),
            ordinal: n.parse().ok()?,
        }),
        None => Some(Segment {
            name: s.into(),
            ordinal: 0,
        }),
    };
    let path = qual.split('.').map(segment).collect::<Option<Vec<_>>>()?;
    let id = SymbolId::new(RepoPath::new(file).ok()?, path).ok()?;
    Some(EntityId::Symbol(id))
}

/// Resolves entities and their 1-based line ranges from verified bytes.
struct Locator<'a> {
    view: &'a LadybugView,
    sources: Sources<'a>,
    entities: BTreeMap<EntityId, Option<Entity>>,
}

impl<'a> Locator<'a> {
    fn new(view: &'a LadybugView) -> Self {
        Self {
            view,
            sources: Sources::new(view),
            entities: BTreeMap::new(),
        }
    }

    fn load(&mut self, ids: impl IntoIterator<Item = EntityId>) {
        let missing: BTreeSet<EntityId> = ids
            .into_iter()
            .filter(|i| !self.entities.contains_key(i))
            .collect();
        let missing: Vec<EntityId> = missing.into_iter().collect();
        for chunk in missing.chunks(1024) {
            for (id, e) in chunk.iter().zip(self.view.entities(chunk).unwrap()) {
                self.entities.insert(id.clone(), e);
            }
        }
    }

    fn entity(&mut self, id: &EntityId) -> Option<Entity> {
        self.load([id.clone()]);
        self.entities[id].clone()
    }

    /// `path:first-last` and a one-line preview; the path alone when the
    /// entity has no source range.
    fn location(&mut self, e: &Entity) -> (String, Option<String>) {
        let Some(s) = &e.source else {
            return (reference(&e.id).unwrap_or_default(), None);
        };
        let Ok(bytes) = self.sources.file(&s.file, &s.digest).unwrap() else {
            return (format!("{} (source unavailable)", s.file.as_str()), None);
        };
        let (first, last) = lines(&bytes, s.range);
        let text = std::str::from_utf8(&bytes[s.range.start as usize..s.range.end as usize]);
        let preview = e.signature.clone().or_else(|| {
            let line = text.ok()?.lines().find(|l| !l.trim().is_empty())?;
            Some(line.trim().to_owned())
        });
        let preview = preview.map(|p| {
            let one_line = p.split_whitespace().collect::<Vec<_>>().join(" ");
            one_line.chars().take(PREVIEW_CHARS).collect()
        });
        (format!("{}:{first}-{last}", s.file.as_str()), preview)
    }

    /// `ref [first-last]` for a resolved target, else the evidence we have.
    fn target(&mut self, t: &Target) -> String {
        match t {
            Target::Resolved(id) => match self.entity(id) {
                Some(e) => {
                    let (place, _) = self.location(&e);
                    match (reference(id), place.rsplit_once(':')) {
                        (Some(r), Some((_, range))) if e.source.is_some() => {
                            format!("{r} [{range}]")
                        }
                        (Some(r), _) => r,
                        (None, _) => place,
                    }
                }
                None => format!("{id:?} (missing)"),
            },
            Target::Ambiguous(ids) => {
                let refs: Vec<String> = ids.iter().filter_map(reference).take(PER_GROUP).collect();
                format!("ambiguous: {}", refs.join(" | "))
            }
            Target::Unresolved { name } => format!("{name} (unresolved)"),
        }
    }
}

fn kind_name(k: RelationKind) -> &'static str {
    match k {
        RelationKind::Contains(Containment::Physical) => "contains",
        RelationKind::Contains(Containment::Logical) => "contains(logical)",
        RelationKind::Defines => "defines",
        RelationKind::References => "refs",
        RelationKind::Calls => "calls",
        RelationKind::Imports => "imports",
        RelationKind::Implements => "implements",
        RelationKind::TestedBy => "tests",
    }
}

fn basis(b: Basis) -> &'static str {
    match b {
        Basis::Syntactic => "syntactic",
        Basis::Resolved => "resolved",
        Basis::Heuristic => "heuristic",
    }
}

/// Grouped relation lines for one entity, both directions. Containment and
/// definition edges are the tree itself; `expand <ref> contains` shows them.
fn relation_lines(loc: &mut Locator<'_>, id: &EntityId, out: &mut String) {
    let request = AdjacencyRequest {
        entity: id.clone(),
        direction: Direction::Both,
        kinds: vec![
            RelationKind::Calls,
            RelationKind::References,
            RelationKind::Imports,
            RelationKind::Implements,
            RelationKind::TestedBy,
        ],
        limit: EDGES,
    };
    let adjacency = loc.view.adjacency(&request).unwrap();
    // Distinct targets per group, first seen first; unresolved names only counted.
    let mut groups: BTreeMap<String, (Vec<String>, usize)> = BTreeMap::new();
    for edge in &adjacency.edges {
        let (label, target) = if edge.from == *id {
            (format!("{} →", kind_name(edge.kind)), edge.to.clone())
        } else {
            let from = Target::Resolved(edge.from.clone());
            (format!("← {}", kind_name(edge.kind)), from)
        };
        let group = groups.entry(label).or_default();
        if matches!(target, Target::Unresolved { .. }) {
            group.1 += 1;
            continue;
        }
        let shown = loc.target(&target);
        if !group.0.contains(&shown) {
            group.0.push(shown);
        }
    }
    for (label, (targets, unresolved)) in groups {
        if targets.is_empty() {
            continue;
        }
        let shown = targets[..targets.len().min(PER_GROUP)].join("; ");
        let _ = write!(out, "   {label} {shown}");
        if targets.len() > PER_GROUP {
            let _ = write!(out, "; +{} more", targets.len() - PER_GROUP);
        }
        if unresolved > 0 {
            let _ = write!(out, " (+{unresolved} unresolved names)");
        }
        out.push('\n');
    }
    if adjacency.truncated {
        let _ = writeln!(out, "   (relations truncated at {EDGES})");
    }
}

/// The NavigationBundle: selected plan entities in plan order, then the
/// rest of the routed graph in graph order. Locations and evidence only.
fn navigation(view: &LadybugView, run: &ContextRun) -> String {
    let mut why: Vec<(EntityId, String)> = Vec::new();
    for item in &run.plan.items {
        let reason = match &item.reason {
            Reason::Primary { entry: true, .. } => "selected; entry point".to_owned(),
            Reason::Primary { depth, .. } => format!("selected; routed depth {depth}"),
            Reason::Expanded { seed, relation, .. } => format!(
                "selected; expanded from {} via {}",
                reference(seed).unwrap_or_default(),
                kind_name(*relation)
            ),
            Reason::Prerequisite { .. } => continue,
        };
        why.push((item.entity.clone(), reason));
    }
    for node in &run.graph.nodes {
        let c = &node.candidate;
        let reason = match c.channels.first() {
            Some(ch) => format!("routed; lexical #{}", ch.rank),
            None if !c.seeds.is_empty() => "routed; structural seed".into(),
            None => format!("routed depth {}", c.depth),
        };
        why.push((c.entity.clone(), reason));
    }
    let mut seen = BTreeSet::new();
    why.retain(|(id, _)| reference(id).is_some() && seen.insert(id.clone()));
    why.truncate(LOCATIONS);

    let key = &run.bundle.snapshot;
    let mut out = format!(
        "OXIDE navigation (snapshot {}, derivation {}). Ranked by OXIDE routing/selection, \
         not verified relevant. Entries are path::Qualified.name [first-last line, 1-based, \
         in this checkout].\n",
        short(key.snapshot.as_str()),
        short(key.derivation.as_str())
    );
    let mut loc = Locator::new(view);
    loc.load(why.iter().map(|(id, _)| id.clone()));
    for (n, (id, reason)) in why.iter().enumerate() {
        let Some(e) = loc.entity(id) else { continue };
        let (_, preview) = loc.location(&e);
        let shown = loc.target(&Target::Resolved(id.clone()));
        let _ = writeln!(out, "{}. {} {shown}", n + 1, e.kind);
        if let Some(p) = preview {
            let _ = writeln!(out, "   preview: {p}");
        }
        let _ = writeln!(out, "   why: {reason}");
        relation_lines(&mut loc, id, &mut out);
    }
    if why.is_empty() {
        out.push_str("(no routed locations)\n");
    }
    out
}

fn short(s: &str) -> &str {
    let s = s.strip_prefix("sha256:").unwrap_or(s);
    &s[..s.len().min(12)]
}

fn find(view: &LadybugView, name: &str) -> String {
    let named = view.named(name, RESULTS).unwrap();
    let mut loc = Locator::new(view);
    loc.load(named.entities.iter().cloned());
    let mut out = String::new();
    for id in &named.entities {
        let Some(e) = loc.entity(id) else { continue };
        let shown = loc.target(&Target::Resolved(id.clone()));
        let _ = writeln!(out, "{} {shown}", e.kind);
    }
    if named.truncated {
        let _ = writeln!(out, "(truncated at {RESULTS})");
    }
    if out.is_empty() {
        out = format!("no entity named {name:?}\n");
    }
    out
}

fn expand(view: &LadybugView, r: &str, kind: &str, direction: &str) -> String {
    let Some(id) = parse_reference(r) else {
        return format!("invalid reference {r:?}; use path or path::Outer.inner\n");
    };
    let kinds = match kind {
        "calls" => vec![RelationKind::Calls],
        "refs" => vec![RelationKind::References],
        "imports" => vec![RelationKind::Imports],
        "tests" => vec![RelationKind::TestedBy],
        "contains" => vec![RelationKind::Contains(Containment::Physical)],
        "implements" => vec![RelationKind::Implements],
        "all" => RelationKind::ALL.to_vec(),
        _ => return format!("unknown kind {kind:?}\n"),
    };
    let direction = match direction {
        "in" => Direction::Incoming,
        "out" => Direction::Outgoing,
        "both" => Direction::Both,
        _ => return format!("unknown direction {direction:?}; use in|out|both\n"),
    };
    let request = AdjacencyRequest {
        entity: id.clone(),
        direction,
        kinds,
        limit: EXPAND_EDGES,
    };
    let adjacency = match view.adjacency(&request) {
        Ok(a) => a,
        Err(e) => return format!("{r}: {e}\n"),
    };
    // One line per distinct relation; call sites of the same pair are counted.
    let mut loc = Locator::new(view);
    let mut lines: Vec<(String, usize)> = Vec::new();
    for edge in &adjacency.edges {
        let (arrow, other) = if edge.from == id {
            ("→", loc.target(&edge.to))
        } else {
            ("←", loc.target(&Target::Resolved(edge.from.clone())))
        };
        let b = basis(edge.basis);
        let line = format!("{} {arrow} {other} ({b})", kind_name(edge.kind));
        match lines.iter_mut().find(|(l, _)| *l == line) {
            Some((_, n)) => *n += 1,
            None => lines.push((line, 1)),
        }
    }
    let mut out = String::new();
    for (line, n) in lines.iter().take(RESULTS) {
        let _ = match n {
            1 => writeln!(out, "{line}"),
            n => writeln!(out, "{line} x{n}"),
        };
    }
    if lines.len() > RESULTS || adjacency.truncated {
        let _ = writeln!(
            out,
            "(truncated: first {RESULTS} distinct of {EXPAND_EDGES} edges read)"
        );
    }
    if out.is_empty() {
        out = format!("no {kind} relations for {r}\n");
    }
    out
}
