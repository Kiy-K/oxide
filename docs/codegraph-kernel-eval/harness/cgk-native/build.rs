// The kernel's build.rs minus `napi_build::setup()`: compiles the four
// vendored grammars (kotlin, lua, scala, dart) with the same flags.
fn main() {
    let k = "vendor/codegraph/codegraph-kernel/grammars";
    for lang in ["kotlin", "lua", "scala", "dart"] {
        let dir = format!("{k}/{lang}");
        let mut c = cc::Build::new();
        c.include(&dir);
        c.file(format!("{dir}/parser.c"));
        c.file(format!("{dir}/scanner.c"));
        c.flag_if_supported("-Wno-unused-parameter");
        c.flag_if_supported("-Wno-unused-but-set-variable");
        c.flag_if_supported("-Wno-trigraphs");
        c.flag_if_supported("-Wno-unused-function");
        c.compile(&format!("tree-sitter-{lang}"));
        println!("cargo:rerun-if-changed={dir}");
    }
}
