using Shop.Storage;
using static Shop.Util.Strings;

namespace Shop.Cart;

public class Cart
{
    private readonly IBackend backend;

    public Cart(IBackend backend) { this.backend = backend; }

    public void Add(string item)
    {
        backend.Put(Trim(item), item);
        Store store = new Store();
        store.Put(item, 1);
    }
}
