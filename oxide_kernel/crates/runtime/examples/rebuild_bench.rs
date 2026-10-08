//! Measurement aid for docs/phase-3.md, not a test. Release build:
//!
//! `cargo run --release --example rebuild_bench -- <repo> <empty-store-dir> [cycles]`
//!
//! Stage timings for one full rebuild into an empty store and a reopen,
//! then cold/warm read, retrieval and routing timings on the reopened
//! generation. With `cycles`, republishes the same facts under that many
//! new snapshot keys in one process and prints resident memory after each,
//! the long-lived-runtime generation leak check.

use std::path::Path;
use std::time::{Duration, Instant};

use oxide_kernel::decision::{Allowance, DecisionPolicy};
use oxide_kernel::id::{EntityId, RepoId, SnapshotId};
use oxide_kernel::query::{Query, QueryContext};
use oxide_kernel::retrieve::{BASELINE, ChannelState, retrieve};
use oxide_kernel::route::{BASELINE_LIMITS, RouteRequest, baseline_policy, route};
use oxide_kernel::store::{KnowledgeStore, ReadView};
use oxide_kernel::tree::{NavLimits, RegionId, region};
use oxide_runtime::capture::{Scope, capture};
use oxide_runtime::derivation::derive;
use oxide_runtime::storage::LadybugStore;

fn rss_mib() -> f64 {
    let status = std::fs::read_to_string("/proc/self/status").unwrap();
    let line = status.lines().find(|l| l.starts_with("VmRSS:")).unwrap();
    line.split_whitespace()
        .nth(1)
        .unwrap()
        .parse::<f64>()
        .unwrap()
        / 1024.0
}

/// First call, then the median of 20 more.
fn timed<T>(mut f: impl FnMut() -> T) -> (Duration, Duration, T) {
    let t = Instant::now();
    let out = f();
    let cold = t.elapsed();
    let mut warm: Vec<Duration> = (0..20)
        .map(|_| {
            let t = Instant::now();
            f();
            t.elapsed()
        })
        .collect();
    warm.sort();
    (cold, warm[10], out)
}

fn ms(d: Duration) -> String {
    format!("{:.2}ms", d.as_secs_f64() * 1e3)
}

fn main() {
    let args: Vec<String> = std::env::args().collect();
    let (repo, store_dir) = (Path::new(&args[1]), Path::new(&args[2]));
    let cycles: usize = args.get(3).map_or(0, |c| c.parse().unwrap());
    let repo_id = RepoId::new("bench").unwrap();

    let t = Instant::now();
    let source = capture(repo, &Scope::default()).unwrap();
    let (manifest, batch, components) = derive(&repo_id, &source).unwrap();
    let derive_s = t.elapsed().as_secs_f64();
    let (entities, relations) = (batch.entities.len(), batch.relations.len());
    let key = manifest.key.clone();

    let t = Instant::now();
    let mut store = LadybugStore::new(store_dir).unwrap();
    store.begin(manifest.clone()).unwrap();
    store.retain_derivation(&key, &components).unwrap();
    store.retain_source(&source).unwrap();
    let begin_s = t.elapsed().as_secs_f64();
    let t = Instant::now();
    store.write(&key, batch.clone()).unwrap();
    let write_s = t.elapsed().as_secs_f64();
    let t = Instant::now();
    store.publish(&key).unwrap();
    let publish_s = t.elapsed().as_secs_f64();
    drop(store);
    println!(
        "files={} entities={entities} relations={relations} capture+derive={derive_s:.3}s \
         begin={begin_s:.3}s write={write_s:.3}s publish={publish_s:.3}s",
        source.files.len()
    );

    let t = Instant::now();
    let mut store = LadybugStore::new(store_dir).unwrap();
    let view = store.open(&key).unwrap();
    println!("reopen={} rss={:.0}MiB", ms(t.elapsed()), rss_mib());

    let file = view.snapshot().files.keys().next().unwrap().clone();
    let (c, w, _) = timed(|| view.entities(&[EntityId::File(file.clone())]).unwrap());
    println!("entities(1): cold={} warm={}", ms(c), ms(w));
    let nav = NavLimits {
        children: 16,
        cross_edges: 16,
    };
    let (c, w, _) =
        timed(|| region(&view, &RegionId::of(EntityId::File(file.clone())), nav).unwrap());
    println!("region(file): cold={} warm={}", ms(c), ms(w));

    let query = Query {
        text: "run the event loop until the future completes".into(),
    };
    let context = QueryContext::default();
    let (c, w, set) = timed(|| {
        retrieve(
            &view,
            &query,
            &context,
            ChannelState::Unconfigured,
            BASELINE,
        )
        .unwrap()
    });
    println!(
        "retrieve(lexical+structural): cold={} warm={} entries={}",
        ms(c),
        ms(w),
        set.entries.len()
    );
    let request = RouteRequest {
        snapshot: key.clone(),
        query: query.clone(),
        entry_points: set.entries.clone(),
        limits: BASELINE_LIMITS,
        policy: baseline_policy(),
    };
    let (c, w, routed) = timed(|| {
        let mut allowance = Allowance { remaining: 0 };
        route(
            &view,
            &request,
            None,
            &mut allowance,
            DecisionPolicy::default(),
        )
        .unwrap()
    });
    let work = routed.trace.work;
    println!(
        "route(baseline): cold={} warm={} candidates={} regions={} edges={} stopped_by={:?} rss={:.0}MiB",
        ms(c),
        ms(w),
        routed.candidates.len(),
        work.regions_loaded,
        work.edges_examined,
        routed.trace.stopped_by,
        rss_mib()
    );
    drop(view);

    for i in 0..cycles {
        let mut m = manifest.clone();
        m.key.snapshot = SnapshotId::new(format!("cycle-{i}")).unwrap();
        let k = m.key.clone();
        store.begin(m).unwrap();
        store.retain_derivation(&k, &components).unwrap();
        store.write(&k, batch.clone()).unwrap();
        store.publish(&k).unwrap();
        // A reader that comes and goes, as a request would.
        drop(
            store
                .open(&k)
                .unwrap()
                .entities(&[EntityId::Repository])
                .unwrap(),
        );
        let dirs = std::fs::read_dir(store_dir.join("generations"))
            .unwrap()
            .count();
        println!("cycle {i}: rss={:.0}MiB generation_dirs={dirs}", rss_mib());
    }
}
