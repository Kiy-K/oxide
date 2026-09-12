package com.example.store;

public interface Backend extends AutoCloseable {
  String get(String key);

  String get(String key, String fallback);
}
