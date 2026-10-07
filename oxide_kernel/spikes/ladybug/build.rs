// Extensions resolve lbug symbols from the host binary, so test binaries
// must export them (lbug crate docs, "Using Extensions").
fn main() {
    println!("cargo:rustc-link-arg=-rdynamic");
}
