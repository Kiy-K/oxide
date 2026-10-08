//! Phase 2B end to end: capture → Python derivation → generation publication
//! → close → reopen, through the kernel's domain contracts only.

use std::collections::BTreeSet;
use std::fs;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};

use oxide_kernel::id::*;
use oxide_kernel::knowledge::*;
use oxide_kernel::source::Skip;
use oxide_kernel::store::{
    AdjacencyRequest, Direction, KnowledgeStore, MAX_REQUEST_ITEMS, MemoryStore, ReadView,
};
use oxide_kernel::tree::{NavLimits, RegionId, region};
use oxide_runtime::capture::{Scope, capture};
use oxide_runtime::derivation::derive;
use oxide_runtime::repository::{DataLocation, Outcome, RepositorySession};

fn temp(name: &str) -> PathBuf {
    static NEXT: AtomicU64 = AtomicU64::new(0);
    let path = std::env::temp_dir().join(format!(
        "oxide-ingest-{}-{}-{name}",
        std::process::id(),
        NEXT.fetch_add(1, Ordering::Relaxed)
    ));
    let _ = fs::remove_dir_all(&path);
    fs::create_dir_all(&path).unwrap();
    path
}

fn copy_tree(from: &Path, to: &Path) {
    fs::create_dir_all(to).unwrap();
    for entry in fs::read_dir(from).unwrap() {
        let entry = entry.unwrap();
        let target = to.join(entry.file_name());
        if entry.file_type().unwrap().is_dir() {
            copy_tree(&entry.path(), &target);
        } else {
            fs::copy(entry.path(), target).unwrap();
        }
    }
}

/// A copy of the retained `fixtures/py_repo`, plus a gitignored cache file.
fn fixture(base: &Path) -> PathBuf {
    let root = base.join("repo");
    let source = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../../fixtures/py_repo");
    copy_tree(&source, &root);
    write(&root, "oxidepy/__pycache__/auth.cpython-311.pyc", "\0");
    root
}

fn write(root: &Path, rel: &str, text: &str) {
    let path = root.join(rel);
    fs::create_dir_all(path.parent().unwrap()).unwrap();
    fs::write(path, text).unwrap();
}

fn file(path: &str) -> EntityId {
    EntityId::File(RepoPath::new(path).unwrap())
}

fn sym(file: &str, path: &[(&str, u32)]) -> EntityId {
    let segments = path
        .iter()
        .map(|(name, ordinal)| Segment {
            name: (*name).into(),
            ordinal: *ordinal,
        })
        .collect();
    EntityId::Symbol(SymbolId::new(RepoPath::new(file).unwrap(), segments).unwrap())
}

fn module(name: &str) -> EntityId {
    EntityId::Module(ModuleId::new(format!("python:{name}")).unwrap())
}

fn unresolved(name: &str) -> Target {
    Target::Unresolved { name: name.into() }
}

type Knowledge = (Vec<Entity>, BTreeSet<Relation>);

/// Every entity reachable through the TreeIndex hierarchy, with all its
/// outgoing typed edges: the domain-visible knowledge of one generation.
fn knowledge(view: &impl ReadView) -> Knowledge {
    let limits = NavLimits {
        children: MAX_REQUEST_ITEMS,
        cross_edges: MAX_REQUEST_ITEMS,
    };
    let mut entities = Vec::new();
    let mut relations = BTreeSet::new();
    let mut queue = vec![RegionId::root()];
    while let Some(id) = queue.pop() {
        let region = region(view, &id, limits).unwrap();
        assert!(!region.children_truncated && !region.cross_edges_truncated);
        let edges = view
            .adjacency(&AdjacencyRequest {
                entity: id.anchor.clone(),
                direction: Direction::Outgoing,
                kinds: RelationKind::ALL.to_vec(),
                limit: MAX_REQUEST_ITEMS,
            })
            .unwrap();
        assert!(!edges.truncated);
        relations.extend(edges.edges);
        entities.push(region.anchor);
        queue.extend(region.children);
    }
    entities.sort_by(|a, b| a.id.cmp(&b.id));
    (entities, relations)
}

