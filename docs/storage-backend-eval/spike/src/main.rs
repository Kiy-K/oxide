/// Vector writes are committed in transactions of this many rows on BOTH
/// backends. The comparison is of the store, not of how the harness batched.
pub const VEC_CHUNK: usize = 1000;

/// How many distinct targets/terms/substrings each correctness gate probes.
/// One probe proves one query worked; several make a lucky pass unlikely.
pub const PROBES: usize = 5;

mod corpus;
mod gate;
mod sqlite;
#[cfg(feature = "surreal")]
mod surreal;
#[cfg(feature = "turso")]
mod turso;

/// `sdb_gate <sqlite|surreal|turso> [files] [per_file]` — one backend per process so
/// peak-RSS and startup numbers are not contaminated by the other.
fn main() -> anyhow::Result<()> {
    let a: Vec<String> = std::env::args().collect();
    let which = a.get(1).map(|s| s.as_str()).unwrap_or("sqlite");
    let files: usize = a.get(2).and_then(|s| s.parse().ok()).unwrap_or(400);
    let per_file: usize = a.get(3).and_then(|s| s.parse().ok()).unwrap_or(25);
    let dir = tempfile::tempdir()?;
    // An explicit 4th argument keeps the store after the process exits, which
    // the two-process and cold-query measurements need.
    let workdir = |a: &Vec<String>| -> std::path::PathBuf {
        a.get(4)
            .map(std::path::PathBuf::from)
            .unwrap_or_else(|| dir.path().to_path_buf())
    };
    let rep = match which {
        "sqlite" => sqlite::run(&workdir(&a), files, per_file)?,
        "sqlite-conc" => sqlite::concurrency(&workdir(&a), files, per_file)?,
        #[cfg(feature = "surreal")]
        "surreal-conc" => {
            let d = workdir(&a);
            tokio::runtime::Builder::new_multi_thread()
                .enable_all()
                .build()?
                .block_on(surreal::concurrency(&d, files, per_file))?
        }
        "sqlite-ctx" => {
            let f = a.get(3).and_then(|s| s.parse().ok()).unwrap_or(400);
            let pf = a.get(4).and_then(|s| s.parse().ok()).unwrap_or(25);
            sqlite::context_shaped(std::path::Path::new(&a[2]), f, pf)?;
            return Ok(());
        }
        #[cfg(feature = "surreal")]
        "surreal-ctx" => {
            let f = a.get(3).and_then(|s| s.parse().ok()).unwrap_or(400);
            let pf = a.get(4).and_then(|s| s.parse().ok()).unwrap_or(25);
            tokio::runtime::Builder::new_multi_thread().enable_all().build()?.block_on(
                surreal::context_shaped(std::path::Path::new(&a[2]), f, pf))?;
            return Ok(());
        }
        "sqlite-query" => {
            sqlite::one_query(std::path::Path::new(&a[2]))?;
            return Ok(());
        }
        #[cfg(feature = "surreal")]
        "surreal-audit" => {
            tokio::runtime::Builder::new_multi_thread().enable_all().build()?.block_on(
                surreal::audit(std::path::Path::new(&a[2])))?;
            return Ok(());
        }
        #[cfg(feature = "surreal")]
        "surreal-query" => {
            tokio::runtime::Builder::new_multi_thread().enable_all().build()?.block_on(
                surreal::one_query(std::path::Path::new(&a[2])))?;
            return Ok(());
        }
        "sqlite-hold" => {
            sqlite::hold(&a[2], a.get(3).and_then(|s| s.parse().ok()).unwrap_or(20))?;
            return Ok(());
        }
        "sqlite-try" => {
            sqlite::try_open(&a[2])?;
            return Ok(());
        }
        // Two processes, because RocksDB's lock is not released on drop and
        // because that is how OXIDE actually runs: index, then query.
        #[cfg(feature = "surreal")]
        "surreal1" | "surreal2" => {
            let d = workdir(&a);
            let rt = tokio::runtime::Builder::new_multi_thread().enable_all().build()?;
            if which == "surreal1" {
                rt.block_on(surreal::run1(&d, files, per_file))?
            } else {
                rt.block_on(surreal::run2(&d, files, per_file))?
            }
        }
        #[cfg(feature = "surreal")]
        "surreal-hold" => {
            tokio::runtime::Builder::new_multi_thread().enable_all().build()?.block_on(
                surreal::hold(&a[2], a[3].parse().unwrap_or(20)))?;
            return Ok(());
        }
        #[cfg(feature = "surreal")]
        "surreal-reopen" => {
            tokio::runtime::Builder::new_multi_thread().enable_all().build()?.block_on(
                surreal::reopen_same_process(&a[2]))?;
            return Ok(());
        }
        #[cfg(feature = "surreal")]
        "surreal-try" => {
            tokio::runtime::Builder::new_multi_thread().enable_all().build()?.block_on(
                surreal::try_open(&a[2]))?;
            return Ok(());
        }
        #[cfg(feature = "turso")]
        "turso" => turso::run(&workdir(&a), files, per_file)?,
        // Separate process: peak RSS is a high-water mark, so measuring ingest
        // shapes alongside the gate suite would charge the gate suite for them.
        #[cfg(feature = "turso")]
        "turso-shapes" => turso::ingest_shapes(&workdir(&a), files, per_file)?,
        other => anyhow::bail!("unknown backend {other}"),
    };
    rep.print();
    Ok(())
}
