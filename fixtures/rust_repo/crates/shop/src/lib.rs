pub mod store;
mod cart;
mod platform;

pub use cart::Cart;
pub use store::{Backend, Mode, Store};

pub fn open(mode: Mode) -> Store {
    Store::new(mode)
}
