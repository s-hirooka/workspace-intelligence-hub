using System.Text.Json;
using Microsoft.CodeAnalysis.CSharp;
using Microsoft.CodeAnalysis.CSharp.Syntax;

if (args.Length != 1 || !File.Exists(args[0]))
{
    Console.Error.WriteLine("Expected one readable source file");
    return 2;
}
var file = Path.GetFullPath(args[0]);
var root = CSharpSyntaxTree.ParseText(File.ReadAllText(file)).GetCompilationUnitRoot();
var symbols = new List<object>();
void Add(string kind, string name, Microsoft.CodeAnalysis.SyntaxNode node, string? parent = null, object? metadata = null)
{
    var lines = node.GetLocation().GetLineSpan();
    symbols.Add(new { symbol_type = kind, symbol_name = name, parent_symbol = parent,
        start_line = lines.StartLinePosition.Line + 1, end_line = lines.EndLinePosition.Line + 1,
        metadata_json = metadata ?? new { } });
}
foreach (var n in root.DescendantNodes())
{
    var parent = n.Ancestors().OfType<TypeDeclarationSyntax>().FirstOrDefault()?.Identifier.Text;
    switch (n)
    {
        case BaseNamespaceDeclarationSyntax x: Add("namespace", x.Name.ToString(), x); break;
        case ClassDeclarationSyntax x: Add("class", x.Identifier.Text, x, parent,
            new { inheritance = x.BaseList?.Types.Select(t => t.Type.ToString()).ToArray() ?? [] }); break;
        case InterfaceDeclarationSyntax x: Add("interface", x.Identifier.Text, x, parent); break;
        case RecordDeclarationSyntax x: Add("record", x.Identifier.Text, x, parent); break;
        case EnumDeclarationSyntax x: Add("enum", x.Identifier.Text, x, parent); break;
        case MethodDeclarationSyntax x: Add("method", x.Identifier.Text, x, parent); break;
        case ConstructorDeclarationSyntax x: Add("constructor", x.Identifier.Text, x, parent); break;
        case PropertyDeclarationSyntax x: Add("property", x.Identifier.Text, x, parent); break;
        case FieldDeclarationSyntax x:
            foreach (var v in x.Declaration.Variables) Add("field", v.Identifier.Text, x, parent);
            break;
        case UsingDirectiveSyntax x: Add("using", x.Name?.ToString() ?? "", x); break;
        case InvocationExpressionSyntax x:
            // Only the called symbol is emitted. Argument text can contain credentials.
            var called = x.Expression switch {
                MemberAccessExpressionSyntax m => m.Name.Identifier.Text,
                IdentifierNameSyntax i => i.Identifier.Text,
                _ => "" };
            if (called.Length > 0) Add("method_call", called, x, parent);
            break;
    }
}
Console.Write(JsonSerializer.Serialize(new { file, symbols }));
return 0;
