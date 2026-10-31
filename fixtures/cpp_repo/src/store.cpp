#include "shop/store.hpp"

#include <stdexcept>

namespace shop {
namespace {
int hash_key(const Key &key) { return static_cast<int>(key.size()); }
}  // namespace

Backend::~Backend() {}

int Backend::capacity() const { return STORE_MAX; }

Store::Store(Mode mode) : size_(clamp_size(capacity())), mode_(mode) {}

Store::~Store() {}

const std::string &Store::get(const Key &key) const { return get(key, key); }

const std::string &Store::get(const Key &key, const std::string &fallback) const {
  return hash_key(key) < size_ ? key : fallback;
}

const std::string &Store::must_get(const Key &key) const {
  if (key.empty()) throw std::out_of_range("empty key");
  return this->get(key);
}

Store Store::open(Mode mode) { return Store(mode); }

bool Store::operator==(const Store &other) const { return size_ == other.size_; }

int store_limit() { return clamp_size(STORE_MAX); }

}  // namespace shop