fn edges(knowledge: &Knowledge, kind: RelationKind, from: &EntityId) -> Vec<Target> {
    knowledge
        .1
        .iter()
        .filter(|r| r.kind == kind && r.from == *from)
        .map(|r| r.to.clone())
        .collect()
}

fn entity<'a>(knowledge: &'a Knowledge, id: &EntityId) -> &'a Entity {
    knowledge
        .0
        .iter()
        .find(|e| e.id == *id)
        .unwrap_or_else(|| panic!("missing {id:?}"))
}

#[test]
fn fixture_capture_ingest_publish_reopen() {
    let base = temp("fixture");
    let root = fixture(&base);
    let location = DataLocation(base.join("data"));

    let mut session = RepositorySession::open(&root, &location).unwrap();
    let report = session.rebuild(&Scope::default()).unwrap();
    assert_eq!(report.outcome, Outcome::Published);
    assert!(report.skipped.is_empty());
    let view = session.store.open(&report.key).unwrap();
    let published = knowledge(&view);

    // The same capture and derivation through the fake store is the same
    // domain knowledge (fake/real parity on real ingested data).
    let mut memory = MemoryStore::default();
    let capture = capture(&root, &Scope::default()).unwrap();
    let (manifest, batch, _) = derive(&session.repo_id, &capture).unwrap();
    assert_eq!(manifest.key, report.key);
    memory.begin(manifest.clone()).unwrap();
    memory.write(&report.key, batch).unwrap();
    memory.publish(&report.key).unwrap();
    assert_eq!(knowledge(&memory.open(&report.key).unwrap()), published);
    assert_eq!(*view.snapshot(), manifest);

    // Scope and coverage.
    let files = &view.snapshot().files;
    assert!(!files.keys().any(|p| p.as_str().contains("__pycache__")));
    let readme = &files[&RepoPath::new("README.md").unwrap()];
    assert_eq!(readme.language, "unknown");
    assert!(matches!(readme.coverage, Coverage::Unsupported { .. }));
    let auth_file = &files[&RepoPath::new("oxidepy/auth.py").unwrap()];
    assert_eq!(
        (auth_file.language.as_str(), &auth_file.coverage),
        ("python", &Coverage::Complete)
    );

    // Symbols, containment, test facet, hydration.
    let auth = "oxidepy/auth.py";
    let service = sym(auth, &[("AuthService", 0)]);
    let refresh = sym(auth, &[("AuthService", 0), ("refresh_token", 0)]);
    assert_eq!(entity(&published, &refresh).kind, "method");
    assert_eq!(
        entity(&published, &refresh).signature.as_deref(),
        Some("def refresh_token(self, session_id: str) -> str")
    );
    assert!(
        edges(
            &published,
            RelationKind::Contains(Containment::Physical),
            &service
        )
        .contains(&Target::Resolved(refresh.clone()))
    );
    let source = entity(&published, &service).source.clone().unwrap();
    assert!(
        view.hydrate(&source)
            .unwrap()
            .starts_with(b"class AuthService:")
    );
    let tests = "tests/test_auth.py";
    let test = sym(tests, &[("test_refresh_token_stores_new_token", 0)]);
    assert!(entity(&published, &test).test);
    assert!(!entity(&published, &sym(tests, &[("FakeClient", 0)])).test);
    assert!(
        edges(
            &published,
            RelationKind::Contains(Containment::Logical),
            &module("oxidepy.auth")
        )
        .contains(&Target::Resolved(file(auth)))
    );
    assert!(
        edges(&published, RelationKind::Defines, &module("oxidepy.auth"))
            .contains(&Target::Resolved(service.clone()))
    );

    // Imports: repository modules resolve, everything else stays unresolved.
    let auth_imports = edges(&published, RelationKind::Imports, &file(auth));
    let client = sym("oxidepy/http_client.py", &[("HttpClient", 0)]);
    assert!(auth_imports.contains(&Target::Resolved(client.clone())));
    assert!(auth_imports.contains(&unresolved("json")));

    // Calls: plain names resolve through Python scopes, attributes do not.
    let calls = edges(&published, RelationKind::Calls, &refresh);
    assert!(calls.contains(&Target::Resolved(sym(auth, &[("TokenError", 0)]))));
    assert!(calls.contains(&unresolved("self.store.load")));
    let token_store = sym(auth, &[("TokenStore", 0)]);
    assert!(
        edges(&published, RelationKind::Calls, &test)
            .contains(&Target::Resolved(token_store.clone()))
    );
    assert!(
        edges(&published, RelationKind::TestedBy, &token_store).contains(&Target::Resolved(test))
    );
    assert!(
        published
            .1
            .iter()
            .all(|r| r.kind != RelationKind::TestedBy || r.basis == Basis::Heuristic)
    );
    assert_eq!(
        edges(
            &published,
            RelationKind::References,
            &sym(auth, &[("TokenError", 0)])
        ),
        [unresolved("RuntimeError")]
    );
    assert_eq!(
        edges(
            &published,
            RelationKind::References,
            &sym(tests, &[("FakeClient", 0)])
        ),
        [Target::Resolved(client)]
    );

    // Close, reopen: same identity, same published knowledge.
    let key = report.key.clone();
    drop(view);
    drop(session);
    let mut session = RepositorySession::open(&root, &location).unwrap();
    assert_eq!(session.repo_id, key.repo);
    assert_eq!(session.store.current(&key.repo).unwrap(), key);
    assert_eq!(knowledge(&session.store.open(&key).unwrap()), published);
    let again = session.rebuild(&Scope::default()).unwrap();
    assert_eq!((again.outcome, again.key), (Outcome::Unchanged, key));
    drop(session);
    fs::remove_dir_all(base).unwrap();
}

