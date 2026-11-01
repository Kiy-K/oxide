#include "shop/limits.h"
int clamp_size(int size) { return size > STORE_MAX ? STORE_MAX : size; }
