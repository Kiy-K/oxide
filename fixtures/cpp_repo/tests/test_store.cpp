#include "shop/store.hpp"

int test_must_get() {
  shop::Store store(shop::Mode::Read);
  return store.must_get("k").empty() ? 1 : 0;
}
