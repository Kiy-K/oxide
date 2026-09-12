#include <vector>

namespace acme {

class Store {
public:
  Store(int capacity);
  ~Store();
  int get(int id) const;
  int get(const std::vector<int>& ids) const;
  bool operator==(const Store& other) const;
};

class MemoryStore : public Store {
};

class Qualified {
public:
  void get() &;
  void get() &&;
  void put() const;
  void put();
};

class Convert {
public:
  operator bool() const;
  operator int() const;
};

class Forward;
class Forward {
public:
  int value() const;
};

Store::Store(int capacity) {}
Store::~Store() {}

int Store::get(int id) const {
  return id;
}

int Store::get(const std::vector<int>& ids) const {
  return static_cast<int>(ids.size());
}

bool Store::operator==(const Store& other) const {
  return true;
}

} // namespace acme