#[test]
fn changed_and_deleted_files_rebuild_fresh_and_history_stays_exact() {
    let base = temp("rebuild");
    let root = fixture(&base);
    let location = DataLocation(base.join("data"));
    let mut session = RepositorySession::open(&root, &location).unwrap();
    let first = session.rebuild(&Scope::default()).unwrap().key;
    let old = session.store.open(&first).unwrap();
    let decode = sym("oxidepy/auth.py", &[("decode_claims", 0)]);
    let old_source = entity(&knowledge(&old), &decode).source.clone().unwrap();
    let old_bytes = old.hydrate(&old_source).unwrap();

    let auth = fs::read_to_string(root.join("oxidepy/auth.py")).unwrap();
    let cache = fs::read(root.join("oxidepy/cache.py")).unwrap();
    write(
        &root,
        "oxidepy/auth.py",
        &auth.replace("decode_claims", "parse_claims"),
    );
    fs::remove_file(root.join("oxidepy/cache.py")).unwrap();
    let report = session.rebuild(&Scope::default()).unwrap();
    assert_eq!(report.outcome, Outcome::Published);
    assert_ne!(report.key, first);
    assert_eq!(session.store.current(&first.repo).unwrap(), report.key);

    let new = knowledge(&session.store.open(&report.key).unwrap());
    let ids: BTreeSet<_> = new.0.iter().map(|e| e.id.clone()).collect();
    assert!(!ids.contains(&decode));
    assert!(ids.contains(&sym("oxidepy/auth.py", &[("parse_claims", 0)])));
    assert!(!ids.contains(&file("oxidepy/cache.py")));
    assert!(!ids.contains(&module("oxidepy.cache")));
    assert!(
        edges(&new, RelationKind::Imports, &file("oxidepy/__init__.py"))
            .contains(&unresolved(".cache.TTLCache"))
    );
    // The pinned old view still reads the old generation and its captured
    // bytes, never the changed worktree.
    assert!(knowledge(&old).0.iter().any(|e| e.id == decode));
    assert_eq!(old.hydrate(&old_source).unwrap(), old_bytes);
    assert!(old_bytes.starts_with(b"def decode_claims"));

    // Restoring the source reproduces the first snapshot: re-pointed, not
    // rebuilt.
    write(&root, "oxidepy/auth.py", &auth);
    fs::write(root.join("oxidepy/cache.py"), cache).unwrap();
    let restored = session.rebuild(&Scope::default()).unwrap();
    assert_eq!(
        (restored.outcome, &restored.key),
        (Outcome::Reactivated, &first)
    );
    drop(old);
    let store_dir = session.store_directory.clone();
    drop(session);
    let session = RepositorySession::open(&root, &location).unwrap();
    assert_eq!(session.store.current(&first.repo).unwrap(), first);
    // Only the current generation survives a restart: no view can be pinned
    // before startup.
    assert_eq!(
        fs::read_dir(store_dir.join("generations")).unwrap().count(),
        1
    );
    drop(session);
    fs::remove_dir_all(base).unwrap();
}

