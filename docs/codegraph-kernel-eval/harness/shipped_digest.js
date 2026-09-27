// Research-only (issue #23): digests the npm-shipped CodeGraph kernel binary's
// raw output for every manifest entry — the reference `cgk-native digest` is
// compared against. Run with the bundle's own Node:
//   $CG/node shipped_digest.js $CG/lib/kernel/codegraph-kernel.node work/manifest.jsonl
'use strict';
const fs = require('fs');
const crypto = require('crypto');
const k = require(process.argv[2]);
for (const line of fs.readFileSync(process.argv[3], 'utf8').split('\n')) {
  if (!line.trim()) continue;
  const e = JSON.parse(line);
  if (!e.cg_lang) continue;
  const src = fs.readFileSync(e.path, 'utf8');
  try {
    const b = k.extractFile(e.rel, src, e.cg_lang);
    const h = crypto.createHash('sha256');
    for (const part of [b.meta.subarray(0, 28), b.nodes, b.edges, b.refs, b.arena]) h.update(part);
    console.log(`${e.id}\t${h.digest('hex')}`);
  } catch (err) {
    console.log(`${e.id}\tERR ${err.message}`);
  }
}
