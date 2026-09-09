use std::collections::HashMap;
use crate::backend::Backend;

pub const DEFAULT_LIMIT: usize = 10;

pub type Key = String;

pub struct Store {
    entries: HashMap<Key, String>,
}

impl Store {
    pub fn new() -> Self {
        Store { entries: HashMap::new() }
    }

    fn helper(&self) -> usize {
        fn inner(n: usize) -> usize {
            n + 1
        }
        inner(self.entries.len())
    }
}

impl Backend for Store {
    fn get(&self, key: &Key) -> Option<String> {
        self.entries.get(key).cloned()
    }
}

pub enum Level {
    Low,
    High,
}

pub mod net {
    pub fn fetch() -> u32 {
        0
    }
}
