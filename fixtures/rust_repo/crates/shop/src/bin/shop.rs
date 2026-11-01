use shop::{open, Mode};

fn main() {
    let store = open(Mode::Read);
    println!("{}", store.label());
}
