import oxide, sys, tempfile, os, random, time, statistics, collections, json
out=[]
with tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR")) as d:
    for path in sys.argv[1:]:
        with oxide.open_repository(path, data_dir=d) as r:
            r.index(); v=r._view()
            keys=sorted(k for (k,) in v._query("MATCH (e:Entity) WHERE e.category='symbol' AND e.test=false RETURN e.key"))
            sample=random.Random(1).sample(keys, min(40,len(keys)))
            for direction in ("upstream","downstream"):
                ts=[];sizes=[];proven=[];stops=collections.Counter();unres=[]
                for k in sample:
                    t=time.perf_counter(); im=r.impact(k, direction=direction); ts.append(time.perf_counter()-t)
                    sizes.append(len(im.hops)); stops[im.stopped_by]+=1; unres.append(im.unresolved)
                    if im.hops: proven.append(sum(h.proven_depth is not None for h in im.hops)/len(im.hops))
                ts.sort()
                row=dict(corpus=path.rsplit('/',1)[-1][:30],direction=direction,p50_ms=round(1000*ts[len(ts)//2],1),p95_ms=round(1000*ts[int(len(ts)*.95)-1],1),
                    max_ms=round(1000*ts[-1],1),median_hops=statistics.median(sizes),empty=sum(s==0 for s in sizes),
                    proven_share=round(statistics.mean(proven),3) if proven else None,stops=dict(stops),median_unresolved=statistics.median(unres))
                print(json.dumps(row),flush=True); out.append(row)
