//! Helpers shared by the shop crates.
pub mod text;

#[macro_export]
macro_rules! square {
    ($x:expr) => {
        $x * $x
    };
}

pub const VERSION: &str = "1";

pub fn map_all<T, U>(items: Vec<T>, f: impl Fn(T) -> U) -> Vec<U> {
    items.into_iter().map(f).collect()
}
