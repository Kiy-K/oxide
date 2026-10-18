function clamp(value, low, high) { return Math.min(Math.max(value, low), high); }
function positive(value) { return clamp(value, 0, Infinity); }
module.exports = { clamp, positive };
