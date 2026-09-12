package com.example.store;

import java.util.List;
import java.util.Map;
import static java.util.Collections.emptyList;

@Service
public class Store extends Base implements Backend, Cloneable {

  public Store(Map<String, String> data) {
    this.data = data;
  }

  public Store() {
    this(Map.of());
  }

  @Override
  public String get(String key) {
    return find(key);
  }

  public String get(String key, String fallback) {
    return get(key);
  }

  public String get(byte[] raw) {
    return new String(raw);
  }

  public void put(final String key, @Nullable String value, int... flags) {
    emptyList();
  }

  public <T> List<T> all(List<T> in, java.util.Set<String> keys) {
    return in;
  }

  static class Inner {
    void ping() {}
  }

  enum Mode {
    FAST,
    SLOW
  }
}
