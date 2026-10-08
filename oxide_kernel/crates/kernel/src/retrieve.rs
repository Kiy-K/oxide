//! Entry-point retrieval (SPEC § Retrieval): lexical hits and structural
//! seeds from the typed query context become one [`CandidateSet`]. Entry
//! points start TreeRouter; they are never final context, and a candidate
//! here implies nothing about inclusion.

use std::collections::BTreeMap;

use crate::id::{EntityId, RepoPath, SnapshotKey};
use crate::knowledge::{ByteRange, Containment, RelationKind};
use crate::lexical::{LexicalRequest, terms};
use crate::query::{Query, QueryContext};
use crate::store::{AdjacencyRequest, Direction, MAX_REQUEST_ITEMS, ReadView, StoreError};

/// Version of the merge and seeding rules below.
pub const RETRIEVAL_VERSION: &str = "oxide-retrieval-v1";

/// Scored retrieval channel. Structural seeds are not a scored channel:
/// they carry reasons ([`Seed`]), not ranks.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub enum Channel {
    Lexical,
    Semantic,
}

/// One channel's evidence, on that channel's own scale. A missing channel is
/// absent from the list, never a score of zero.
#[derive(Debug, Clone, PartialEq)]
pub struct ChannelEvidence {
    pub channel: Channel,
    /// 1-based position in that channel's result.
    pub rank: u32,
    pub score: f64,
    /// The scorer that gave `score` meaning.
    pub scorer: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ChannelState {
    Ran {
        scorer: String,
        returned: usize,
        /// The channel limit cut further matches.
        truncated: bool,
    },
    /// Turned off by the configuration (an ablation).
    Disabled,
    /// No provider is configured: a supported mode, not a failure.
    Unconfigured,
    /// Expected but not usable now. Never reported as empty or zero.
    Unavailable(String),
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ChannelStatus {
    pub channel: Channel,
    pub state: ChannelState,
}

/// Which explicit hint produced a structural seed.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub enum HintKind {
    Selection,
    Path,
    Symbol,
    Changed,
}

/// Why an entity is a structural entry point, kept for traces.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Seed {
    pub kind: HintKind,
    /// The hint as given.
    pub hint: String,
    /// The path matched only after case folding (ADR-0010 § RepoPath).
    pub case_insensitive: bool,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum HintIssue {
    NotFound,
    /// Several case-folded paths matched; none was chosen.
    Ambiguous {
        matches: usize,
    },
    /// More entities matched than `limit` (the seed limit, or the store's
    /// request bound for the name lookup itself); the first ones seeded, so
    /// the result may be incomplete.
    Truncated {
        limit: usize,
    },
}

/// A hint that seeded nothing, or not everything it matched.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct HintDiagnostic {
    pub kind: HintKind,
    pub hint: String,
    pub issue: HintIssue,
}

/// A starting point for routing, merged by entity across channels and
/// seeds with every piece of evidence kept.
#[derive(Debug, Clone, PartialEq)]
pub struct EntryPoint {
    pub entity: EntityId,
    pub channels: Vec<ChannelEvidence>,
    pub seeds: Vec<Seed>,
}

/// Declared retrieval limits and channel switches. Candidate limits bound
/// runtime cost; they are not context budgets.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct RetrievalConfig {
    pub lexical: bool,
    pub structural: bool,
    pub lexical_limit: usize,
    /// Entities one symbol hint may seed.
    pub symbol_limit: usize,
}

/// The frozen v2 baseline (docs/phase-3.md).
pub const BASELINE: RetrievalConfig = RetrievalConfig {
    lexical: true,
    structural: true,
    lexical_limit: 20,
    symbol_limit: 8,
};

/// Bounded, deduplicated entry points for one query and one generation.
/// Order: structural seeds in hint order (selection, paths, symbols,
/// changed), then lexical rank. This is a declared precedence, not a fused
/// score: channel scores stay on their own scales.
#[derive(Debug, Clone, PartialEq)]
pub struct CandidateSet {
    pub snapshot: SnapshotKey,
    pub version: &'static str,
    pub config: RetrievalConfig,
    pub channels: Vec<ChannelStatus>,
    pub entries: Vec<EntryPoint>,
    pub hints: Vec<HintDiagnostic>,
}

