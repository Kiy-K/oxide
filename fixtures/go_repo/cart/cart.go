package cart

import (
	"example.com/shop/internal/util"
	st "example.com/shop/store"
	"example.com/shop/tools"
)

// Cart embeds the store, so Get is promoted.
type Cart struct {
	*st.Store
	items []st.Key
}

type Totaler interface {
	st.Resource
	Total() int
}

func NewCart() *Cart {
	return &Cart{Store: st.New()}
}

func (c *Cart) Add(k st.Key) {
	c.items = append(c.items, k)
	c.Get(k)
}

func (c *Cart) Names() []string {
	tools.Generate()
	return util.Map[st.Key, string](c.items, func(k st.Key) string { return string(k) })
}
