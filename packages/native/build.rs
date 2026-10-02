fn main() {
    napi_build::setup();
    // On macOS the dylib's own install name (LC_ID_DYLIB) would be its
    // absolute build path; Node loads the addon by file path, so give it a
    // path-free one.
    if std::env::var("CARGO_CFG_TARGET_OS").as_deref() == Ok("macos") {
        println!("cargo:rustc-cdylib-link-arg=-Wl,-install_name,@rpath/liboxide_native.dylib");
    }
}
