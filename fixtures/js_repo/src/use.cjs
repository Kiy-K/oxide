const { clamp: bound } = require('./legacy.cjs');
const legacy = require('./legacy.cjs');
function normalize(value) { return legacy.positive(bound(value, -10, 10)); }
module.exports = normalize;
