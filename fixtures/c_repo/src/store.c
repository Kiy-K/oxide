#include <stdlib.h>
#include <string.h>

#include "shop/store.h"

int store_default_limit = 10;

static unsigned hash_key(Key key)
{
	unsigned h = 0;
	while (*key)
		h = h * 31 + (unsigned char)*key++;
	return h;
}

Store *store_open(enum Mode mode)
{
	Store *s = calloc(1, sizeof(Store));
	(void)mode;
	return s;
}

const char *store_get(Store *s, Key key)
{
	size_t i;
	(void)hash_key(key);
	if (STORE_EMPTY(s))
		return NULL;
	for (i = 0; i < store_size(s); i++)
		if (strcmp(s->entries[i].key, key) == 0)
			return s->entries[i].value;
	return NULL;
}

const char *store_must_get(Store *s, Key key)
{
	const char *v = store_get(s, key);
	if (v == NULL)
		abort();
	return v;
}
