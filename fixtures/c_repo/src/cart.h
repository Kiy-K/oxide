#ifndef CART_H
#define CART_H

#include "../include/shop/store.h"

struct Cart {
	Store *store;
	Key items[16];
	int n;
};

void cart_add(struct Cart *c, Key key);
const char *cart_first(struct Cart *c);

#endif
