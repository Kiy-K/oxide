use std::path::PathBuf;
use std::process::Command;

fn main() {
    assert_eq!(
        std::env::var("CARGO_CFG_TARGET_OS").unwrap(),
        "linux",
        "Phase 2B supports Linux x86_64 only"
    );
    assert_eq!(
        std::env::var("CARGO_CFG_TARGET_ARCH").unwrap(),
        "x86_64",
        "Phase 2B supports Linux x86_64 only"
    );
    let directory = PathBuf::from(std::env::var("CARGO_MANIFEST_DIR").unwrap())
        .join("../../native/.cache/liblbug-0.21.2");
    for (name, expected) in [
        (
            "liblbug.a",
            "9100591f7cf1de0f565c96b55e3293998763260fc936bfbb9e304bef6f4ec2b8",
        ),
        (
            "lbug.h",
            "fec7bf52a59ab673cac1d74443a0bf88867395e673766f8190dcc608deb9c307",
        ),
        (
            "lbug.hpp",
            "3657d3cffd954728c30710206c84971231b1decde5f5835c22a576cf5de3704d",
        ),
    ] {
        let file = directory.join(name);
        println!("cargo:rerun-if-changed={}", file.display());
        let output = Command::new("sha256sum")
            .arg(&file)
            .output()
            .expect("Linux sha256sum is required");
        assert!(
            output.status.success(),
            "Run mise run native:prepare before building"
        );
        assert_eq!(
            String::from_utf8(output.stdout)
                .unwrap()
                .split_whitespace()
                .next(),
            Some(expected),
            "Native artifact integrity failure; run mise run native:prepare"
        );
    }
    // The pinned FTS extension (native:prepare). Its digest is checked by the
    // runtime before every load; a missing file is an unavailable lexical
    // accelerator, not a build failure.
    let fts = PathBuf::from(std::env::var("CARGO_MANIFEST_DIR").unwrap())
        .join("../../native/.cache/extensions-0.21.0/libfts.lbug_extension");
    println!("cargo:rerun-if-changed={}", fts.display());
    println!("cargo:rustc-env=OXIDE_FTS_EXTENSION={}", fts.display());
    // ADR-0002 § 3: extensions resolve lbug symbols from the host binary.
    println!("cargo:rustc-link-arg=-rdynamic");
}
