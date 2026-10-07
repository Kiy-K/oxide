//! LadybugDB feasibility evidence at lbug 0.21.2 (README.md). Each test pins
//! one observed behavior that an OXIDE architectural decision relies on. These
//! are spike tests: plain Cypher strings, no adapter, no kernel types.
//!
//! Child-process cases re-run this test binary with `SPIKE_CHILD` set; the
//! `child` test is a no-op otherwise.

use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::mpsc;
use std::time::{Duration, Instant};

use lbug::{Connection, Database, SystemConfig, Value};

/// Small, fixed resources: the default buffer pool is sized from host RAM.
fn config() -> SystemConfig {
    SystemConfig::default()
        .buffer_pool_size(64 << 20)
        .max_num_threads(2)
        .max_db_size(1 << 30)
}

fn open(path: &Path) -> Result<Database, lbug::Error> {
    Database::new(path, config())
}

fn tempdir(name: &str) -> PathBuf {
    let dir = std::env::temp_dir().join(format!("lbug-spike-{}-{name}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).unwrap();
    dir
}

fn rows(conn: &Connection, query: &str) -> Vec<Vec<Value>> {
    conn.query(query)
        .unwrap_or_else(|e| panic!("{query}: {e}"))
        .collect()
}

fn count(conn: &Connection, query: &str) -> i64 {
    match rows(conn, query).as_slice() {
        [row] => match row.as_slice() {
            [Value::Int64(n)] => *n,
            other => panic!("not a count: {other:?}"),
        },
        other => panic!("not one row: {other:?}"),
    }
}

fn strings(conn: &Connection, query: &str) -> Vec<String> {
    rows(conn, query)
        .into_iter()
        .map(|row| match row.as_slice() {
            [Value::String(s)] => s.clone(),
            other => panic!("not one string: {other:?}"),
        })
        .collect()
}

/// Representative physical mapping (ADR-0002): one Entity table keyed by an
/// injective encoding of the domain ID, one rel table per relation kind, and
/// unresolved references as their own nodes so no endpoint is invented.
const SCHEMA: &[&str] = &[
    "CREATE NODE TABLE Entity(key STRING PRIMARY KEY, category STRING, kind STRING, name STRING, file STRING, start_byte INT64, end_byte INT64, digest STRING, test BOOLEAN)",
    "CREATE NODE TABLE Unresolved(key STRING PRIMARY KEY, name STRING)",
    "CREATE REL TABLE CONTAINS(FROM Entity TO Entity, physical BOOLEAN)",
    "CREATE REL TABLE CALLS(FROM Entity TO Entity, FROM Entity TO Unresolved, basis STRING, resolution STRING, site INT64)",
];

fn load_fixture(conn: &Connection) {
    for ddl in SCHEMA {
        conn.query(ddl).unwrap();
    }
    let mut node = conn
        .prepare(
            "CREATE (:Entity {key: $key, category: $cat, kind: $kind, name: $name, file: $file, \
             start_byte: $s, end_byte: $e, digest: 'sha256:00', test: false})",
        )
        .unwrap();
    let entities: &[(&str, &str, &str, &str, i64, i64)] = &[
        ("r", "repository", "repository", "repo", 0, 0),
        ("f:src/a.rs", "file", "file", "a.rs", 0, 90),
        ("s:src/a.rs#f.0", "symbol", "function", "f", 0, 30),
        ("s:src/a.rs#f.1", "symbol", "function", "f", 31, 60),
        ("s:src/a.rs#g.0", "symbol", "function", "g", 61, 90),
    ];
    conn.query("BEGIN TRANSACTION").unwrap();
    for &(key, cat, kind, name, s, e) in entities {
        conn.execute(
            &mut node,
            vec![
                ("key", key.into()),
                ("cat", cat.into()),
                ("kind", kind.into()),
                ("name", name.into()),
                ("file", "src/a.rs".into()),
                ("s", s.into()),
                ("e", e.into()),
            ],
        )
        .unwrap();
    }
    for q in [
        "MATCH (a:Entity {key: 'r'}), (b:Entity {key: 'f:src/a.rs'}) CREATE (a)-[:CONTAINS {physical: true}]->(b)",
        "MATCH (a:Entity {key: 'f:src/a.rs'}), (b:Entity) WHERE b.category = 'symbol' CREATE (a)-[:CONTAINS {physical: true}]->(b)",
        // g calls f, which is ambiguous between two overloads: two edges sharing site 0.
        "MATCH (g:Entity {key: 's:src/a.rs#g.0'}), (f:Entity) WHERE f.name = 'f' CREATE (g)-[:CALLS {basis: 'resolved', resolution: 'ambiguous', site: 0}]->(f)",
        "CREATE (:Unresolved {key: 'u:s:src/a.rs#g.0:1', name: 'external_fn'})",
        "MATCH (g:Entity {key: 's:src/a.rs#g.0'}), (u:Unresolved) CREATE (g)-[:CALLS {basis: 'syntactic', resolution: 'unresolved', site: 1}]->(u)",
    ] {
        conn.query(q).unwrap();
    }
    conn.query("COMMIT").unwrap();
}

const CHILDREN_OF_FILE: &str = "MATCH (:Entity {key: 'f:src/a.rs'})-[:CONTAINS]->(c:Entity) \
     RETURN c.key ORDER BY c.key LIMIT 2";

#[test]
fn persist_reopen_bounded_adjacency_and_mutation() {
    let dir = tempdir("persist");
    let path = dir.join("db");
    {
        let db = open(&path).unwrap();
        let conn = Connection::new(&db).unwrap();
        load_fixture(&conn);
        // Bounded, stably ordered adjacency; the third child is cut by LIMIT.
        assert_eq!(
            strings(&conn, CHILDREN_OF_FILE),
            ["s:src/a.rs#f.0", "s:src/a.rs#f.1"]
        );
        // Cross-edges to either endpoint table, grouped back by site.
        let calls = rows(
            &conn,
            "MATCH (:Entity {key: 's:src/a.rs#g.0'})-[c:CALLS]->(t) \
             RETURN c.site, c.resolution, t.key ORDER BY c.site, t.key",
        );
        assert_eq!(calls.len(), 3, "{calls:?}");
    } // Database dropped: closed.
    assert!(path.exists(), "on-disk database persisted");

    let db = open(&path).unwrap();
    let conn = Connection::new(&db).unwrap();
    assert_eq!(
        strings(&conn, CHILDREN_OF_FILE),
        ["s:src/a.rs#f.0", "s:src/a.rs#f.1"]
    );

    // Duplicate primary key is rejected, not merged.
    assert!(
        conn.query("CREATE (:Entity {key: 'r', category: 'repository'})")
            .is_err(),
        "duplicate PK accepted"
    );

    // Update, edge delete, node delete.
    conn.query("MATCH (e:Entity {key: 's:src/a.rs#g.0'}) SET e.test = true")
        .unwrap();
    assert_eq!(
        count(&conn, "MATCH (e:Entity) WHERE e.test RETURN count(e)"),
        1
    );
    conn.query("MATCH (:Entity)-[c:CALLS {resolution: 'unresolved'}]->(:Unresolved) DELETE c")
        .unwrap();
    assert_eq!(count(&conn, "MATCH ()-[c:CALLS]->() RETURN count(c)"), 2);
    // A node with edges cannot be deleted without DETACH.
    assert!(
        conn.query("MATCH (e:Entity {key: 's:src/a.rs#f.0'}) DELETE e")
            .is_err()
    );
    conn.query("MATCH (e:Entity {key: 's:src/a.rs#f.0'}) DETACH DELETE e")
        .unwrap();
    assert_eq!(count(&conn, "MATCH (e:Entity) RETURN count(e)"), 4);
    assert_eq!(count(&conn, "MATCH ()-[c:CALLS]->() RETURN count(c)"), 1);
    drop(conn);
    drop(db);
    let _ = std::fs::remove_dir_all(dir);
}

#[test]
fn rollback_and_failed_statement_in_a_transaction() {
    let dir = tempdir("rollback");
    let db = open(&dir.join("db")).unwrap();
    let conn = Connection::new(&db).unwrap();
    conn.query(SCHEMA[0]).unwrap();
    conn.query("BEGIN TRANSACTION").unwrap();
    conn.query("CREATE (:Entity {key: 'a'})").unwrap();
    conn.query("ROLLBACK").unwrap();
    assert_eq!(count(&conn, "MATCH (e:Entity) RETURN count(e)"), 0);

    // A failing statement inside a manual transaction: what survives?
    conn.query("BEGIN TRANSACTION").unwrap();
    conn.query("CREATE (:Entity {key: 'b'})").unwrap();
    assert!(conn.query("CREATE (:Entity {key: 'b'})").is_err());
    let commit = conn.query("COMMIT").map(drop);
    eprintln!("commit after failed statement: {:?}", commit.as_ref().err());
    let after = count(&conn, "MATCH (e:Entity) RETURN count(e)");
    eprintln!("entities after failed-statement transaction: {after}");
    // A failed statement aborts the whole manual transaction.
    assert!(commit.is_err());
    assert_eq!(after, 0);

    // DDL inside a manual transaction, then rollback.
    conn.query("BEGIN TRANSACTION").unwrap();
    let ddl = conn
        .query("CREATE NODE TABLE Gen2(key STRING PRIMARY KEY)")
        .map(drop);
    eprintln!("DDL in manual transaction: {:?}", ddl.as_ref().err());
    let _ = conn.query("ROLLBACK");
    let tables = strings(&conn, "CALL SHOW_TABLES() RETURN name ORDER BY name");
    eprintln!("tables after rollback: {tables:?}");
    assert!(ddl.is_ok());
    assert_eq!(tables, ["Entity"]);
    drop(conn);
    drop(db);
    let _ = std::fs::remove_dir_all(dir);
}

/// Runs this test binary's `child` test with `mode`; returns its exit code.
fn run_child(mode: &str, path: &Path) -> Option<i32> {
    Command::new(std::env::current_exe().unwrap())
        .args(["--exact", "child", "--nocapture", "--test-threads=1"])
        .env("SPIKE_CHILD", mode)
        .env("SPIKE_DB", path)
        .status()
        .unwrap()
        .code()
}

#[test]
fn child() {
    let Ok(mode) = std::env::var("SPIKE_CHILD") else {
        return;
    };
    let path = PathBuf::from(std::env::var("SPIKE_DB").unwrap());
    match mode.as_str() {
        "crash-mid-transaction" => {
            let db = open(&path).unwrap();
            let conn = Connection::new(&db).unwrap();
            conn.query(SCHEMA[0]).unwrap();
            conn.query("CREATE (:Entity {key: 'committed'})").unwrap();
            conn.query("BEGIN TRANSACTION").unwrap();
            conn.query("CREATE (:Entity {key: 'uncommitted'})").unwrap();
            std::process::abort();
        }
        "open-read-write" => std::process::exit(match open(&path) {
            Ok(_) => 0,
            Err(e) => {
                eprintln!("read-write open refused: {e}");
                3
            }
        }),
        "open-read-only" => {
            std::process::exit(match Database::new(&path, config().read_only(true)) {
                Ok(_) => 0,
                Err(e) => {
                    eprintln!("read-only open refused: {e}");
                    3
                }
            })
        }
        other => panic!("unknown child mode {other}"),
    }
}

#[test]
fn crash_before_commit_is_invisible_after_commit_is_durable() {
    let dir = tempdir("crash");
    let path = dir.join("db");
    assert_ne!(run_child("crash-mid-transaction", &path), Some(0));
    let db = open(&path).unwrap();
    let conn = Connection::new(&db).unwrap();
    assert_eq!(
        strings(&conn, "MATCH (e:Entity) RETURN e.key ORDER BY e.key"),
        ["committed"]
    );
    drop(conn);
    drop(db);
    let _ = std::fs::remove_dir_all(dir);
}

#[test]
fn one_read_write_owner_across_processes() {
    let dir = tempdir("owner");
    let path = dir.join("db");
    let db = open(&path).unwrap();
    // Another process cannot open a second writer...
    assert_eq!(run_child("open-read-write", &path), Some(3));
    // ...but a read-only open succeeds, although the concurrency docs say
    // READ_WRITE and READ_ONLY owners must not coexist. The lock does not
    // enforce that; OXIDE's runtime has to.
    assert_eq!(run_child("open-read-only", &path), Some(0));
    // Within one process nothing stops a second read-write Database on the
    // same files: the runtime must hold exactly one per store itself.
    let second = open(&path);
    assert!(second.is_ok(), "in-process second owner now refused");
    drop(second);
    drop(db);
    // After the owner closes, another process can open it.
    assert_eq!(run_child("open-read-write", &path), Some(0));
    let _ = std::fs::remove_dir_all(dir);
}

#[test]
fn one_writer_and_snapshot_reads_across_connections() {
    let dir = tempdir("concurrency");
    let db = open(&dir.join("db")).unwrap();
    let setup = Connection::new(&db).unwrap();
    setup.query(SCHEMA[0]).unwrap();
    setup.query("CREATE (:Entity {key: 'v1'})").unwrap();
    let writer = Connection::new(&db).unwrap();
    let other_writer = Connection::new(&db).unwrap();
    let reader = Connection::new(&db).unwrap();

    reader.query("BEGIN TRANSACTION READ ONLY").unwrap();
    assert_eq!(count(&reader, "MATCH (e:Entity) RETURN count(e)"), 1);

    writer.query("BEGIN TRANSACTION").unwrap();
    writer.query("CREATE (:Entity {key: 'v2'})").unwrap();

    // A second write transaction while one is open: error or block?
    std::thread::scope(|s| {
        let (tx, rx) = mpsc::channel();
        let other_writer = &other_writer;
        s.spawn(move || {
            let started = Instant::now();
            let r = other_writer.query("CREATE (:Entity {key: 'w'})");
            tx.send((r.map(drop).map_err(|e| e.to_string()), started.elapsed()))
                .unwrap();
        });
        match rx.recv_timeout(Duration::from_secs(5)) {
            Ok((result, took)) => {
                eprintln!("concurrent second write: {result:?} after {took:?}");
                // Fails fast instead of waiting: callers must serialize writes.
                assert!(result.is_err());
                writer.query("COMMIT").unwrap();
            }
            Err(_) => {
                writer.query("COMMIT").unwrap();
                panic!("second write transaction blocked: {:?}", rx.recv());
            }
        }
    });

    // Does the read-only transaction keep its snapshot across the commit?
    let during = count(&reader, "MATCH (e:Entity) RETURN count(e)");
    eprintln!("reader inside its transaction after commit sees {during}");
    reader.query("COMMIT").unwrap();
    let after = count(&reader, "MATCH (e:Entity) RETURN count(e)");
    eprintln!("reader after its transaction sees {after}");
    assert_eq!(
        (during, after),
        (1, 2),
        "read-only transactions are snapshots"
    );
    drop((setup, writer, other_writer, reader));
    drop(db);
    let _ = std::fs::remove_dir_all(dir);
}

// count(*) over a cross product is factorized and fast; sum(a * b) is not.
const SLOW: &str = "UNWIND range(1, 100000) AS a UNWIND range(1, 100000) AS b RETURN sum(a * b)";

#[test]
fn timeout_and_interrupt_cancel_queries() {
    let dir = tempdir("cancel");
    let db = open(&dir.join("db")).unwrap();
    let conn = Connection::new(&db).unwrap();
    conn.set_query_timeout(200);
    let started = Instant::now();
    let r = conn.query(SLOW).map(drop);
    eprintln!("timeout: {r:?} after {:?}", started.elapsed());
    assert!(r.is_err() && started.elapsed() < Duration::from_secs(10));
    conn.set_query_timeout(0);

    let started = Instant::now();
    let r = std::thread::scope(|s| {
        s.spawn(|| {
            std::thread::sleep(Duration::from_millis(200));
            conn.interrupt().unwrap();
        });
        conn.query(SLOW).map(drop)
    });
    eprintln!("interrupt: {r:?} after {:?}", started.elapsed());
    assert!(r.is_err() && started.elapsed() < Duration::from_secs(10));
    // The connection stays usable.
    assert_eq!(count(&conn, "RETURN 1"), 1);
    drop(conn);
    drop(db);
    let _ = std::fs::remove_dir_all(dir);
}

#[test]
fn fts_and_vector_need_pinned_extension_files() {
    let dir = tempdir("ext");
    let db = open(&dir.join("db")).unwrap();
    let conn = Connection::new(&db).unwrap();
    let loaded = rows(&conn, "CALL SHOW_LOADED_EXTENSIONS() RETURN *");
    eprintln!("loaded extensions at open: {loaded:?}");
    // Not statically linked: without INSTALL (a network download) they fail.
    for ext in ["fts", "vector"] {
        let r = conn.query(&format!("LOAD {ext}")).map(drop);
        assert!(r.is_err(), "{ext} loaded without install");
    }
    // Offline path: LOAD the sha256-pinned files fetch-liblbug.sh placed.
    let Ok(ext_dir) = std::env::var("SPIKE_EXTENSION_DIR") else {
        eprintln!("SPIKE_EXTENSION_DIR unset; skipping extension behavior");
        return;
    };
    for ext in ["fts", "vector"] {
        conn.query(&format!(
            "LOAD EXTENSION '{ext_dir}/lib{ext}.lbug_extension'"
        ))
        .unwrap_or_else(|e| panic!("LOAD {ext} by path: {e}"));
    }
    eprintln!(
        "loaded: {:?}",
        rows(&conn, "CALL SHOW_LOADED_EXTENSIONS() RETURN *")
    );
    conn.query("CREATE NODE TABLE Doc(key STRING PRIMARY KEY, text STRING, emb FLOAT[3])")
        .unwrap();
    conn.query(
        "CREATE (:Doc {key: 'a', text: 'parse_config reads the config file', emb: [1.0, 0.0, 0.0]})",
    )
    .unwrap();
    conn.query("CREATE (:Doc {key: 'b', text: 'render html output', emb: [0.0, 1.0, 0.0]})")
        .unwrap();

    let keys = |q: &str| strings(&conn, q);
    let fts = |term: &str| {
        keys(&format!(
            "CALL QUERY_FTS_INDEX('Doc', 'doc_fts', '{term}') RETURN node.key ORDER BY node.key"
        ))
    };
    conn.query("CALL CREATE_FTS_INDEX('Doc', 'doc_fts', ['text'], stemmer := 'none')")
        .unwrap();
    assert_eq!(fts("config"), ["a"]);
    // Maintained online: rows written after index creation are searchable.
    conn.query("CREATE (:Doc {key: 'c', text: 'config loader', emb: [0.9, 0.1, 0.0]})")
        .unwrap();
    assert_eq!(fts("config"), ["a", "c"]);
    // Case-insensitive; how code identifiers tokenize is recorded, not assumed.
    assert_eq!(fts("Config"), ["a", "c"]);
    eprintln!(
        "FTS tokenization: 'parse_config' -> {:?}, 'parse' -> {:?}, 'reads' -> {:?}",
        fts("parse_config"),
        fts("parse"),
        fts("reads")
    );
    conn.query("MATCH (d:Doc {key: 'a'}) DELETE d").unwrap();
    assert_eq!(fts("config"), ["c"]);

    let nearest = "CALL QUERY_VECTOR_INDEX('Doc', 'doc_vec', [1.0, 0.0, 0.0], 2) \
                   RETURN node.key ORDER BY distance";
    conn.query("CALL CREATE_VECTOR_INDEX('Doc', 'doc_vec', 'emb', metric := 'cosine')")
        .unwrap();
    assert_eq!(keys(nearest), ["c", "b"]);
    conn.query("CREATE (:Doc {key: 'd', text: 'x', emb: [1.0, 0.01, 0.0]})")
        .unwrap();
    assert_eq!(keys(nearest), ["d", "c"]);
    conn.query("MATCH (d:Doc {key: 'd'}) DELETE d").unwrap();
    assert_eq!(keys(nearest), ["c", "b"]);
    drop(conn);
    drop(db);
    let _ = std::fs::remove_dir_all(dir);
}

/// Logical publication (ADR-0002): one database directory per generation and
/// an atomically replaced CURRENT pointer. A crash mid-build leaves an
/// unreferenced directory; the pointer never names an incomplete generation.
#[test]
fn generation_per_directory_with_atomic_pointer() {
    let root = tempdir("publish");
    let publish = |generation: &str| {
        let tmp = root.join("CURRENT.tmp");
        std::fs::write(&tmp, generation).unwrap();
        std::fs::File::open(&tmp).unwrap().sync_all().unwrap();
        std::fs::rename(&tmp, root.join("CURRENT")).unwrap();
        std::fs::File::open(&root).unwrap().sync_all().unwrap();
    };
    let current = || std::fs::read_to_string(root.join("CURRENT")).unwrap();

    {
        let db = open(&root.join("g1")).unwrap();
        load_fixture(&Connection::new(&db).unwrap());
    } // closed before it is published
    publish("g1");

    // A pinned reader on g1 coexists with builders of later generations.
    let g1 = open(&root.join(current())).unwrap();
    let reader = Connection::new(&g1).unwrap();
    // The builder of g2 crashes mid-build; g2 is never published.
    assert_ne!(
        run_child("crash-mid-transaction", &root.join("g2")),
        Some(0)
    );
    assert_eq!(current(), "g1");
    assert_eq!(
        strings(&reader, CHILDREN_OF_FILE),
        ["s:src/a.rs#f.0", "s:src/a.rs#f.1"]
    );

    // Rebuild g3 from source while g1 stays readable, then publish it.
    {
        let g3 = open(&root.join("g3")).unwrap();
        load_fixture(&Connection::new(&g3).unwrap());
    }
    publish("g3");
    assert_eq!(current(), "g3");
    // The pinned g1 view is unaffected by the publication.
    assert_eq!(count(&reader, "MATCH (e:Entity) RETURN count(e)"), 5);
    drop(reader);
    drop(g1);
    let _ = std::fs::remove_dir_all(root);
}
