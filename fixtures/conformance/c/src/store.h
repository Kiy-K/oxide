#ifndef STORE_H
#define STORE_H

#include <stddef.h>
#include "base.h"

#define MAX_KEYS 64
#define MIN(a, b) ((a) < (b) ? (a) : (b))

typedef struct store store_t;

typedef enum { MODE_FAST, MODE_SLOW } mode_t;

/* First member is a `struct base`, which is C's inheritance idiom. */
struct store {
    struct base base;
    char *name;
    size_t len;
};

union payload {
    int i;
    double d;
};

enum level { LOW, HIGH };

char *store_find(store_t *s, const char *key);
void store_free(store_t *s);

#endif
