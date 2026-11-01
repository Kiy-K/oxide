package com.example.shop.store;

public record Key(String value) {
    public Key {
        value = value.trim();
    }

    public static Key of(String value) {
        return new Key(value);
    }
}
