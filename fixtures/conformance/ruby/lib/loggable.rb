require 'logger'

module Acme
  module Loggable
    def log(message)
      Logger.new($stdout).info(message)
    end
  end
end
