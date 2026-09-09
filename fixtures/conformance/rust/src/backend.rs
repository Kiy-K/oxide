use crate::store::Key;

pub trait Resource {
    fn id(&self) -> u32;
}

pub trait Backend: Resource {
    fn get(&self, key: &Key) -> Option<String>;
}

pub fn build() -> u32 {
    log_it!("building");
    0
}
