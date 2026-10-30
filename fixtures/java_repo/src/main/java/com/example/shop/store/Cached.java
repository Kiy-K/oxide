package com.example.shop.store;

public @interface Cached {
    int seconds() default 60;
}
