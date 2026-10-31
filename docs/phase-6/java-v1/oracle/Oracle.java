// javac attribution oracle for the OXIDE Java v1 evaluation (parse + analyze only; no class files).
// Usage: java Oracle.java GSON_ROOT SHADOW_DIR OUT.json MODULE...
// SHADOW_DIR receives one directory per module holding a symlink to each top-level package directory
// of its src/main/java, so the -sourcepath never sees module-info.java (which would switch javac to
// module mode and hide the absent test dependencies differently per module).
import com.sun.source.tree.*;
import com.sun.source.util.*;
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.util.*;
import java.util.stream.*;
import javax.lang.model.element.*;
import javax.lang.model.type.TypeKind;
import javax.tools.*;

public class Oracle {
  static Path root;
  static final StringBuilder out = new StringBuilder();
  static boolean firstFile = true;

  public static void main(String[] args) throws Exception {
    root = Path.of(args[0]).toRealPath();
    Path shadow = Path.of(args[1]);
    List<String> modules = Arrays.asList(args).subList(3, args.length);
    List<String> sourcepath = new ArrayList<>();
    for (String m : modules) {
      for (String sub : List.of("src/main/java", "src/main/java-templates")) {
        Path src = root.resolve(m).resolve(sub);
        if (!Files.isDirectory(src)) continue;
        Path dir = shadow.resolve(m + "-" + sub.replace('/', '_'));
        Files.createDirectories(dir);
        try (Stream<Path> top = Files.list(src)) {
          for (Path p : top.filter(Files::isDirectory).toList()) {
            Path link = dir.resolve(p.getFileName().toString());
            if (!Files.exists(link, LinkOption.NOFOLLOW_LINKS)) Files.createSymbolicLink(link, p);
          }
        }
        sourcepath.add(dir.toString());
      }
    }
    JavaCompiler javac = ToolProvider.getSystemJavaCompiler();
    out.append("{\"javac\": \"").append(System.getProperty("java.version")).append("\", \"modules\": {");
    StringBuilder uses = new StringBuilder();
    boolean firstModule = true;
    for (String m : modules) {
      List<File> units = new ArrayList<>();
      for (String sub : List.of("src/main/java", "src/test/java")) {
        Path src = root.resolve(m).resolve(sub);
        if (!Files.isDirectory(src)) continue;
        try (Stream<Path> walk = Files.walk(src)) {
          walk.filter(p -> p.toString().endsWith(".java"))
              .filter(p -> !p.getFileName().toString().equals("module-info.java"))
              .sorted().forEach(p -> units.add(p.toFile()));
        }
      }
      if (units.isEmpty()) continue;
      // This module's own sources first, so a duplicated class name resolves inside the module.
      List<String> sp = new ArrayList<>(sourcepath);
      sp.sort(Comparator.comparing(s -> Path.of(s).getFileName().toString().startsWith(m + "-") ? 0 : 1));
      List<String> options = List.of(
          "-proc:none", "-implicit:none", "-XDshould-stop.ifError=FLOW", "-Xmaxerrs", "1000000",
          "-encoding", "UTF-8", "-sourcepath", String.join(File.pathSeparator, sp));
      DiagnosticCollector<JavaFileObject> diags = new DiagnosticCollector<>();
      StandardJavaFileManager fm = javac.getStandardFileManager(diags, null, StandardCharsets.UTF_8);
      JavacTask task = (JavacTask) javac.getTask(null, fm, diags, options, null, fm.getJavaFileObjectsFromFiles(units));
      Iterable<? extends CompilationUnitTree> cus = task.parse();
      task.analyze();
      long errors = diags.getDiagnostics().stream().filter(d -> d.getKind() == Diagnostic.Kind.ERROR).count();
      System.err.println(m + ": units=" + units.size() + " errors=" + errors);
      out.append(firstModule ? "" : ", ").append(q(m)).append(": {\"units\": ").append(units.size())
          .append(", \"errors\": ").append(errors).append("}");
      firstModule = false;
      Trees trees = Trees.instance(task);
      for (CompilationUnitTree cu : cus) {
        Scanner s = new Scanner(trees, cu);
        s.scan(cu, null);
        uses.append(firstFile ? "" : ",\n").append(q(rel(cu))).append(": [").append(s.rows).append("]");
        firstFile = false;
      }
      fm.close();
    }
    out.append("}, \"uses\": {\n").append(uses).append("}}\n");
    Files.writeString(Path.of(args[2]), out);
  }

