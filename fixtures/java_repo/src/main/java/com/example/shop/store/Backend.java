package com.example.shop.store;

/** Where values come from. */
public interface Backend {
    String get(Key key);

    default String getOrDefault(Key key, String fallback) {
        String value = get(key);
        return value != null ? value : fallback;
    }
}
