// Roslyn oracle for OXIDE's C# slice (docs/csharp.md): compiles a source tree against the
// runtime's reference assemblies and prints, one JSON object per line, every invocation,
// object creation and type reference that binds to a declaration inside the tree.
// Usage: dotnet run -c Release -- <root> [DEFINE;DEFINE...] > edges.jsonl
using System.Text.Json;
using Microsoft.CodeAnalysis;
using Microsoft.CodeAnalysis.CSharp;
using Microsoft.CodeAnalysis.CSharp.Syntax;

var root = Path.GetFullPath(args[0]);
var files = Directory.EnumerateFiles(root, "*.cs", SearchOption.AllDirectories)
    .Where(f => !f.Split(Path.DirectorySeparatorChar).Any(p => p is "bin" or "obj" or ".git"))
    .OrderBy(f => f, StringComparer.Ordinal).ToList();
var defines = args.Length > 1 ? args[1].Split(';', StringSplitOptions.RemoveEmptyEntries) : [];
var options = CSharpParseOptions.Default.WithLanguageVersion(LanguageVersion.Latest).WithPreprocessorSymbols(defines);
var trees = files.Select(f => CSharpSyntaxTree.ParseText(File.ReadAllText(f), options, f)).ToList();
var runtime = ((string)AppContext.GetData("TRUSTED_PLATFORM_ASSEMBLIES")!).Split(Path.PathSeparator);
var refs = runtime.Select(p => MetadataReference.CreateFromFile(p));
var compilation = CSharpCompilation.Create("oracle", trees, refs,
    new CSharpCompilationOptions(OutputKind.DynamicallyLinkedLibrary, allowUnsafe: true));

object? Where(SyntaxTree tree, int position)
{
    var p = tree.GetLineSpan(new Microsoft.CodeAnalysis.Text.TextSpan(position, 0)).StartLinePosition;
    return new { file = Path.GetRelativePath(root, tree.FilePath).Replace('\\', '/'), line = p.Line, col = p.Character };
}

// Every in-tree location: a partial type has one per part.
object[]? Target(ISymbol? symbol)
{
    var at = symbol?.OriginalDefinition.Locations.Where(l => l.IsInSource && l.SourceTree is not null)
        .Select(l => Where(l.SourceTree!, l.SourceSpan.Start)).Cast<object>().ToArray();
    return at is { Length: > 0 } ? at : null;
}

var output = Console.Out;
foreach (var tree in trees)
{
    var model = compilation.GetSemanticModel(tree);
    foreach (var node in tree.GetRoot().DescendantNodes())
    {
        string? kind = null;
        ISymbol? symbol = null;
        switch (node)
        {
            case InvocationExpressionSyntax call:
                kind = "call";
                var method = model.GetSymbolInfo(call).Symbol as IMethodSymbol;
                symbol = method?.ReducedFrom ?? method;
                break;
            case BaseObjectCreationExpressionSyntax created:
                kind = "call";
                symbol = (model.GetSymbolInfo(created).Symbol as IMethodSymbol)?.ContainingType;
                break;
            case IdentifierNameSyntax id when id.Parent is not (InvocationExpressionSyntax or MemberAccessExpressionSyntax { Name: var _ } and { Parent: InvocationExpressionSyntax }):
                kind = "ref";
                symbol = model.GetSymbolInfo(id).Symbol as INamedTypeSymbol;
                break;
        }
        if (kind is null || Target(symbol) is not { } target) continue;
        output.WriteLine(JsonSerializer.Serialize(new { kind, from = Where(tree, node.SpanStart), to = target }));
    }
}
