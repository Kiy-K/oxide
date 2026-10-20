package store

import "errors"

const (
	ModeRead  = 1
	ModeWrite = 2
)

var (
	ErrMissing = errors.New("missing")
	ErrClosed  = errors.New("closed")
)

func Open(mode int) (*Store, error) {
	if mode == ModeWrite {
		return nil, ErrClosed
	}
	return New(), nil
}

func shadowed(New func() *Store) *Store {
	return New()
}
