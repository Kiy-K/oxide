//! KnowledgeStore contract suite (SPEC § Storage abstraction). Every case is
//! generic over the store; `contract_suite!` instantiates it per
//! implementation, so the Phase 2 LadybugDB adapter runs the same cases.

mod common;

use common::*;
use oxide_kernel::id::{DerivationId, EntityId};
use oxide_kernel::knowledge::{Basis, Batch, Containment, Entity, RelationKind, Target};
use oxide_kernel::store::{
    AdjacencyRequest, Direction, KnowledgeStore, MAX_REQUEST_ITEMS, ReadView, StoreError,
};

macro_rules! contract_suite {
    ($store:ident: $make:expr; $($case:ident),* $(,)?) => {
        mod $store {
            $( #[test] fn $case() { super::$case($make) } )*
        }
    };
}

contract_suite!(memory: oxide_kernel::store::MemoryStore::default();
    unpublished_generation_is_invisible,
    failed_publication_stays_invisible,
    derivations_are_never_mixed,
    lookup_is_scoped_ordered_and_explicit_about_missing,
    adjacency_is_typed_bounded_and_stable,
    views_stay_pinned_across_publication,
    published_generations_are_immutable,
    publication_is_independent_of_write_order,
);

fn unpublished_generation_is_invisible(mut store: impl KnowledgeStore) {
    let k = key("s1", "d1");
    store.begin(manifest(k.clone())).unwrap();
    store.write(&k, batch()).unwrap();
    assert_eq!(store.current(&k.repo), Err(StoreError::MissingSnapshot));
    assert_eq!(store.open(&k).err(), Some(StoreError::MissingSnapshot));

    store.publish(&k).unwrap();
    assert_eq!(store.current(&k.repo), Ok(k.clone()));
    assert_eq!(store.open(&k).unwrap().snapshot().key, k);
}

fn failed_publication_stays_invisible(mut store: impl KnowledgeStore) {
    let good = key("s1", "d1");
    publish(&mut store, &good, batch());

    // (case, expected rejection reason, corruption)
    type Case = (&'static str, &'static str, fn(&mut Batch));
    let corruptions: Vec<Case> = vec![
        ("dangling target", "dangling target", |b| {
            let nope = sym("src/a.rs", &[("nope", 0)]);
            let edge = rel(
                RelationKind::Calls,
                g(),
                Target::Resolved(nope),
                Basis::Resolved,
            );
            b.relations.push(edge);
        }),
        ("orphan entity", "exactly 1 physical parent", |b| {
            let orphan = sym("src/a.rs", &[("orphan", 0)]);
            b.entities.push(entity(orphan, "fn", "orphan", None));
        }),
        ("second physical parent", "exactly 1 physical parent", |b| {
            b.relations.push(contains(file("src/b.rs"), g()));
        }),
        ("containment cycle", "physically contained by", |b| {
            b.relations
                .retain(|r| *r != contains(file("src/a.rs"), c()));
            b.relations.push(contains(f0(), c()));
        }),
        (
            "nesting that skips a declaration",
            "physically contained by",
            |b| {
                b.relations.retain(|r| *r != contains(c(), f0()));
                b.relations.push(contains(file("src/a.rs"), f0()));
            },
        ),
        ("source in another file", "source in another file", |b| {
            let g = b.entities.iter_mut().find(|e| e.id == g()).unwrap();
            let source = g.source.as_mut().unwrap();
            (source.file, source.digest) = (path("src/b.rs"), digest("src/b.rs"));
        }),
        ("file outside manifest", "outside the manifest", |b| {
            b.entities.push(entity(file("x.rs"), "file", "x.rs", None));
            b.relations
                .push(contains(EntityId::Repository, file("x.rs")));
        }),
        ("manifest file without entity", "has no entity", |b| {
            let gone = [file("tests/t.rs"), test_h()];
            b.entities.retain(|e| !gone.contains(&e.id));
            b.relations.retain(|r| {
                !gone.contains(&r.from) && !r.to.entities().iter().any(|t| gone.contains(t))
            });
        }),
        (
            "source digest from another snapshot",
            "source evidence",
            |b| {
                let f = b.entities.iter_mut().find(|e| e.id == f0()).unwrap();
                f.source.as_mut().unwrap().digest = digest("stale");
            },
        ),
        ("unsorted ambiguous target", "sorted ids", |b| {
            let to = Target::Ambiguous(vec![f1(), f0()]);
            b.relations
                .push(rel(RelationKind::References, h(), to, Basis::Syntactic));
        }),
        ("unresolved physical child", "resolved child", |b| {
            let to = Target::Unresolved { name: "x".into() };
            let kind = RelationKind::Contains(Containment::Physical);
            b.relations.push(rel(kind, g(), to, Basis::Syntactic));
        }),
    ];
    for (i, (name, reason, corrupt)) in corruptions.into_iter().enumerate() {
        let bad = key(&format!("bad{i}"), "d1");
        let mut b = batch();
        corrupt(&mut b);
        store.begin(manifest(bad.clone())).unwrap();
        store.write(&bad, b).unwrap();
        match store.publish(&bad) {
            Err(StoreError::InvalidBatch(why)) => assert!(why.contains(reason), "{name}: {why}"),
            other => panic!("{name}: {other:?}"),
        }
        assert_eq!(store.current(&bad.repo), Ok(good.clone()), "{name}");
        assert_eq!(
            store.open(&bad).err(),
            Some(StoreError::MissingSnapshot),
            "{name}"
        );
        store.discard(&bad).unwrap();
        assert_eq!(store.publish(&bad), Err(StoreError::NotStaged), "{name}");
    }

    // A rejected write leaves nothing behind either.
    let dup = key("dup", "d1");
    store.begin(manifest(dup.clone())).unwrap();
    let mut b = batch();
    b.entities.push(entity(g(), "function", "g-again", None));
    assert!(matches!(
        store.write(&dup, b),
        Err(StoreError::InvalidBatch(_))
    ));
    store.write(&dup, batch()).unwrap();
    store.publish(&dup).unwrap();
}

fn derivations_are_never_mixed(mut store: impl KnowledgeStore) {
    let d1 = key("s1", "d1");
    publish(&mut store, &d1, batch());
    assert_eq!(
        store.open(&key("s1", "d2")).err(),
        Some(StoreError::IncompatibleDerivation {
            available: vec![DerivationId::new("d1").unwrap()]
        })
    );
    assert_eq!(
        store.open(&key("s2", "d1")).err(),
        Some(StoreError::MissingSnapshot)
    );

    let d2 = key("s1", "d2");
    let mut b = batch();
    b.entities.iter_mut().find(|e| e.id == g()).unwrap().name = "g-v2".into();
    publish(&mut store, &d2, b);
    let name = |k| {
        let found = store.open(k).unwrap().entities(&[g()]).unwrap();
        found[0].clone().unwrap().name
    };
    assert_eq!(name(&d1), "g");
    assert_eq!(name(&d2), "g-v2");
    assert_eq!(store.current(&d1.repo), Ok(d2));
}

fn lookup_is_scoped_ordered_and_explicit_about_missing(mut store: impl KnowledgeStore) {
    let k = key("s1", "d1");
    publish(&mut store, &k, batch());
    let view = store.open(&k).unwrap();

    let missing = sym("src/a.rs", &[("absent", 0)]);
    let found = view.entities(&[f1(), missing, f0()]).unwrap();
    assert_eq!(found[0].as_ref().unwrap().id, f1());
    assert_eq!(found[1], None);
    assert_eq!(found[2].as_ref().unwrap().id, f0());

    // Overloads and same-line nesting keep distinct identity and byte ranges.
    let start = |e: &Option<Entity>| e.as_ref().unwrap().source.as_ref().unwrap().range.start;
    assert_eq!((start(&found[2]), start(&found[0])), (11, 21));

    let too_many = vec![g(); MAX_REQUEST_ITEMS + 1];
    assert_eq!(
        view.entities(&too_many),
        Err(StoreError::LimitExceeded {
            limit: MAX_REQUEST_ITEMS
        })
    );
}

fn adjacency_is_typed_bounded_and_stable(mut store: impl KnowledgeStore) {
    let k = key("s1", "d1");
    publish(&mut store, &k, batch());
    let view = store.open(&k).unwrap();
    let ask = |entity, direction, kinds: &[RelationKind], limit| {
        view.adjacency(&AdjacencyRequest {
            entity,
            direction,
            kinds: kinds.to_vec(),
            limit,
        })
    };
    let physical = [RelationKind::Contains(Containment::Physical)];

    let cut = ask(c(), Direction::Outgoing, &physical, 1).unwrap();
    assert_eq!(
        (cut.edges, cut.truncated),
        (vec![contains(c(), f0())], true)
    );
    let all = ask(c(), Direction::Outgoing, &physical, 10).unwrap();
    assert_eq!(
        (all.edges, all.truncated),
        (vec![contains(c(), f0()), contains(c(), f1())], false)
    );

    // Cycles come back as plain typed edges in stable order.
    let calls = ask(g(), Direction::Both, &[RelationKind::Calls], 10).unwrap();
    let ends: Vec<_> = calls.edges.iter().map(|r| (&r.from, &r.to)).collect();
    assert_eq!(
        ends,
        [
            (&g(), &Target::Resolved(h())),
            (&g(), &Target::Ambiguous(vec![f0(), f1()])),
            (&h(), &Target::Resolved(g())),
        ]
    );

    // Ambiguous targets are visible from each candidate; unresolved ones are kept.
    let incoming = ask(f1(), Direction::Incoming, &[RelationKind::Calls], 10).unwrap();
    assert_eq!(incoming.edges.len(), 1);
    let refs = ask(g(), Direction::Outgoing, &[RelationKind::References], 10).unwrap();
    let unresolved = Target::Unresolved {
        name: "missing".into(),
    };
    assert_eq!(refs.edges[0].to, unresolved);

    assert!(ask(g(), Direction::Both, &[], 10).unwrap().edges.is_empty());
    let absent = sym("src/a.rs", &[("absent", 0)]);
    assert_eq!(
        ask(absent.clone(), Direction::Both, &physical, 10),
        Err(StoreError::MissingEntity(absent))
    );
    assert_eq!(
        ask(g(), Direction::Both, &physical, MAX_REQUEST_ITEMS + 1),
        Err(StoreError::LimitExceeded {
            limit: MAX_REQUEST_ITEMS
        })
    );
}

fn views_stay_pinned_across_publication(mut store: impl KnowledgeStore) {
    let old = key("s1", "d1");
    publish(&mut store, &old, batch());
    let view = store.open(&old).unwrap();

    let new = key("s2", "d1");
    let mut b = batch();
    b.relations.retain(|r| r.kind != RelationKind::Calls);
    publish(&mut store, &new, b);

    let calls = AdjacencyRequest {
        entity: g(),
        direction: Direction::Both,
        kinds: vec![RelationKind::Calls],
        limit: 10,
    };
    assert_eq!(view.adjacency(&calls).unwrap().edges.len(), 3);
    assert_eq!(view.snapshot().key, old);
    let fresh = store.open(&new).unwrap();
    assert!(fresh.adjacency(&calls).unwrap().edges.is_empty());
}

fn published_generations_are_immutable(mut store: impl KnowledgeStore) {
    let k = key("s1", "d1");
    publish(&mut store, &k, batch());
    assert_eq!(store.begin(manifest(k.clone())), Err(StoreError::Conflict));
    assert_eq!(
        store.write(&k, Batch::default()),
        Err(StoreError::NotStaged)
    );
    assert_eq!(store.discard(&k), Err(StoreError::NotStaged));
}

fn publication_is_independent_of_write_order(mut store: impl KnowledgeStore) {
    let forward = key("forward", "d1");
    publish(&mut store, &forward, batch());

    // Same facts, reversed and split across two batches.
    let reversed = key("reversed", "d1");
    let mut b = batch();
    b.entities.reverse();
    b.relations.reverse();
    let tail = Batch {
        entities: b.entities.split_off(5),
        relations: b.relations.split_off(7),
    };
    store.begin(manifest(reversed.clone())).unwrap();
    store.write(&reversed, b).unwrap();
    store.write(&reversed, tail).unwrap();
    store.publish(&reversed).unwrap();

    let dump = |k| {
        let view = store.open(k).unwrap();
        let ids: Vec<EntityId> = batch().entities.into_iter().map(|e| e.id).collect();
        let entities = view.entities(&ids).unwrap();
        let edges: Vec<_> = ids
            .into_iter()
            .map(|entity| {
                let request = AdjacencyRequest {
                    entity,
                    direction: Direction::Both,
                    kinds: RelationKind::ALL.to_vec(),
                    limit: 100,
                };
                view.adjacency(&request).unwrap()
            })
            .collect();
        (entities, edges)
    };
    assert_eq!(dump(&forward), dump(&reversed));
}
