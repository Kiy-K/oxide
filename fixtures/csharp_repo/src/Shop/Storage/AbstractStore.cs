using System.Collections.Generic;

namespace Shop.Storage;

public abstract partial class AbstractStore<T> : IBackend
{
    protected readonly Dictionary<string, T> Items = new();

    public abstract string? Get(string key);

    public void Put(string key, string value) => Save(key, value);

    protected abstract void Save(string key, string value);
}
