#include <stdio.h>

#include "cart.h"

int main(void)
{
	struct Cart cart = {0};
	cart.store = store_open(MODE_READ);
	cart_add(&cart, "apple");
	printf("%s %s\n", cart_first(&cart), platform_path());
	return 0;
}
