package main

import (
	"fmt"

	"example.com/shop/cart"
)

func main() {
	c := cart.NewCart()
	c.Add("apple")
	fmt.Println(c.Names())
}
