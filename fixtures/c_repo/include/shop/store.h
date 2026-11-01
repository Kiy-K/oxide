/* Keyed entries for the shop. */
#ifndef SHOP_STORE_H
#define SHOP_STORE_H

#include <stddef.h>

#define STORE_MAX 64
#define STORE_EMPTY(s) ((s)->count == 0)

typedef const char *Key;

struct Entry {
	Key key;
	const char *value;
};

typedef struct Store {
	struct Entry entries[STORE_MAX];
	size_t count;
} Store;

enum Mode { MODE_READ = 1, MODE_WRITE = 2 };

extern int store_default_limit;

Store *store_open(enum Mode mode);
const char *store_get(Store *s, Key key);
const char *store_must_get(Store *s, Key key);
const char *platform_path(void);

static inline size_t store_size(const Store *s) { return s->count; }

#endif
