package com.example.shop.store;

import static org.junit.jupiter.api.Assertions.assertEquals;

import org.junit.jupiter.api.Test;

class StoreTest {
    private Store fixture() {
        return Store.open(Mode.WRITE);
    }

    @Test
    void getsWhatWasPut() {
        Store store = fixture();
        store.put(new Key("a"), "1");
        assertEquals("1", store.mustGet(new Key("a")));
    }

    @org.junit.jupiter.api.Test
    void labelsWrites() {
        assertEquals("write:WRITE", Mode.WRITE.label());
    }
}
