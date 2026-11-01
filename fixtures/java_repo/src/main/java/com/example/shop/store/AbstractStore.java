package com.example.shop.store;

/** Shared storage helpers. */
public abstract class AbstractStore {
    protected int limit = 10;

    protected String normalize(String key) {
        return key.trim();
    }

    public abstract String get(Key key);
}