#[test]
fn python_identity_and_conservative_resolution() {
    let base = temp("resolution");
    let root = base.join("repo");
    write(&root, "pkg/__init__.py", "from .mod import C\n");
    write(
        &root,
        "pkg/mod.py",
        r#"import os
from . import helpers
from .missing import thing


def f():
    return 1


def f():
    return 2


class C:
    def f(self):
        return f()

    def g(self):
        def inner():
            return 0
        return inner()

    def h(self):
        def inner():
            return 1
        return inner()


def uses_dup():
    return f()


def shadowed(f):
    return f()


def assigned():
    g = make
    return g()


def make():
    return C()


def builtin():
    return print("x")


def tick():
    return 1


def bump():
    global tick
    tick = None


def run():
    return tick()


@decorator
def decorated():
    pass
"#,
    );
    write(
        &root,
        "pkg/star.py",
        "from .mod import *\n\n\ndef local():\n    return 1\n\n\ndef caller():\n    return local()\n",
    );
    write(
        &root,
        "app.py",
        "from pkg.mod import make, f\nfrom pkg import mod\nimport pkg.mod as m\n\n\ndef main():\n    make()\n    f()\n    m.make()\n",
    );
    let mut session = RepositorySession::open(&root, &DataLocation(base.join("data"))).unwrap();
    let key = session.rebuild(&Scope::default()).unwrap().key;
    let view = session.store.open(&key).unwrap();
    let k = knowledge(&view);
    let m = "pkg/mod.py";
    let f = |ordinal| sym(m, &[("f", ordinal)]);
    let both_f = Target::Ambiguous(vec![f(0), f(1)]);

    // Duplicate and nested declarations: ordinals and declaration paths.
    let ids: BTreeSet<_> = k.0.iter().map(|e| e.id.clone()).collect();
    for id in [
        f(0),
        f(1),
        sym(m, &[("C", 0), ("f", 0)]),
        sym(m, &[("C", 0), ("g", 0), ("inner", 0)]),
        sym(m, &[("C", 0), ("h", 0), ("inner", 0)]),
    ] {
        assert!(ids.contains(&id), "{id:?}");
    }
    assert!(!ids.contains(&sym(m, &[("f", 2)])));
    let decorated = entity(&k, &sym(m, &[("decorated", 0)]));
    let source = view.hydrate(decorated.source.as_ref().unwrap()).unwrap();
    assert!(source.starts_with(b"@decorator\ndef decorated():"));

    let calls = |path: &[(&str, u32)]| edges(&k, RelationKind::Calls, &sym(m, path));
    // A method body skips its class scope: `f` is the module's two `f`s.
    assert_eq!(calls(&[("C", 0), ("f", 0)]), std::slice::from_ref(&both_f));
    assert_eq!(calls(&[("uses_dup", 0)]), std::slice::from_ref(&both_f));
    assert_eq!(
        calls(&[("C", 0), ("g", 0)]),
        [Target::Resolved(sym(
            m,
            &[("C", 0), ("g", 0), ("inner", 0)]
        ))]
    );
    assert_eq!(
        calls(&[("make", 0)]),
        [Target::Resolved(sym(m, &[("C", 0)]))]
    );
    // Shadowing, dynamic rebinding and builtins never resolve.
    assert_eq!(calls(&[("shadowed", 0)]), [unresolved("f")]);
    assert_eq!(calls(&[("assigned", 0)]), [unresolved("g")]);
    assert_eq!(calls(&[("builtin", 0)]), [unresolved("print")]);
    assert_eq!(calls(&[("run", 0)]), [unresolved("tick")]);
    assert_eq!(
        edges(
            &k,
            RelationKind::Calls,
            &sym("pkg/star.py", &[("caller", 0)])
        ),
        [unresolved("local")]
    );

    let imports = edges(&k, RelationKind::Imports, &file(m));
    for target in [
        unresolved("os"),
        unresolved(".helpers"),
        unresolved(".missing.thing"),
    ] {
        assert!(imports.contains(&target), "{target:?}");
    }
    let app_imports = edges(&k, RelationKind::Imports, &file("app.py"));
    for target in [
        Target::Resolved(sym(m, &[("make", 0)])),
        both_f.clone(),
        Target::Resolved(module("pkg.mod")),
    ] {
        assert!(app_imports.contains(&target), "{target:?}");
    }
    let main = edges(&k, RelationKind::Calls, &sym("app.py", &[("main", 0)]));
    assert_eq!(
        main.into_iter().collect::<BTreeSet<_>>(),
        BTreeSet::from([
            Target::Resolved(sym(m, &[("make", 0)])),
            both_f,
            unresolved("m.make"),
        ])
    );
    // Every resolved or ambiguous endpoint is an entity of this generation.
    assert!(
        k.1.iter()
            .flat_map(|r| r.to.entities().to_vec())
            .all(|id| ids.contains(&id))
    );
    drop(view);
    drop(session);
    fs::remove_dir_all(base).unwrap();
}

