"""SCIP descriptor parser per the documented grammar in scip.proto:
   namespace ::= <name> '/'      type ::= <name> '#'      term ::= <name> '.'
   meta ::= <name> ':'           method ::= <name> '(' <disambiguator> ')' '.'
   type-parameter ::= '[' <name> ']'   parameter ::= '(' <name> ')'
   <name> may be backtick-escaped."""
def descriptors(sym):
    parts = sym.split(' ', 4)
    if len(parts) < 5: return None
    t = parts[4]; out = []; i = 0
    while i < len(t):
        if t[i] == '[':
            j = t.index(']', i); out.append((t[i+1:j], 'typaram')); i = j+1; continue
        if t[i] == '(':
            j = t.index(')', i); out.append((t[i+1:j], 'param')); i = j+1; continue
        if t[i] == '`':
            j = t.index('`', i+1); name = t[i+1:j]; i = j+1
        else:
            j = i
            while j < len(t) and t[j] not in '#./:(': j += 1
            name = t[i:j]; i = j
        if i >= len(t): out.append((name, '?')); break
        if t[i] == '(':                       # method: name(disambig).
            j = t.index(')', i)
            out.append((name, 'method'))
            i = j + 1
            if i < len(t) and t[i] == '.': i += 1
        elif t[i] == '#': out.append((name, 'type')); i += 1
        elif t[i] == '.': out.append((name, 'term')); i += 1
        elif t[i] == '/': out.append((name, 'ns')); i += 1
        elif t[i] == ':': out.append((name, 'meta')); i += 1
        else: out.append((name, '?')); i += 1
    return out

def qname(sym):
    d = descriptors(sym)
    if not d: return None
    body = [n for n, s in d if s in ('type', 'method', 'term')]
    return '.'.join(body) if body else None

def is_callable(sym):
    d = descriptors(sym)
    return bool(d) and d[-1][1] == 'method'

if __name__ == '__main__':
    for s in ["scip-python python oxidepy 0.1.0 `oxidepy.auth`/AuthService#login().",
              "scip-typescript npm oxidets-fixture 0.1.0 src/net/`client.ts`/ApiClient#request().",
              "scip-typescript npm oxidets-fixture 0.1.0 src/net/`retry.ts`/ExponentialBackoff#`<constructor>`().(maxAttempts)",
              "scip-python python oxidepy 0.1.0 `oxidepy.retry`/RetryPolicy#max_attempts.",
              "scip-python python python-stdlib 3.11 json/loads()."]:
        print(f"{qname(s)!r:34s} callable={is_callable(s)!s:6s} {descriptors(s)}")
