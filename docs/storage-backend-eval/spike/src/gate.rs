//! Shared gate bookkeeping. A gate is PASS/FAIL/NA with a one-line detail;
//! measurements ride along as separate metric lines so the report is
//! diffable between backends.

pub struct Report {
    pub backend: &'static str,
    pub rows: Vec<(String, &'static str, String)>,
}

impl Report {
    pub fn new(backend: &'static str) -> Self {
        Report { backend, rows: Vec::new() }
    }
    // Echoed to stderr as they are recorded, not only in the final table: a
    // backend that dies mid-run still has to say how far it got.
    pub fn gate(&mut self, name: &str, pass: bool, detail: impl Into<String>) {
        let d = detail.into();
        eprintln!("[{}] {} {}", if pass { "PASS" } else { "FAIL" }, name, d);
        self.rows.push((name.into(), if pass { "PASS" } else { "FAIL" }, d));
    }
    pub fn metric(&mut self, name: &str, detail: impl Into<String>) {
        let d = detail.into();
        eprintln!("[METRIC] {name} {d}");
        self.rows.push((name.into(), "METRIC", d));
    }
    /// Progress marker for long write phases; not part of the report.
    pub fn step(msg: &str) {
        eprintln!("[step] {msg}");
    }
    pub fn print(&self) {
        println!("\n=== {} ===", self.backend);
        for (n, s, d) in &self.rows {
            println!("{s:<6} {n:<34} {d}");
        }
        let failed = self.rows.iter().filter(|r| r.1 == "FAIL").count();
        println!("-- {} gate(s) FAILED", failed);
    }
}

pub fn rss_kb() -> u64 {
    std::fs::read_to_string("/proc/self/status")
        .ok()
        .and_then(|s| {
            s.lines()
                .find(|l| l.starts_with("VmHWM:"))
                .and_then(|l| l.split_whitespace().nth(1)?.parse().ok())
        })
        .unwrap_or(0)
}

pub fn dir_bytes(p: &std::path::Path) -> u64 {
    let mut t = 0;
    if let Ok(rd) = std::fs::read_dir(p) {
        for e in rd.flatten() {
            let m = match e.metadata() {
                Ok(m) => m,
                Err(_) => continue,
            };
            t += if m.is_dir() { dir_bytes(&e.path()) } else { m.len() };
        }
    }
    t
}