  static String rel(CompilationUnitTree cu) {
    try {
      Path p = Path.of(cu.getSourceFile().toUri()).toRealPath();
      return p.startsWith(root) ? root.relativize(p).toString() : null;
    } catch (IOException e) {
      return null;
    }
  }

  static String q(String s) {
    if (s == null) return "null";
    StringBuilder b = new StringBuilder("\"");
    for (char c : s.toCharArray()) {
      if (c == '"' || c == '\\') b.append('\\').append(c);
      else if (c < 0x20) b.append(String.format("\\u%04x", (int) c));
      else b.append(c);
    }
    return b.append('"').toString();
  }

  static final class Scanner extends TreePathScanner<Void, Void> {
    final Trees trees;
    final CompilationUnitTree cu;
    final SourcePositions pos;
    final StringBuilder rows = new StringBuilder();
    final Set<Long> seen = new HashSet<>();

    Scanner(Trees trees, CompilationUnitTree cu) {
      this.trees = trees;
      this.cu = cu;
      this.pos = trees.getSourcePositions();
    }

    @Override
    public Void visitIdentifier(IdentifierTree t, Void v) {
      record(t.getName().toString(), pos.getStartPosition(cu, t));
      return super.visitIdentifier(t, v);
    }

    @Override
    public Void visitMemberSelect(MemberSelectTree t, Void v) {
      String name = t.getIdentifier().toString();
      record(name, pos.getEndPosition(cu, t) - name.length());
      return super.visitMemberSelect(t, v);
    }

    void record(String name, long at) {
      if (at < 0 || name.equals("this") || name.equals("super") || name.equals("class") || name.equals("*")) return;
      if (!seen.add(at)) return;
      Element e = trees.getElement(getCurrentPath());
      List<Element> targets = new ArrayList<>();
      String status;
      if (e == null && inImport()) {
        targets = staticImport();
        status = targets.isEmpty() ? "null" : "import";
      } else if (e == null) {
        status = "null";
      } else if (e.getKind() == ElementKind.PACKAGE || e.getKind() == ElementKind.MODULE) {
        return;
      } else if (e.asType().getKind() == TypeKind.ERROR || e.getKind() == ElementKind.OTHER) {
        status = "error";
      } else {
        targets.add(e);
        status = "ok";
      }
      StringBuilder decls = new StringBuilder();
      for (Element d : targets) {
        TreePath p = trees.getPath(d);
        if (p == null) {
          if (status.equals("ok")) status = "external";
          continue;
        }
        String file = rel(p.getCompilationUnit());
        if (file == null) {
          if (status.equals("ok")) status = "external";
          continue;
        }
        long start = trees.getSourcePositions().getStartPosition(p.getCompilationUnit(), p.getLeaf());
        decls.append(decls.length() == 0 ? "" : ", ").append("[").append(q(file)).append(", ").append(start).append("]");
      }
      String kind = e == null ? (targets.isEmpty() ? "NONE" : targets.get(0).getKind().toString()) : e.getKind().toString();
      rows.append(rows.length() == 0 ? "" : ", ").append("[").append(at).append(", ").append(q(name)).append(", ")
          .append(q(kind)).append(", ").append(q(status)).append(", [").append(decls).append("]]");
    }

    boolean inImport() {
      for (Tree t : getCurrentPath()) if (t instanceof ImportTree) return true;
      return false;
    }

    // javac leaves the last segment of `import static a.B.m;` unattributed: resolve m among B's members.
    List<Element> staticImport() {
      List<Element> found = new ArrayList<>();
      if (!(getCurrentPath().getLeaf() instanceof MemberSelectTree ms)) return found;
      Element owner = trees.getElement(new TreePath(getCurrentPath(), ms.getExpression()));
      if (owner == null || !(owner instanceof TypeElement type)) return found;
      for (Element m : type.getEnclosedElements())
        if (m.getSimpleName().contentEquals(ms.getIdentifier())) found.add(m);
      return found;
    }
  }
}
