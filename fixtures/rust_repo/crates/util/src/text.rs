pub fn pad(s: &str, width: usize) -> String {
    format!("{s:<width$}")
}

pub fn shout(s: &str) -> String {
    pad(&s.to_uppercase(), 1)
}
