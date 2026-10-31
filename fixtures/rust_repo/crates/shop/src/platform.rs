#[cfg(unix)]
pub fn separator() -> char {
    '/'
}

#[cfg(windows)]
pub fn separator() -> char {
    '\\'
}

pub fn join(a: &str, b: &str) -> String {
    let sep = separator();
    format!("{a}{sep}{b}")
}
