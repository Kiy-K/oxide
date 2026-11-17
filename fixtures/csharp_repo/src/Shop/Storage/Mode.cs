namespace Shop.Storage
{
    public enum Mode
    {
        Read,
        Write,
    }

    [AttributeUsage(AttributeTargets.Method)]
    public sealed class CachedAttribute : Attribute
    {
        public int Seconds { get; init; }
    }
}
