const ns = require('./ns');

class Store {
  get(key) {
    return key;
  }
}

module.exports = { Store, make: () => new ns.Base() };
