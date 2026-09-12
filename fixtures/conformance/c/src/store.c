#include <stdio.h>
#include <stdlib.h>
#include "store.h"

static int retry_count = 3;
const char *const STORAGE_ROOT = "/var/data";

static void log_miss(const char *key);

char *store_find(store_t *s, const char *key)
{
    if (s->len == 0) {
        log_miss(key);
        return NULL;
    }
    return s->name;
}

static void log_miss(const char *key)
{
    fprintf(stderr, "miss %s\n", key);
}

void store_free(store_t *s)
{
    free(s);
}

int main(void)
{
    store_t *s = malloc(sizeof(store_t));
    store_find(s, "k");
    store_free(s);
    return MIN(retry_count, 1);
}
