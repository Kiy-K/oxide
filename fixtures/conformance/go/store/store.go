package store

import (
	"fmt"
	"strings"
)

const DefaultLimit = 10

var DefaultStore = New()

type Key string

type Resource interface {
	ID() uint32
}

type Backend interface {
	Resource
	Get(key Key) (string, bool)
}

type Base struct {
	name string
}

type Store struct {
	Base
	entries map[Key]string
}

func New() *Store {
	return &Store{entries: map[Key]string{}}
}

func (s *Store) Get(key Key) (string, bool) {
	v, ok := s.entries[key]
	return v, ok
}

func (s *Store) String() string {
	return strings.Join([]string{s.name}, ",")
}

func (b Base) String() string {
	return fmt.Sprintf("%s", b.name)
}
