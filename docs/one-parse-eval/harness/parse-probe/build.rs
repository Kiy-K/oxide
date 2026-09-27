// Route every call to tree-sitter's parse entry point through
// `__wrap_ts_parser_parse_with_options` in src/main.rs.
fn main() {
    println!("cargo:rustc-link-arg-bins=-Wl,--wrap=ts_parser_parse_with_options");
}
