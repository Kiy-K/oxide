use crate::store::{self, Backend, Store};
use shop_util::{map_all, square};

pub struct Cart {
    store: Store,
    lines: Vec<Line>,
}

struct Line(u32);

pub mod pricing {
    use super::helper;

    pub fn discount(total: u32) -> u32 {
        helper() + total
    }
}

impl Cart {
    pub fn total(&self) -> u32 {
        square!(self.lines.len() as u32) + pricing::discount(Line(1).0)
    }

    pub fn names(&self) -> Vec<String> {
        map_all(vec![1], |n| n.to_string())
    }

    pub fn first_key(&self) -> Option<store::Key> {
        self.store.first()
    }

    fn open() -> Cart {
        Cart { store: store::Store::new(store::Mode::Write(1)), lines: vec![] }
    }
}

fn helper() -> u32 {
    0
}
