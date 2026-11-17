using System.Collections.Generic;
using Shop.Util;
using Text = Shop.Util.Strings;

namespace Shop.Storage;

public class Store : AbstractStore<string>
{
    private readonly List<Key> keys = new();

    public Store() : this(0) { }

    public Store(int capacity) { }

    [Cached(Seconds = 5)]
    public override string? Get(string key)
    {
        var found = Items.TryGetValue(key, out var value) ? value : null;
        return found is null ? null : Strings.Trim(found);
    }

    protected override void Save(string key, string value)
    {
        Items[key] = Text.Trim(value);
        keys.Add(new Key(key, Mode.Write));
        Touch();
    }

    public void Put(string key, int value) => Put(key, value.ToString());

    private void Touch() { }

    public class Entry
    {
        public string Value => "";
    }
}
