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

func Open(mode int) error {
	var retries int
	const backoff = 2
	_ = retries * backoff
	if mode == ModeWrite {
		return ErrClosed
	}
	return nil
}