/// Retrieves entry points from one pinned view. `semantic` is the semantic
/// channel's state as the runtime found it; no vector channel runs in this
/// phase, so it is reported, never scored. An unavailable lexical
/// capability is reported, not an error.
pub fn retrieve(
    view: &impl ReadView,
    query: &Query,
    context: &QueryContext,
    semantic: ChannelState,
    config: RetrievalConfig,
) -> Result<CandidateSet, StoreError> {
    let mut set = CandidateSet {
        snapshot: view.snapshot().key.clone(),
        version: RETRIEVAL_VERSION,
        config,
        channels: Vec::new(),
        entries: Vec::new(),
        hints: Vec::new(),
    };
    let mut position: BTreeMap<EntityId, usize> = BTreeMap::new();
    let mut entry = |set: &mut CandidateSet, entity: EntityId| -> usize {
        *position.entry(entity.clone()).or_insert_with(|| {
            set.entries.push(EntryPoint {
                entity,
                channels: Vec::new(),
                seeds: Vec::new(),
            });
            set.entries.len() - 1
        })
    };

    if config.structural {
        for (kind, hint, (entity, case_insensitive)) in
            seeds(view, context, config, &mut set.hints)?
        {
            let i = entry(&mut set, entity);
            let seed = Seed {
                kind,
                hint,
                case_insensitive,
            };
            if !set.entries[i].seeds.contains(&seed) {
                set.entries[i].seeds.push(seed);
            }
        }
    }

    let lexical = if !config.lexical {
        ChannelState::Disabled
    } else {
        let request = LexicalRequest {
            terms: terms(&query.text),
            limit: config.lexical_limit,
        };
        match view.lexical(&request) {
            Ok(result) => {
                for (i, hit) in result.hits.iter().enumerate() {
                    let at = entry(&mut set, hit.entity.clone());
                    set.entries[at].channels.push(ChannelEvidence {
                        channel: Channel::Lexical,
                        rank: i as u32 + 1,
                        score: hit.score,
                        scorer: result.scorer.clone(),
                    });
                }
                ChannelState::Ran {
                    scorer: result.scorer,
                    returned: result.hits.len(),
                    truncated: result.truncated,
                }
            }
            Err(StoreError::Unsupported(why)) => ChannelState::Unavailable(why),
            Err(error) => return Err(error),
        }
    };
    set.channels = vec![
        ChannelStatus {
            channel: Channel::Lexical,
            state: lexical,
        },
        ChannelStatus {
            channel: Channel::Semantic,
            state: semantic,
        },
    ];
    Ok(set)
}

/// Every hint resolved against the pinned generation, in hint order:
/// (kind, hint text, (entity, matched case-insensitively)).
type Resolved = (HintKind, String, (EntityId, bool));

fn seeds(
    view: &impl ReadView,
    context: &QueryContext,
    config: RetrievalConfig,
    hints: &mut Vec<HintDiagnostic>,
) -> Result<Vec<Resolved>, StoreError> {
    let mut out = Vec::new();
    let mut report = |kind, hint: &str, issue| {
        hints.push(HintDiagnostic {
            kind,
            hint: hint.into(),
            issue,
        })
    };
    if let Some((path, range)) = &context.selection {
        let hint = format!("{}@{}..{}", path.as_str(), range.start, range.end);
        match file(view, path) {
            Ok((file, folded)) => {
                let innermost = innermost(view, file, *range)?;
                out.push((HintKind::Selection, hint, (innermost, folded)));
            }
            Err(issue) => report(HintKind::Selection, &hint, issue),
        }
    }
    for path in &context.paths {
        match file(view, path) {
            Ok(found) => out.push((HintKind::Path, path.as_str().into(), found)),
            Err(issue) => report(HintKind::Path, path.as_str(), issue),
        }
    }
    for hint in &context.symbols {
        let (found, cut) = symbols(view, hint, config.symbol_limit)?;
        if let Some(limit) = cut {
            report(HintKind::Symbol, hint, HintIssue::Truncated { limit });
        } else if found.is_empty() {
            report(HintKind::Symbol, hint, HintIssue::NotFound);
        }
        let seeded = found.into_iter().map(|id| (id, false));
        out.extend(seeded.map(|found| (HintKind::Symbol, hint.clone(), found)));
    }
    for path in &context.changed {
        match file(view, path) {
            Ok(found) => out.push((HintKind::Changed, path.as_str().into(), found)),
            Err(issue) => report(HintKind::Changed, path.as_str(), issue),
        }
    }
    Ok(out)
}