#[test]
fn malformed_unsupported_unreadable_and_ignored_sources() {
    use std::os::unix::fs::{PermissionsExt, symlink};
    let base = temp("malformed");
    let root = base.join("repo");
    write(&root, ".gitignore", "ignored.py\n");
    write(&root, "ignored.py", "def hidden():\n    pass\n");
    write(
        &root,
        "broken.py",
        "def ok():\n    pass\n\ndef broken(:\n    pass\n",
    );
    fs::write(root.join("latin.py"), b"x = '\xe9'\n").unwrap();
    write(&root, "notes.txt", "plain text\n");
    write(&root, "secret.py", "def s():\n    pass\n");
    fs::set_permissions(root.join("secret.py"), fs::Permissions::from_mode(0o000)).unwrap();
    symlink("broken.py", root.join("link.py")).unwrap();
    // Root ignores file permissions, so the unreadable case needs a normal user.
    let readable = fs::read(root.join("secret.py")).is_ok();

    let mut session = RepositorySession::open(&root, &DataLocation(base.join("data"))).unwrap();
    let report = session.rebuild(&Scope::default()).unwrap();
    assert_eq!(report.skipped.get("link.py"), Some(&Skip::Symlink));
    if !readable {
        assert!(matches!(
            report.skipped.get("secret.py"),
            Some(Skip::Unreadable(_))
        ));
    }
    assert!(!report.skipped.contains_key("ignored.py"));
    let view = session.store.open(&report.key).unwrap();
    let files = &view.snapshot().files;
    let manifest = |p: &str| &files[&RepoPath::new(p).unwrap()];
    assert!(!files.contains_key(&RepoPath::new("ignored.py").unwrap()));
    assert!(
        matches!(&manifest("broken.py").coverage, Coverage::Partial { diagnostics } if !diagnostics.is_empty())
    );
    assert_eq!(
        (
            manifest("latin.py").language.as_str(),
            &manifest("latin.py").coverage
        ),
        (
            "python",
            &Coverage::Unsupported {
                reason: "Python source is not UTF-8".into()
            }
        )
    );
    assert!(matches!(
        manifest("notes.txt").coverage,
        Coverage::Unsupported { .. }
    ));
    let k = knowledge(&view);
    // A partial file keeps what it parsed; unsupported files are still file
    // evidence, never empty successes.
    assert!(k.0.iter().any(|e| e.id == sym("broken.py", &[("ok", 0)])));
    for p in ["latin.py", "notes.txt", "broken.py", ".gitignore"] {
        assert!(k.0.iter().any(|e| e.id == file(p)), "{p}");
    }
    drop(view);
    drop(session);
    fs::set_permissions(root.join("secret.py"), fs::Permissions::from_mode(0o600)).unwrap();
    fs::remove_dir_all(base).unwrap();
}
