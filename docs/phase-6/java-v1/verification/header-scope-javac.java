import javax.tools.*;
public class Probe {
    public static void main(String[] a) {
        JavaCompiler c = ToolProvider.getSystemJavaCompiler();
        for (String f : a) {
            System.out.println("== " + f);
            c.run(null, System.out, System.out, "-proc:none", "-d", "out", f);
        }
    }
}
