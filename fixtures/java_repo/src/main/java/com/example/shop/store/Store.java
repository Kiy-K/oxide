package com.example.shop.store;

import java.util.HashMap;
import java.util.Map;

/** An in-memory store. */
@Cached
public class Store extends AbstractStore implements Backend {
    public static final int DEFAULT_LIMIT = 10;
    private final Map<String, Entry> entries = new HashMap<>();
    private final Backend fallback;

    public Store() {
        this(null);
    }

    public Store(Backend fallback) {
        this.fallback = fallback;
    }

    @Override
    public String get(Key key) {
        Entry entry = entries.get(normalize(key.value()));
        return entry == null ? fallback.get(key) : entry.value();
    }

    public String mustGet(Key key) {
        String found = this.get(key);
        if (found == null) {
            throw new IllegalStateException(key.value());
        }
        return found;
    }

    public void put(Key key, String value) {
        entries.put(key.value(), new Entry(value));
    }

    public void put(Key key, String value, Mode mode) {
        if (mode == Mode.READ) {
            return;
        }
        put(key, value);
    }

    static Store open(Mode mode) {
        Store store = new Store();
        store.put(new Key("mode"), mode.label());
        return store;
    }

    /** One stored value. */
    static final class Entry {
        private final String value;

        Entry(String value) {
            this.value = value;
        }

        String value() {
            return value;
        }
    }
}
