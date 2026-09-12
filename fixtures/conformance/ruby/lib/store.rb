require 'json'
require_relative 'base'
require_relative 'loggable'

module Acme
  class Store < Base
    include Loggable
    extend Enumerable

    VERSION = "1.0"
    DEFAULTS = { ttl: 60 }

    attr_accessor :name

    def initialize(name)
      @name = name
    end

    def find(key)
      log(key)
      JSON.parse(key)
    end

    def get(key)
      find(key)
    end

    alias fetch get

    def self.create(name)
      new(name)
    end

    class << self
      def reset
        create("default")
      end
    end
  end
end
