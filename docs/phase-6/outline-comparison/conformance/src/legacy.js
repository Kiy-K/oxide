'use strict';
const path = require('path');
const { join, resolve: res } = require('path');

const DEFAULTS = { depth: 2 };

function walk(root, depth) {
  return [root, depth];
}

class Walker {
  static instances = 0;
  #seen = new Set();
  constructor(root) {
    this.root = root;
  }
  visit(node) {
    return node;
  }
}

module.exports = walk;
module.exports.Walker = Walker;
exports.helper = function helper(x) {
  return x;
};
