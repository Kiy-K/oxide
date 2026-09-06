/// Vector writes are committed in transactions of this many rows on BOTH
/// backends. The comparison is of the store, not of how the harness batched.
pub const VEC_CHUNK: usize = 1000;

/// How many distinct targets/terms/substrings each correctness gate probes.
/// One probe proves one query worked; several make a lucky pass unlikely.
pub const PROBES: usize = 5;

mod corpus;
mod gate;
mod sqlite;
mod surreal;

/// `sdb_gate <sqlite|surreal> [files] [per_file]` — one backend per process so
/// peak-RSS and startup numbers are not contaminated by the other.
fn main() -> anyhow::Result<()> {
    let a: Vec<String> = std::env::args().collect();
    let which = a.get(1).map(|s| s.as_str()).unwrap_or("sqlite");
    let files: usize = a.get(2).and_then(|s| s.parse().ok()).unwrap_or(400);
    let per_file: usize = a.get(3).and_then(|s| s.parse().ok()).unwrap_or(25);
    let dir = tempfile::tempdir()?;
    let rep = match which {
        "sqlite" => sqlite::run(dir.path(), files, per_file)?,
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
        "surreal1" | "surreal2" => {
            let d = std::path::PathBuf::from(
                a.get(4).cloned().unwrap_or_else(|| dir.path().display().to_string()),
            );
            let rt = tokio::runtime::Builder::new_multi_thread().enable_all().build()?;
            if which == "surreal1" {
                rt.block_on(surreal::run1(&d, files, per_file))?
            } else {
                rt.block_on(surreal::run2(&d, files, per_file))?
            }
        }
        "surreal-hold" => {
            tokio::runtime::Builder::new_multi_thread().enable_all().build()?.block_on(
                surreal::hold(&a[2], a[3].parse().unwrap_or(20)))?;
            return Ok(());
        }
        "surreal-reopen" => {
            tokio::runtime::Builder::new_multi_thread().enable_all().build()?.block_on(
                surreal::reopen_same_process(&a[2]))?;
            return Ok(());
        }
        "surreal-try" => {
            tokio::runtime::Builder::new_multi_thread().enable_all().build()?.block_on(
                surreal::try_open(&a[2]))?;
            return Ok(());
        }
        other => anyhow::bail!("unknown backend {other}"),
    };
    rep.print();
    Ok(())
}
