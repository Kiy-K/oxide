#pragma once
#include <string>
#include "shop/limits.h"

namespace shop {

using Key = std::string;

enum class Mode { Read, Write };

class Backend {
 public:
  virtual ~Backend();
  int capacity() const;
};

/// A keyed store over a backend.
class Store : public Backend {
 public:
  explicit Store(Mode mode);
  ~Store();
  const std::string &get(const Key &key) const;
  const std::string &get(const Key &key, const std::string &fallback) const;
  const std::string &must_get(const Key &key) const;
  static Store open(Mode mode);
  bool operator==(const Store &other) const;

 private:
  int size_;
  Mode mode_;
};

int store_limit();

}  // namespace shop
