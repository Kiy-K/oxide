import sys, scip_pb2
idx = scip_pb2.Index(); idx.ParseFromString(open(sys.argv[1],'rb').read())
enc=0; tot=0; roles={}
for d in idx.documents:
    for o in d.occurrences:
        tot+=1
        if o.HasField('single_line_enclosing_range') or o.HasField('multi_line_enclosing_range') or len(o.enclosing_range)>0: enc+=1
        r=o.symbol_roles
        for name,bit in [('Definition',1),('Import',2),('Write',4),('Read',8),('Generated',16),('Test',32),('Fwd',64)]:
            if r & bit: roles[name]=roles.get(name,0)+1
        if r==0: roles['(reference/none)']=roles.get('(reference/none)',0)+1
print(f"occurrences={tot} with_enclosing_range={enc}")
print("roles:", roles)
# kinds
kinds={}; disp=0; encsym=0; sigs=0; docs_lang={}
for d in idx.documents:
    docs_lang[d.language]=docs_lang.get(d.language,0)+1
    for s in d.symbols:
        k=scip_pb2.SymbolInformation.Kind.Name(s.kind); kinds[k]=kinds.get(k,0)+1
        if s.display_name: disp+=1
        if s.enclosing_symbol: encsym+=1
        if s.HasField('signature_documentation'): sigs+=1
print("kinds:", kinds)
print(f"display_name set={disp} enclosing_symbol set={encsym} signature_doc={sigs} doc.language={docs_lang}")
