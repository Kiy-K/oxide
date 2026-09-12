module Acme
  # Abstract-ish base every backend inherits from.
  class Base
    STORAGE_ROOT = "/var/data"

    def find(key)
      raise NotImplementedError
    end
  end
end
