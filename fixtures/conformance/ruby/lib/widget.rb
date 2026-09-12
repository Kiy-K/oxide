require_relative 'store'

# Declared with a scope-resolution name rather than nested `module` blocks.
class Acme::Widget
  def render
    Acme::Store.new("w").get(1)
  end
end
