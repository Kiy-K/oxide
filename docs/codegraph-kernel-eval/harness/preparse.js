// Research-only (issue #23): applies CodeGraph's own shipped C/C++ `preParse`
// (offset-preserving macro blanking that production runs TS-side before the
// kernel call — src/extraction/kernel/index.ts::preParsedSource) and writes a
// second manifest whose c/cpp entries point at the blanked copies. Every other
// entry is passed through unchanged.
//   $CG/node preparse.js $CG/lib/dist work/manifest.jsonl work/manifest.preparsed.jsonl
'use strict';
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const { EXTRACTORS } = require(path.join(process.argv[2], 'extraction/languages'));
const outDir = path.join(path.dirname(process.argv[4]), 'preparsed');
fs.mkdirSync(outDir, { recursive: true });
const out = [];
for (const line of fs.readFileSync(process.argv[3], 'utf8').split('\n')) {
  if (!line.trim()) continue;
  const e = JSON.parse(line);
  const pre = EXTRACTORS[e.cg_lang] && EXTRACTORS[e.cg_lang].preParse;
  if ((e.cg_lang === 'c' || e.cg_lang === 'cpp') && pre) {
    const src = fs.readFileSync(e.path, 'utf8');
    const blanked = pre(src, e.rel);
    if (blanked.length !== src.length) throw new Error(`preParse changed length: ${e.id}`);
    const p = path.join(outDir, crypto.createHash('sha1').update(e.id).digest('hex') + path.extname(e.rel));
    fs.writeFileSync(p, blanked);
    e.path = p;
    e.preparsed = blanked !== src;
  }
  out.push(JSON.stringify(e));
}
fs.writeFileSync(process.argv[4], out.join('\n') + '\n');
