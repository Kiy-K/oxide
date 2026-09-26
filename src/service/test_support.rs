//! Fixtures shared by more than one service test module.

use std::path::Path;

pub(super) fn seed_repo(root: &Path) {
    let src = root.join("src/thing.py");
    std::fs::create_dir_all(src.parent().unwrap()).unwrap();
    std::fs::write(&src, "def thing():\n    return 1\n").unwrap();
}
