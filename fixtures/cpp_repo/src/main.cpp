#include <cstdio>

#include "cart.h"

using namespace shop;

int main() {
  Store store = Store::open(Mode::Read);
  Cart<int> cart;
  std::printf("%d %d\n", store_limit(), cart.count);
  return shop::store_limit() > 0 ? 0 : 1;
}
