use shop::{open, Mode};

#[test]
fn open_read() {
    let _store = open(Mode::Read);
}
