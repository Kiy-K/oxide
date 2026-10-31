#include <assert.h>

#include "shop/store.h"

static void test_get(void)
{
	Store *s = store_open(MODE_WRITE);
	assert(store_get(s, "missing") == NULL);
}

int main(void)
{
	test_get();
	return 0;
}
