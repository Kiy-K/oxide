import com.sun.source.tree.*;
import com.sun.source.util.*;
import javax.tools.*;
import javax.lang.model.element.*;
import java.io.*;
import java.nio.file.*;
import java.util.*;
import java.util.regex.*;

public class Oracle {
  static Path root;
  static Trees trees;
  static SourcePositions positions;
  static final Map<String,String> uses = new TreeMap<>();
  static String rel(Path p) { return root.relativize(p.toAbsolutePath().normalize()).toString().replace('\\','/'); }
  static String key(Path p, long offset) { return rel(p) + ":" + offset; }

  static class Scan extends TreePathScanner<Void,Void> {
    CompilationUnitTree unit;
    Scan(CompilationUnitTree u) { unit=u; }
    Path file() { return Paths.get(unit.getSourceFile().toUri()); }
    long start(Tree t) { return positions.getStartPosition(unit,t); }
    long end(Tree t) { return positions.getEndPosition(unit,t); }
    String source(Tree t) {
      try { return Files.readString(file()); } catch(Exception e) { return ""; }
    }
    long lastIdentifier(Tree t) {
      long a=start(t), b=end(t); if(a<0||b<a) return -1;
      String s=source(t); int from=(int)Math.min(a,s.length()), to=(int)Math.min(b,s.length());
      Matcher m=Pattern.compile("[\\p{javaJavaIdentifierStart}][\\p{javaJavaIdentifierPart}]*").matcher(s.substring(from,to));
      long pos=-1; while(m.find()) pos=from+m.start(); return pos;
    }
    void record(Tree use, long offset) {
      if(offset<0) return;
      Element e=trees.getElement(new TreePath(getCurrentPath(),use));
      if(e==null) return;
      if(e.getKind()==ElementKind.CONSTRUCTOR) e=e.getEnclosingElement();
      TreePath decl=trees.getPath(e); if(decl==null) return;
      Path dp=Paths.get(decl.getCompilationUnit().getSourceFile().toUri()).toAbsolutePath().normalize();
      if(!dp.startsWith(root)) return;
      long ds=positions.getStartPosition(decl.getCompilationUnit(),decl.getLeaf());
      if(ds>=0) uses.put(key(file(),offset),key(dp,ds));
    }
    @Override public Void visitMethodInvocation(MethodInvocationTree n,Void x) {
      Tree s=n.getMethodSelect(); record(s,lastIdentifier(s)); return super.visitMethodInvocation(n,x);
    }
    @Override public Void visitNewClass(NewClassTree n,Void x) {
      record(n.getIdentifier(),lastIdentifier(n.getIdentifier())); return super.visitNewClass(n,x);
    }
    @Override public Void visitIdentifier(IdentifierTree n,Void x) {
      Element e=trees.getElement(getCurrentPath());
      if(e instanceof TypeElement) record(n,start(n)); return super.visitIdentifier(n,x);
    }
    @Override public Void visitMemberSelect(MemberSelectTree n,Void x) {
      Element e=trees.getElement(getCurrentPath());
      if(e instanceof TypeElement) record(n,lastIdentifier(n)); return super.visitMemberSelect(n,x);
    }
    @Override public Void visitAnnotation(AnnotationTree n,Void x) {
      record(n.getAnnotationType(),lastIdentifier(n.getAnnotationType())); return super.visitAnnotation(n,x);
    }
    @Override public Void visitImport(ImportTree n,Void x) {
      if(!n.getQualifiedIdentifier().toString().endsWith(".*"))
        record(n.getQualifiedIdentifier(),lastIdentifier(n.getQualifiedIdentifier()));
      return super.visitImport(n,x);
    }
  }
  public static void main(String[] args) throws Exception {
    root=Paths.get(args[0]).toRealPath(); Path output=Paths.get(args[1]);
    JavaCompiler c=ToolProvider.getSystemJavaCompiler(); if(c==null) throw new IllegalStateException("No system Java compiler");
    DiagnosticCollector<JavaFileObject> diagnostics=new DiagnosticCollector<>();
    StandardJavaFileManager fm=c.getStandardFileManager(diagnostics,null,null);
    List<Path> modules;
    try(var s=Files.walk(root)) { modules=s.filter(p->p.getFileName().toString().equals("pom.xml") && Files.isDirectory(p.getParent().resolve("src/main/java"))).map(Path::getParent).sorted().toList(); }
    List<String> errors=new ArrayList<>();
    String sourcepath=modules.stream().map(p->p.resolve("src/main/java").toString()).reduce((a,b)->a+File.pathSeparator+b).orElse("");
    for(Path module:modules) {
      List<Path> files=new ArrayList<>();
      for(String set:List.of("src/main/java","src/test/java")) {
        Path dir=module.resolve(set); if(Files.isDirectory(dir)) try(var s=Files.walk(dir)) {
          s.filter(p->p.toString().endsWith(".java")&&!p.getFileName().toString().equals("module-info.java")).forEach(files::add);
        }
      }
      if(files.isEmpty()) continue;
      Iterable<? extends JavaFileObject> units=fm.getJavaFileObjectsFromPaths(files);
      int diagStart=diagnostics.getDiagnostics().size();
      JavacTask task=(JavacTask)c.getTask(null,fm,diagnostics,List.of("-proc:none","-XDshould-stop.ifError=FLOW","-Xmaxerrs","100000","-sourcepath",sourcepath),null,units);
      List<CompilationUnitTree> parsed=new ArrayList<>(); task.parse().forEach(parsed::add);
      try { task.analyze(); } catch(Exception ignored) {}
      trees=Trees.instance(task); positions=trees.getSourcePositions();
      for(CompilationUnitTree u:parsed) new Scan(u).scan(u,null);
      long count=diagnostics.getDiagnostics().subList(diagStart,diagnostics.getDiagnostics().size()).stream().filter(d->d.getKind()==Diagnostic.Kind.ERROR).count();
      errors.add(module.toString()+"="+count);
    }
    StringBuilder out=new StringBuilder("{\"errors_per_module\":[");
    for(int i=0;i<errors.size();i++) { if(i>0)out.append(','); out.append('"').append(errors.get(i).replace("\\","/").replace("\"","\\\"")).append('"'); }
    out.append("],\"uses\":{"); int i=0;
    for(var e:uses.entrySet()) { if(i++>0)out.append(','); out.append('"').append(e.getKey()).append("\":\"").append(e.getValue()).append('"'); }
    out.append("}}\n"); Files.writeString(output,out.toString());
    System.out.println("modules="+errors.size()+" recorded_uses="+uses.size()+" output="+output);
  }
}
