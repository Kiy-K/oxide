mod memory;

use shop_util::text::pad;
use std::collections::HashMap;

pub use self::memory::Memory;

pub type Key = String;

pub const MAX_KEYS: usize = 1024;
static NAME: &str = "store";

#[derive(Debug, Clone, Copy, PartialEq)]
pub enum Mode {
    Read,
    Write(u8),
}

pub trait Backend {
    fn get(&self, key: &Key) -> Option<String>;

    fn keys(&self) -> Vec<Key> {
        Vec::new()
    }

    fn first(&self) -> Option<Key> {
        self.keys().into_iter().next()
    }
}

pub struct Store {
    mode: Mode,
    backend: Memory,
    cache: HashMap<Key, String>,
}

impl Store {
    pub fn new(mode: Mode) -> Self {
        Store { mode, backend: Memory::default(), cache: HashMap::new() }
    }

    pub fn must_get(&self, key: &Key) -> String {
        self.get(key).expect("missing key")
    }

    pub fn label(&self) -> String {
        pad(NAME, MAX_KEYS)
    }

    pub fn mode(&self) -> Mode {
        match self.mode {
            Mode::Read => Mode::Read,
            Mode::Write(n) => Mode::Write(n),
        }
    }

    fn get(&self, key: &Key) -> Option<String> {
        self.backend.get(key)
    }
}

impl Backend for Store {
    fn get(&self, key: &Key) -> Option<String> {
        Store::get(self, key)
    }
}
