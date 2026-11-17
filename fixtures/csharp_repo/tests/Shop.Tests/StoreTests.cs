using Shop.Storage;
using Xunit;

namespace Shop.Tests;

public class StoreTests
{
    [Fact]
    public void GetsWhatWasPut()
    {
        Store store = new Store();
        store.Put("a", "b");
        Assert.Equal("b", store.Get("a"));
    }

    public void Fixture() { }
}
