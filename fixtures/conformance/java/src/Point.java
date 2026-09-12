package com.example.store;

public record Point(int x, int y) implements Comparable<Point> {
  public Point {
    if (x < 0) {
      throw new IllegalArgumentException("x");
    }
  }

  @Override
  public int compareTo(Point other) {
    return Integer.compare(x, other.x);
  }
}
