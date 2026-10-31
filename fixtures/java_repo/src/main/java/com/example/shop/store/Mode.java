package com.example.shop.store;

public enum Mode {
    READ("r"),
    WRITE("w") {
        @Override
        String label() {
            return "write:" + name();
        }
    };

    private final String code;

    Mode(String code) {
        this.code = code;
    }

    String label() {
        return code;
    }
}
