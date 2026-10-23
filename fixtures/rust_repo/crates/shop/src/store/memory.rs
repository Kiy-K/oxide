use super::*;

#[derive(Default)]
pub struct Memory {
    items: HashMap<Key, String>,
}

impl Backend for Memory {
    fn get(&self, key: &Key) -> Option<String> {
        self.items.get(key).cloned()
    }

    fn keys(&self) -> Vec<Key> {
        self.items.keys().cloned().collect()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn empty_memory_has_no_keys() {
        let memory = Memory::default();
        assert!(memory.keys().is_empty());
        assert_eq!(MAX_KEYS, 1024);
    }

    #[test]
    fn opens_a_store() {
        let store = crate::open(Mode::Read);
        store.must_get(&"k".to_string());
    }
}
