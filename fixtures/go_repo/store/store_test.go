package store

import "testing"

func helper() *Store { return New() }

func TestGet(t *testing.T) {
	s := helper()
	if _, ok := s.Get("x"); ok {
		t.Fatal("unexpected")
	}
}
