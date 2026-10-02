// Loads this platform's prebuilt addon, `oxide_native.<platform>.node`, from
// this package: the name `mise run native:build` writes and a release ships.
// Only platforms with a tested build are listed; anything else throws.
"use strict";

const PLATFORMS = {
  "linux-x64-gnu": "oxide_native.linux-x64-gnu.node",
  "darwin-arm64": "oxide_native.darwin-arm64.node",
};

function platform() {
  const base = `${process.platform}-${process.arch}`;
  if (process.platform !== "linux") return base;
  // glibc reports its runtime version; musl does not.
  return process.report.getReport().header.glibcVersionRuntime ? `${base}-gnu` : `${base}-musl`;
}

const key = platform();
const file = PLATFORMS[key];
if (file === undefined) {
  throw new Error(`@oxide/native has no build for ${key}`);
}
module.exports = require(`./${file}`);