/// A path hint resolves exactly first; failing that, to a unique
/// case-folded match (ADR-0010 § RepoPath).
fn file(view: &impl ReadView, path: &RepoPath) -> Result<(EntityId, bool), HintIssue> {
    let files = &view.snapshot().files;
    if files.contains_key(path) {
        return Ok((EntityId::File(path.clone()), false));
    }
    let folded = path.as_str().to_lowercase();
    let mut matches = files.keys().filter(|p| p.as_str().to_lowercase() == folded);
    match (matches.next(), matches.count()) {
        (None, _) => Err(HintIssue::NotFound),
        (Some(p), 0) => Ok((EntityId::File(p.clone()), true)),
        (Some(_), more) => Err(HintIssue::Ambiguous { matches: more + 1 }),
    }
}

/// The innermost declaration whose source contains `range`, or the file.
/// Containment is exact, so a cut child list (over `MAX_REQUEST_ITEMS`
/// children) can only stop the descent early, at an enclosing scope, never
/// pick a wrong declaration.
fn innermost(
    view: &impl ReadView,
    file: EntityId,
    range: ByteRange,
) -> Result<EntityId, StoreError> {
    let mut current = file;
    loop {
        let children: Vec<EntityId> = view
            .adjacency(&AdjacencyRequest {
                entity: current.clone(),
                direction: Direction::Outgoing,
                kinds: vec![RelationKind::Contains(Containment::Physical)],
                limit: MAX_REQUEST_ITEMS,
            })?
            .edges
            .into_iter()
            .flat_map(|edge| edge.to.entities().to_vec())
            .collect();
        let containing = view.entities(&children)?.into_iter().flatten().find(|e| {
            e.source
                .as_ref()
                .is_some_and(|s| s.range.start <= range.start && range.end <= s.range.end)
        });
        match containing {
            Some(child) => current = child.id,
            None => return Ok(current),
        }
    }
}

/// A symbol hint is an exact name, or a dotted path whose last segments
/// must match the declaration path (`AuthService.refresh_token`). A dotted
/// hint also matches an entity named exactly that (a module). Returns the
/// limit that cut the matches, if one did.
fn symbols(
    view: &impl ReadView,
    hint: &str,
    limit: usize,
) -> Result<(Vec<EntityId>, Option<usize>), StoreError> {
    let parts: Vec<&str> = hint.split('.').collect();
    let last = parts.last().copied().unwrap_or_default();
    let named = view.named(last, MAX_REQUEST_ITEMS)?;
    // ponytail: a name with more than MAX_REQUEST_ITEMS declarations is
    // filtered from its first ones only; reported, not resolved.
    let mut cut = named.truncated.then_some(MAX_REQUEST_ITEMS);
    let mut found: Vec<EntityId> = named
        .entities
        .into_iter()
        .filter(|id| match id {
            EntityId::Symbol(symbol) => {
                let path = symbol.path();
                path.len() >= parts.len()
                    && path[path.len() - parts.len()..]
                        .iter()
                        .zip(&parts)
                        .all(|(segment, part)| segment.name == *part)
            }
            _ => parts.len() == 1,
        })
        .collect();
    if parts.len() > 1 {
        let modules = view.named(hint, MAX_REQUEST_ITEMS)?;
        if modules.truncated {
            cut = Some(MAX_REQUEST_ITEMS);
        }
        found.extend(modules.entities);
    }
    found.sort();
    found.dedup();
    if found.len() > limit {
        cut = Some(limit);
    }
    found.truncate(limit);
    Ok((found, cut))
}
