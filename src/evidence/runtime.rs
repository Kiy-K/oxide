//! The one isolated Tokio runtime evidence collection uses — built once,
//! process-lifetime, safe for both a one-shot CLI call and `oxide mcp`'s
//! long-lived process; never rebuilt per query.
//!
//! Only `rt-multi-thread` is enabled. Git/LSP evidence collection dispatches
//! existing blocking `std::process`/`std::thread` code via `spawn_blocking`
//! rather than moving to `tokio::process`, so no `time`/`process`/`net`
//! feature is needed. `max_blocking_threads(4)`: at most two blocking
//! sources (Git, LSP) run concurrently today, well under Tokio's default
//! 512 — matches this repo's shared-machine CPU-budget convention.

use std::sync::OnceLock;
use tokio::runtime::Runtime;

static EVIDENCE_RUNTIME: OnceLock<Runtime> = OnceLock::new();

pub fn runtime() -> &'static Runtime {
    EVIDENCE_RUNTIME.get_or_init(|| {
        tokio::runtime::Builder::new_multi_thread()
            .worker_threads(2)
            .max_blocking_threads(4)
            .thread_name("oxide-evidence")
            .build()
            .expect("evidence runtime must build: only rt-multi-thread is required")
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn runtime_is_a_singleton_across_calls() {
        let a = runtime() as *const Runtime;
        let b = runtime() as *const Runtime;
        assert_eq!(a, b);
    }

    #[test]
    fn runtime_can_join_two_concurrent_blocking_tasks() {
        let rt = runtime();
        let (a, b) = rt.block_on(async {
            let t1 = tokio::task::spawn_blocking(|| {
                std::thread::sleep(std::time::Duration::from_millis(20));
                1
            });
            let t2 = tokio::task::spawn_blocking(|| {
                std::thread::sleep(std::time::Duration::from_millis(20));
                2
            });
            tokio::join!(t1, t2)
        });
        assert_eq!((a.unwrap(), b.unwrap()), (1, 2));
    }
}
