namespace Shop.Storage;

public record Key(string Name, Mode Mode)
{
    public override string ToString() => Name;
}
