//go:build windows

package store

func dataPath() string { return `C:\shop` }

func init() {}

func init() { _ = dataPath() }
