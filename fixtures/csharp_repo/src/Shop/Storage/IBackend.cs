namespace Shop.Storage;

public interface IBackend
{
    string? Get(string key);
    void Put(string key, string value);
    string GetOrDefault(string key, string fallback = "") => Get(key) ?? fallback;
}
