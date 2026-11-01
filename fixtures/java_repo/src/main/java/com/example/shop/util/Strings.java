package com.example.shop.util;

import java.util.List;

public final class Strings {
    private Strings() {}

    public static String join(String... parts) {
        return String.join(",", parts);
    }

    public static String join(List<String> parts) {
        return join(parts.toArray(new String[0]));
    }
}
