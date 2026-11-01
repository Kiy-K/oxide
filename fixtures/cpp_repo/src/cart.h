#pragma once
#include "shop/store.hpp"

namespace shop {
template <typename T>
struct Cart {
  T first(const Store &store) const;
  int count = 0;
};
}  // namespace shop
