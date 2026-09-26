/// Slice `start..end` (1-based inclusive) from a file, capped at `cap` lines.
pub fn read_snippet(path: &std::path::Path, start: u32, end: u32, cap: usize) -> String {
    let Ok(src) = std::fs::read_to_string(path) else {
        return String::new();
    };
    src.lines()
        .skip(start.saturating_sub(1) as usize)
        .take(((end.saturating_sub(start - 1)) as usize).min(cap))
        .collect::<Vec<_>>()
        .join("\n")
}
