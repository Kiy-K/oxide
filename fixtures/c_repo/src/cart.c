#include "cart.h"

/* A static of the same name as the store's: this file's own. */
static unsigned hash_key(Key key)
{
	return key[0];
}

void cart_add(struct Cart *c, Key key)
{
	(void)hash_key(key);
	c->items[c->n++] = key;
}

const char *cart_first(struct Cart *c)
{
	const char *(*get)(Store *, Key) = store_get;
	if (c->n == 0)
		return get(c->store, "none");
	return store_must_get(c->store, c->items[0]);
}
