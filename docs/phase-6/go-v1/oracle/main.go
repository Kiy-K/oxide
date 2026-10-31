package main

import (
	"encoding/json"
	"fmt"
	"go/token"
	"go/types"
	"os"
	"path/filepath"

	"golang.org/x/tools/go/packages"
)

type position struct {
	File   string `json:"file"`
	Offset int    `json:"offset"`
}

type use struct {
	Use       position `json:"use"`
	Definition position `json:"definition"`
}

func main() {
	if len(os.Args) != 3 {
		panic("usage: oracle ROOT OUTPUT.json")
	}
	root, err := filepath.Abs(os.Args[1])
	if err != nil { panic(err) }
	fset := token.NewFileSet()
	cfg := &packages.Config{
		Mode: packages.NeedName | packages.NeedFiles | packages.NeedCompiledGoFiles |
			packages.NeedSyntax | packages.NeedTypes | packages.NeedTypesInfo | packages.NeedDeps,
		Fset: fset, Dir: root, Tests: true,
	}
	pkgs, err := packages.Load(cfg, "./...")
	if err != nil { panic(err) }
	var rows []use
	seen := map[[4]any]bool{}
	var walk func(*packages.Package)
	visited := map[*packages.Package]bool{}
	walk = func(pkg *packages.Package) {
		if pkg == nil || visited[pkg] { return }; visited[pkg] = true
		for ident, obj := range pkg.TypesInfo.Uses {
			if obj == nil || !obj.Pos().IsValid() { continue }
			uf, df := fset.File(ident.Pos()), fset.File(obj.Pos())
			if uf == nil || df == nil { continue }
			up, dp := uf.Name(), df.Name()
			if !filepath.IsAbs(up) || !filepath.IsAbs(dp) { continue }
			up, _ = filepath.Rel(root, up); dp, _ = filepath.Rel(root, dp)
			r := use{Use: position{up, uf.Offset(ident.Pos())}, Definition: position{dp, df.Offset(obj.Pos())}}
			key := [4]any{r.Use.File, r.Use.Offset, r.Definition.File, r.Definition.Offset}
			if !seen[key] { seen[key] = true; rows = append(rows, r) }
		}
		for _, imp := range pkg.Imports { walk(imp) }
		for _, variant := range pkg.Syntax { _ = variant }
		for _, variant := range pkg.Errors { fmt.Fprintf(os.Stderr, "%s: %s\n", pkg.PkgPath, variant.Msg) }
		for _, dep := range pkg.Imports { walk(dep) }
	}
	for _, pkg := range pkgs { walk(pkg) }
	out, err := os.Create(os.Args[2]); if err != nil { panic(err) }; defer out.Close()
	enc := json.NewEncoder(out); enc.SetIndent("", "  "); if err := enc.Encode(rows); err != nil { panic(err) }
	_ = types.Unalias
}
