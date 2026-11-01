package store_test

import (
	"testing"

	"example.com/shop/store"
)

func TestOpen(t *testing.T) {
	t.Run("read", func(t *testing.T) {
		if _, err := store.Open(store.ModeRead); err != nil {
			t.Fatal(err)
		}
	})
}

func Testable() {}
