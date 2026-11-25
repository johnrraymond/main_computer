"""G1-0114: frozen reader complementarity and reconstruction forensics.
Uses the completed 0113 cache, reader checkpoints and saved test predictions.
No optimizer, backbone load, canonical mutation or test-label-based routing.
0113 test outcomes were already inspected: retrospective, not fresh certification.
"""
from __future__ import annotations
import argparse, collections as co, math, random
from pathlib import Path
import torch
from safetensors.torch import load_file
from tools import nanojev_gamer_v1_g1_multilayer_reconstruction_0113 as m
P=m.P; T=m.t; GH=m.GH
TAG='G1_0114_READER_FORENSICS'; SCHEMA='nanojev.gamer.g1.reader-forensics-0114.v1'
ARMS=tuple(m.ARMS); BASE='original_frozen'

def need(x,msg):
    if not x: raise ValueError(TAG+'_'+msg)
def emit(event,obj):
    print(TAG+'_'+event+' '+__import__('json').dumps(obj,sort_keys=True,allow_nan=False),flush=True)
read=m.read; write=m.write; sha=m.sha; digest=m.digest

def lineage(a):
    d=Path(a.source) if a.source else Path(a.root)/'g1-multilayer-reconstruction-0113'
    f=d/a.fit_name; seal=read(d/'cohort-seal.json'); cfg=read(f/'config.json'); selection=read(f/'selection.json'); report=read(f/'result.json')
    ss=sha(d/'cohort-seal.json'); cache_hash=sha(d/'cache-manifest.json')
    need(seal['schema']==cfg['schema']==report['schema']==m.SCHEMA,'PARENT_SCHEMA')
    need(cfg==report['config'] and selection==report['selection'] and cfg['seal_sha256']==ss and cfg['cache_manifest_sha256']==cache_hash,'PARENT_CONFIG_DRIFT')
    need(cfg['implementation_sha256']==sha(m.__file__) and cfg['arms']=={k:[v[0],list(v[1]),v[2]] for k,v in m.ARMS.items()},'PARENT_IMPLEMENTATION_DRIFT')
    need(selection['config_sha256']==digest(cfg) and set(selection['checkpoints'])==set(ARMS),'PARENT_SELECTION_DRIFT')
    need(report['test_used_for_training'] is False and report['test_used_for_selection'] is False and report['production_promotion'] is False,'PARENT_SCOPE')
    # JSON is key-sorted on disk; restore the original physical cache index order.
    seal['groups']={s:seal['groups'][s] for s in ('train','validation','test')}
    rows=seal['groups']; need(all({r['base_id'] for r in rows[x]}.isdisjoint({r['base_id'] for r in rows[y]}) for x in rows for y in rows if x!=y),'BASE_LEAKAGE')
    need(len({r['case_id'] for rs in rows.values() for r in rs})==sum(map(len,rows.values())),'DUPLICATE_CASES')
    paths,_,sig,_,lin=T.lineage(a.root,rows)
    need(sig==seal['frozen_g1'] and lin==seal['prior_0111'],'CANONICAL_LINEAGE')
    for arm in ARMS:
        ad=f/arm; tr=read(ad/'training.json'); ep=read(ad/'test.json'); ck=sha(ad/'selected.safetensors')
        need(tr==report['training'][arm] and tr['config_sha256']==digest(cfg) and ck==tr['checkpoint_sha256']==selection['checkpoints'][arm]==ep['checkpoint_sha256'],'CHECKPOINT_DRIFT_'+arm)
        need(ep['selection_sha256']==sha(f/'selection.json') and ep['cases_sha256']==digest(ep['cases']),'TEST_PREDICTION_DRIFT_'+arm)
        need([r['case_id'] for r in ep['cases']]==[r['case_id'] for r in rows['test']],'TEST_ORDER_'+arm)
        need(m.summarize(ep['cases'])==report['arms'][arm]['composite'],'TEST_SUMMARY_DRIFT_'+arm)
    _,data=m.cached_data(d,seal,ss)
    identity={'schema':SCHEMA,'parent_report_sha256':sha(f/'result.json'),'parent_seal_sha256':ss,'parent_cache_sha256':cache_hash,'parent_selection_sha256':sha(f/'selection.json'),'checkpoints':selection['checkpoints'],'implementation_sha256':sha(__file__)}
    return f,paths,data,report,identity

def gold(row):
    keys=[(*n['span'],n['kind']) for n in row['graph']['nodes'] if 'span' in n];need(len(keys)==len(set(keys)),'DUPLICATE_GOLD_SPAN');return set(keys)
def objects(seq):return {tuple(x) for x in seq}
def sorted_objects(seq):return [list(x) for x in sorted(seq)]
def avg(xs):return sum(xs)/max(len(xs),1)
def overlap(a,b):return a[0]<b[1] and b[0]<a[1]

def score_objects(raw,p,selected):
    """Native evidence for composite-selected objects, NOT composite posterior.
    Enumerate exact author-coordinate matches including whitespace aliases.
    No gold in this function. Return all valid token mappings for reconstruction.
    """
    logits=raw['span_logits'].detach().float().cpu(); valid=raw['span_valid'].detach().bool().cpu()
    need(bool(torch.isfinite(logits[valid]).all()),'NONFINITE_VALID_LOGITS')
    offsets=p['offsets']; start=p['prompt_start']; text=p['prompt']; wanted=objects(selected)
    coord=co.defaultdict(list)
    for k in wanted:coord[k[:2]].append(k)
    hits=co.defaultdict(list)
    for i,j in valid.nonzero(as_tuple=False).tolist():
        if i+j>=len(offsets):continue
        a,b=offsets[i][0]-start,offsets[i+j][1]-start
        if not 0<=a<b<=len(text):continue
        while a<b and text[a].isspace():a+=1
        while b>a and text[b-1].isspace():b-=1
        for k in coord.get((a,b),()):
            label=GH.SPAN_KINDS.index(k[2])+1; v=logits[i,j]; margin=float(v[label]-v[0]); competitor=float(v[label]-torch.cat((v[:label],v[label+1:])).max())
            hits[k].append((margin,float(v.softmax(0)[label]),competitor,i,i+j+1))
    out=[]
    for k in sorted(wanted):
        vals=hits[k];need(bool(vals),'COMPOSITE_OBJECT_NO_VALID_TOKEN_MAPPING_'+str(k))
        best=max(vals,key=lambda x:(x[0],x[1],-x[3],-x[4]))
        out.append({'object':list(k),'margin':best[0],'probability':best[1],'competitor_margin':best[2],'token_options':sorted({(x[3],x[4]) for x in vals})})
    return out

def capture_one(comp,reader,item,states,saved=None):
    _,p,_=item
    with torch.inference_mode():
        h=reader.hidden(states); raw=reader.head(h,eligible=p['eligible'].to(h.device),active_stage='G1')
        try: native=m.native_objects(raw,p); native_error=None
        except ValueError as e:
            if str(e)!='GAMER_GRAPH_TOO_MANY_PREDICTED_OBJECTS':raise
            native=[];native_error=str(e)
        if saved is None:
            import copy
            view=copy.copy(comp);view.head=reader.head
            try: pred=P.decoded(view,h,p,'unlabeled');error=None
            except ValueError as e:
                if str(e) not in ('G1_0082_UNMAPPABLE_CHAR_OBJECT','G1_0082_UNMAPPABLE_NONOVERLAP_TOKEN_SPANS','G1_0082_INVALID_SELECTED_OBJECT','G1_0082_OVERLAPPING_CHAR_OBJECTS','GAMER_GRAPH_TOO_MANY_PREDICTED_OBJECTS'):raise
                pred=[];error=str(e)
        else:
            need(objects(native)==objects(saved['native']['predicted']) and native_error==saved['native']['decode_error'],'NATIVE_REPLAY_DRIFT_'+item[0]['case_id'])
            pred=saved['composite']['predicted'];error=saved['composite']['decode_error']
        return {'selected':score_objects(raw,p,pred),'native':sorted_objects(objects(native)),'decode_error':error,'native_decode_error':native_error}

def evidence(a,folder,paths,data,out,identity,split):
    comp=None; all_evidence={}; checkpoint_ids=identity['checkpoints']; ed=out/'evidence'/split;ed.mkdir(parents=True,exist_ok=True)
    for arm in ARMS:
        ep=ed/(arm+'.json'); expected={'identity':identity,'split':split,'arm':arm}
        if ep.exists():
            doc=read(ep);need(doc['header']==expected and doc['records_sha256']==digest(doc['records']),'EVIDENCE_DRIFT_'+arm); records=doc['records']
        else:
            if comp is None:comp=P.native.load_composite(paths['composite'],device=a.device);comp.head.eval().requires_grad_(False)
            _,ls,kind=m.ARMS[arm];reader=m.Reader(comp.head,ls,kind).to(a.device)
            ck=folder/arm/'selected.safetensors';need(sha(ck)==checkpoint_ids[arm],'CHECKPOINT_CHANGED')
            reader.load_state_dict(load_file(str(ck),device='cpu'),strict=True);reader.eval().requires_grad_(False)
            saved=read(folder/arm/'test.json')['cases'] if split=='test' else None;records=[]
            for i,item in enumerate(data[split]):
                ev=capture_one(comp,reader,item,m.load_states(item,arm,a.device),saved[i]['decoders'] if saved is not None else None)
                records.append({'case_id':item[0]['case_id'],**ev})
                if (i+1)%32==0 or i+1==len(data[split]):emit('EVIDENCE_PROGRESS',{'split':split,'arm':arm,'done':i+1,'of':len(data[split])})
            need(all(not q.requires_grad and q.grad is None for q in reader.parameters()),'READER_MUTATION')
            write(ep,{'header':expected,'records_sha256':digest(records),'records':records});del reader
        need([r['case_id'] for r in records]==[x[0]['case_id'] for x in data[split]],'EVIDENCE_ORDER')
        all_evidence[arm]=records
    return [{arm:all_evidence[arm][i] for arm in ARMS} for i in range(len(data[split]))]

def candidate_table(ev):
    table={}; native={arm:objects(v['native']) for arm,v in ev.items()}
    for arm,v in ev.items():
        for s in v['selected']:
            k=tuple(s['object']);row=table.setdefault(k,{'support':{},'options':set()}); row['support'][arm]=s;row['options'].update(map(tuple,s['token_options']))
    for k,r in table.items():
        arms=list(r['support']);r['votes']=len(arms);r['families']=len({a.removeprefix('original_').removeprefix('S_') for a in arms});r['sources']=len({m.ARMS[a][0] for a in arms})
        r['native_votes']=sum(k in native[a] for a in ARMS);r['prob']=avg([s['probability'] for s in r['support'].values()]);r['margin']=avg([s['margin'] for s in r['support'].values()]);r['options']=sorted(r['options'])
    return table

def schedule(table,weights):
    """Maximum-weight compatible chain in both character and token coordinates.
    Multiple token mappings for one object are alternatives, never duplicates.
    """
    opts=sorted((k[1],t1,k[0],t0,k,float(w)) for k,w in weights.items() if w>0 for t0,t1 in table[k]['options'])
    dp=[];prev=[]
    for j,(end,te,start,ts,k,w) in enumerate(opts):
        best=0.;parent=-1
        for i in range(j):
            e,et=opts[i][:2]
            if e<=start and et<=ts and dp[i]>best+1e-10:best=dp[i];parent=i
        dp.append(best+w);prev.append(parent)
    chosen=[];i=max(range(len(dp)),key=lambda j:dp[j],default=-1)
    while i>=0:chosen.append(opts[i][4]);i=prev[i]
    need(len(chosen)==len(set(chosen)) and len(chosen)<=128,'INVALID_RECONSTRUCTION')
    return sorted(chosen)

def jaccard(a,b):return len(a&b)/max(len(a|b),1)
def policies(parent_winner):
    # Fixed non-trained grid; no labels influence individual predictions.
    spec={'parent_validation_winner':('arm',parent_winner),'original_frozen':('arm',BASE),'S_frozen':('arm','S_frozen'),'whole_consensus':('whole','consensus'),'whole_confidence':('whole','confidence'),'whole_combined':('whole','combined')}
    for arm in ARMS:spec['reader_'+arm]=('arm',arm)
    for subset in ('original','S','contextual'):
        for n in (2,3,4,5,6):spec[f'{subset}_subset_vote_{n}']=('subset',n,subset)
    for n in (1,2,3,4,5,6,7,8,9,10,12,14,16,17):spec[f'vote_{n}']=('vote',n)
    for n in (2,3,4,5,6,8):
        spec[f'architecture_{n}']=('architecture',n)
    for n in (1,2,3,4,6,8):
        spec[f'cross_source_{n}']=('cross',n)
        for q in (.5,.75,.9):spec[f'vote_{n}_or_conf_{q}']=('rescue',n,q)
    for n in (1,2,3,4,6,8):
        spec[f'vote_{n}_native_1']=('native',n,1)
    return spec

def reconstruct(ev,table,spec):
    sets={arm:objects([x['object'] for x in v['selected']]) for arm,v in ev.items()};kind=spec[0]
    if kind=='arm':return sorted(sets[spec[1]]),spec[1]
    if kind=='whole':
        def rank(arm):
            x=ev[arm]['selected'];agree=avg([jaccard(sets[arm],sets[b]) for b in ARMS if b!=arm]);confidence=avg([s['probability'] for s in x]);mode=spec[1]
            score=agree if mode=='consensus' else confidence if mode=='confidence' else (agree+confidence)/2
            return score,agree,confidence,-ARMS.index(arm)
        winner=max(ARMS,key=rank);return sorted(sets[winner]),winner
    weights={}
    for k,r in table.items():
        n=spec[1];v=r['votes'];p=r['prob'];nv=r['native_votes']
        if kind=='subset':
            supporting=[a for a in r['support'] if (a.startswith(spec[2]+'_') if spec[2]!='contextual' else a!='original_embedding')]
            v=len(supporting);p=avg([r['support'][a]['probability'] for a in supporting]);ok=v>=n
        elif kind=='vote':ok=v>=n
        elif kind=='architecture':ok=r['families']>=n
        elif kind=='cross':ok=v>=n and r['sources']==2
        elif kind=='rescue':ok=v>=n or (p>=spec[2] and nv>=1)
        elif kind=='native':ok=v>=n and nv>=spec[2]
        else:raise ValueError(kind)
        if ok:weights[k]=v+p+.1*nv/len(ARMS)
    return schedule(table,weights),None

def stats(rows,preds):
    counts=P.summarize([P.count(r,p) for r,p in zip(rows,preds)]); edges=co.Counter()
    for r,p in zip(rows,preds):edges.update(T.edge_availability(r,p))
    return counts|dict(edges)

def evaluate(rows,evs,specs):
    tables=[candidate_table(e) for e in evs];preds={};results={};routes={}
    for name,spec in specs.items():
        rec=[reconstruct(e,t,spec) for e,t in zip(evs,tables)]; pp=[x[0] for x in rec];preds[name]=pp;results[name]=stats(rows,pp);routes[name]=dict(co.Counter(x[1] for x in rec if x[1] is not None))
    return tables,preds,results,routes

def choose(results,names):
    return max(names,key=lambda n:(results[n]['core_exact'],results[n]['exact'],results[n]['f1'], -list(names).index(n)))

def auc(pairs):
    pos=sum(y for _,y in pairs);neg=len(pairs)-pos
    if not pos or not neg:return None
    wins=0.;neg_seen=0;bins=co.defaultdict(lambda:[0,0])
    for s,y in pairs:bins[s][int(y)]+=1
    for _,(n,p) in sorted(bins.items()):wins+=p*(neg_seen+n/2);neg_seen+=n
    return wins/(pos*neg)

def reliability(pairs):
    bins=co.defaultdict(lambda:[0,0]);
    for p,y in pairs:
        b=min(9,max(0,int(p*10)));bins[b][0]+=1;bins[b][1]+=int(y)
    return {'auc':auc(pairs),'bins':[{'lower':k/10,'upper':(k+1)/10,'count':n,'true':t,'precision':t/n} for k,(n,t) in sorted(bins.items())]}

def error_kind(k,g):
    if any(k[:2]==x[:2] for x in g):return 'same_boundary_wrong_type'
    if any(overlap(k,x) and k[2]==x[2] for x in g):return 'overlap_same_type_boundary'
    if any(overlap(k,x) for x in g):return 'overlap_other_type'
    return 'disjoint_extra'

def forensics(rows,evs,tables):
    summary={a:co.Counter() for a in ARMS};conf={a:[] for a in ARMS};margin={a:[] for a in ARMS};competitor={a:[] for a in ARMS};agreement=co.defaultdict(co.Counter);kind_agreement=co.defaultdict(co.Counter);by_kind=co.defaultdict(co.Counter);all_cases=[];oracles=co.Counter();pair=co.defaultdict(co.Counter);examples=[]
    for row,ev,tab in zip(rows,evs,tables):
        g=gold(row); sets={a:objects([s['object'] for s in ev[a]['selected']]) for a in ARMS};union=set(tab);core=[a for a in ARMS if g<=sets[a]];exact=[a for a in core if g==sets[a]]
        compatible_gold=set(schedule(tab,{k:1. for k in g&union}));oracles.update({'cases':1,'whole_reader_core':bool(core),'whole_reader_strict':bool(exact),'union_core':g<=union,'union_compatible_strict':compatible_gold==g,'union_gold_objects':len(g&union),'gold_objects':len(g),'union_extra_objects':len(union-g)})
        all_cases.append({'case_id':row['case_id'],'base_id':row['base_id'],'family':row['family'],'mode':row['mode'],'core_readers':core,'strict_readers':exact,'gold_unrecovered':sorted_objects(g-union),'gold_union_only':not core and g<=union,'gold_union_incompatible':sorted_objects((g&union)-compatible_gold)})
        for a in ARMS:
            s=sets[a];native=objects(ev[a]['native']);c=summary[a];c.update({'composite_only_true':len((s-native)&g),'composite_only_false':len((s-native)-g),'native_only_true':len((native-s)&g),'native_only_false':len((native-s)-g),'both_decoder_true':len(s&native&g),'both_decoder_false':len((s&native)-g)});c.update({'tp':len(s&g),'fp':len(s-g),'fn':len(g-s),'core':a in core,'strict':a in exact,'only_core_reader':core==[a],'only_strict_reader':exact==[a],'unique_true':sum(tab[k]['votes']==1 for k in s&g),'unique_false':sum(tab[k]['votes']==1 for k in s-g)})
            for rec in ev[a]['selected']:
                k=tuple(rec['object']);y=k in g;conf[a].append((rec['probability'],y));margin[a].append((rec['margin'],y));competitor[a].append((rec['competitor_margin'],y))
                if not y:c['fp_'+error_kind(k,g)]+=1
            for b in ARMS:
                if a!=b:pair[a+'|'+b].update({'true_only_a':len((s-sets[b])&g),'true_only_b':len((sets[b]-s)&g),'shared_true':len(s&sets[b]&g),'shared_false':len((s&sets[b])-g),'both_core':a in core and b in core,'either_core':a in core or b in core})
        for k,r in tab.items():
            tag='true' if k in g else 'false';agreement[r['votes']][tag]+=1;kind_agreement[(k[2],r['votes'])][tag]+=1;by_kind[k[2]][tag]+=1
            if k not in g:by_kind[k[2]][error_kind(k,g)]+=1
        for k in g-union:by_kind[k[2]]['missed_by_all']+=1
        # Bounded examples of distinct failure mechanisms, not inferred causes.
        for k in sorted(union-g):
            if len(examples)>=500:break
            r=tab[k];examples.append({'case_id':row['case_id'],'mode':row['mode'],'object':list(k),'text':row['prompt'][k[0]:k[1]],'classification':error_kind(k,g),'support':sorted(r['support']),'overlapping_gold':sorted_objects(x for x in g if overlap(k,x))})
    return {'oracle':dict(oracles),'reader':{a:dict(v)|{'native_probability_reliability':reliability(conf[a]),'margin_auc':auc(margin[a]),'competitor_margin_auc':auc(competitor[a])} for a,v in summary.items()},'agreement':{str(k):dict(v)|{'precision':v['true']/max(v['true']+v['false'],1)} for k,v in sorted(agreement.items())},'agreement_by_kind':{f'{k}|{n}':dict(v)|{'precision':v['true']/max(v['true']+v['false'],1)} for (k,n),v in sorted(kind_agreement.items())},'by_object_kind':{k:dict(v) for k,v in by_kind.items()},'pairwise':{k:dict(v) for k,v in pair.items()},'case_oracles':all_cases,'false_positive_examples':examples}

def paired(rows,a,b):
    clusters=co.defaultdict(list);gain=harm=eg=eh=0
    for r,x,y in zip(rows,a,b):
        u=P.count(r,x);v=P.count(r,y);gain+=u['core_exact']>v['core_exact'];harm+=u['core_exact']<v['core_exact'];eg+=u['exact']>v['exact'];eh+=u['exact']<v['exact'];clusters[r['base_id']].append(u['core_exact']-v['core_exact'])
    vals=[(sum(x),len(x)) for x in clusters.values()];rng=random.Random(11406);samples=[]
    for _ in range(2000):
        draw=rng.choices(vals,k=len(vals));samples.append(sum(x[0] for x in draw)/sum(x[1] for x in draw))
    samples.sort();return {'core_gain':gain,'core_harm':harm,'exact_gain':eg,'exact_harm':eh,'base_gain':sum(x[0]>0 for x in vals),'base_harm':sum(x[0]<0 for x in vals),'base_tie':sum(x[0]==0 for x in vals),'base_cluster_core_delta_95pct':[samples[50],samples[1949]]}

def run(a,folder,paths,data,parent,identity,out):
    need(out.resolve()!=folder.resolve() and out.resolve()!=folder.parent.resolve() and folder.resolve() not in out.resolve().parents and folder.parent.resolve() not in out.resolve().parents,'OUTPUT_MUST_NOT_MUTATE_PARENT');out.mkdir(parents=True,exist_ok=True); specs=policies(parent['selection']['winner']); lock={'identity':identity,'policies':{k:list(v) for k,v in specs.items()},'selection_criterion':['core_exact','exact','f1'],'retrospective_0113_test':True,'new_training':False}
    lp=out/'protocol.json'
    if lp.exists():need(read(lp)==lock,'PROTOCOL_DRIFT_USE_NEW_OUTPUT')
    else:write(lp,lock)
    val=evidence(a,folder,paths,data,out,identity,'validation');vr=[x[0] for x in data['validation']];vt,vp,vs,vroute=evaluate(vr,val,specs)
    # Verify selected validation checkpoints actually replay the original scores.
    for arm in ARMS:
        pp=[sorted_objects(objects([s['object'] for s in e[arm]['selected']])) for e in val];got=stats(vr,pp);expected=parent['training'][arm]['validation']
        need(all(got[k]==expected[k] for k in ('cases','tp','fp','fn','core_exact','exact','reachable_surface_edges')),'VALIDATION_REPLAY_DRIFT_'+arm)
    whole=[n for n,s in specs.items() if s[0] in ('arm','whole')]; obj=[n for n,s in specs.items() if s[0] not in ('arm','whole')]
    winners={'whole':choose(vs,whole),'object':choose(vs,obj),'overall':choose(vs,list(specs))}
    # No test evaluation until this selection and validation measurements are persisted.
    selection={'protocol_sha256':sha(lp),'winners':winners,'validation':vs};sp=out/'selection.json'
    if sp.exists():need(read(sp)==selection,'RECONSTRUCTION_SELECTION_DRIFT')
    else:write(sp,selection)
    emit('VALIDATION_SELECTED',{'policies':winners,'metrics':{k:vs[v] for k,v in winners.items()}})
    test=evidence(a,folder,paths,data,out,identity,'test');tr=[x[0] for x in data['test']];tt,tp,ts,route=evaluate(tr,test,specs)
    f=forensics(tr,test,tt);need(f['oracle']['whole_reader_core']==parent['oracle_core_ceiling'],'PARENT_ORACLE_DRIFT')
    vfor=forensics(vr,val,vt);write(out/'validation-forensics.json',vfor);write(out/'test-forensics.json',f)
    selected={};examples=[]
    for group,name in winners.items():
        preds=tp[name];selected[group]={'policy':name,'metrics':ts[name],'routes':route[name],'paired_vs_original':paired(tr,preds,tp['original_frozen']),'paired_vs_parent_validation_winner':paired(tr,preds,tp['parent_validation_winner']),'by_mode':{},'by_family':{}}
        for field in ('mode','family'):
            for value in sorted({r[field] for r in tr}):
                idx=[i for i,r in enumerate(tr) if r[field]==value];selected[group]['by_'+field][value]=stats([tr[i] for i in idx],[preds[i] for i in idx])
    for i,row in enumerate(tr):
        g=gold(row);pred=set(tp[winners['overall']][i]);examples.append({'case_id':row['case_id'],'base_id':row['base_id'],'mode':row['mode'],'predicted':sorted_objects(pred),'missed':sorted_objects(g-pred),'extra':sorted_objects(pred-g),'core_oracle_readers':f['case_oracles'][i]['core_readers']})
    write(out/'selected-case-level.json',examples)
    # Full object-level evidence, including missed-by-all targets; no oracle
    # columns are consumed by reconstruction.
    object_rows=[]
    for row,tab in zip(tr,tt):
        g=gold(row);object_rows.append({'case_id':row['case_id'],'mode':row['mode'],'prompt':row['prompt'],'objects':[{'object':list(k),'text':row['prompt'][k[0]:k[1]],'gold':k in g,'votes':v['votes'],'source_count':v['sources'],'architecture_count':v['families'],'native_votes':v['native_votes'],'mean_native_probability':v['prob'],'support':v['support'],'error_kind':None if k in g else error_kind(k,g)} for k,v in sorted(tab.items())],'missed_by_all':sorted_objects(g-set(tab))})
    write(out/'object-evidence.json',object_rows)
    report={'schema':SCHEMA,'identity':identity,'selection_sha256':sha(sp),'selected':selected,'validation':vs,'test_all_policies_retrospective':ts,'parent_reader_results':{k:v['composite'] for k,v in parent['arms'].items()},'oracle':f['oracle'],'agreement':f['agreement'],'reader_unique':{a:{k:v for k,v in x.items() if k not in ('native_probability_reliability',)} for a,x in f['reader'].items()},'test_forensics_sha256':sha(out/'test-forensics.json'),'validation_forensics_sha256':sha(out/'validation-forensics.json'),'case_level_sha256':sha(out/'selected-case-level.json'),'object_evidence_sha256':sha(out/'object-evidence.json'),'test_bases':len({r['base_id'] for r in tr}),'validation_bases':len({r['base_id'] for r in vr}),'backbone_loaded':False,'new_training':False,'test_labels_used_for_policy_selection':False,'test_previously_inspected':True,'production_promotion':False,'g3_unlocked':False}
    write(out/'result.json',report);emit('ORACLE',f['oracle']);emit('AGREEMENT',f['agreement']);emit('RESULT',{'selected':selected,'oracle':f['oracle']});emit('SAVED',{'report':str(out/'result.json'),'sha256':sha(out/'result.json'),'production_promotion':False})

def selftest():
    # Score actual head output; a synthetic fixture tests exact-coordinate and
    # competing token interval selection, confidence discrimination and oracle.
    torch.manual_seed(114);torch.set_num_threads(1)
    head=GH.GamerGraphHead(hidden_size=16,width=16,layers=1,max_span_tokens=4).eval().requires_grad_(False)
    p={'prompt':'Alpha 95 cost 70','prompt_start':0,'offsets':[(0,0),(0,5),(6,8),(9,13),(14,16),(0,0)],'eligible':torch.tensor([False,True,True,True,True,False])}
    with torch.inference_mode():raw=head(torch.randn(6,16),eligible=p['eligible'],active_stage='G1')
    selected=[[0,5,'action'],[6,8,'quantity'],[9,13,'outcome'],[14,16,'quantity']];sc=score_objects(raw,p,selected);need(len(sc)==4 and all(x['token_options'] for x in sc),'SELFTEST_SCORE')
    class FixtureComposite:
        def __init__(self,head):self.head=head
        def predict(self,h,p,*,family,native_stage):
            assert family=='unlabeled' and native_stage=='G1'
            q=self.head(h,eligible=p['eligible'],active_stage='G1');return {'token_spans':GH.decode_neural_spans(q['span_logits'],q['span_valid'])}
    reader=m.Reader(head,(28,),'frozen');h=torch.randn(6,16);item=({'case_id':'fixture'},p,None)
    first=capture_one(FixtureComposite(head),reader,item,[h]); saved={'native':{'predicted':first['native'],'decode_error':first['native_decode_error']},'composite':{'predicted':[x['object'] for x in first['selected']],'decode_error':first['decode_error']}}
    need(first==capture_one(None,reader,item,[h],saved),'SELFTEST_NATIVE_REPLAY')
    g={tuple(x) for x in selected}; row={'case_id':'fixture','base_id':'fixture','family':'allocation','mode':'control','prompt':p['prompt'],'graph':{'nodes':[{'id':str(i),'span':list(k[:2]),'kind':k[2]} for i,k in enumerate(sorted(g))],'edges':[{'src':'0','dst':'1','type':'applies_to'}]}}
    ev={a:{'selected':sc[:2] if i%2==0 else sc[2:],'native':selected[:2] if i%2==0 else selected[2:]} for i,a in enumerate(ARMS)}
    tab=candidate_table(ev);need(set(tab)==g,'SELFTEST_UNION');pr,_=reconstruct(ev,tab,('vote',2));need(set(pr)==g,'SELFTEST_COMPLEMENTARY_RECONSTRUCTION')
    x=(0,8,'other');tab[x]={'options':[(1,3)]};need(x not in schedule(tab,{k:2 for k in g}|{x:3}),'SELFTEST_INTERVAL_OPTIMALITY')
    tab2={(0,5,'action'):{'options':[(1,3)]},(6,8,'quantity'):{'options':[(2,3)]}};need(len(schedule(tab2,{k:1 for k in tab2}))==1,'SELFTEST_TOKEN_COLLISION')
    specs=policies(BASE);_,pred,res,_=evaluate([row],[ev],specs);need(res['vote_2']['exact']==1 and res['whole_consensus']['core_exact']==0,'SELFTEST_POLICY')
    f=forensics([row],[ev],[candidate_table(ev)]);need(f['oracle']['whole_reader_core']==0 and f['oracle']['union_core']==1,'SELFTEST_ORACLE');need(auc([(0.,False),(1.,True)])==1. and auc([(0.,False),(0.,True)])==.5,'SELFTEST_AUC')
    need(paired([row],pred['vote_2'],pred['whole_consensus'])['core_gain']==1,'SELFTEST_PAIRING')
    emit('SELFTEST',{'actual_G1_head_scores':True,'validation_and_saved_test_replay':True,'exact_coordinate_confidence':True,'all_policies':len(specs),'union_vs_whole_oracle':True,'compatible_interval_optimization':True,'token_overlap_rejected':True,'confidence_auc':True,'paired_cluster_statistics':True,'actual_checkpoint_cuda':False})

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('command',choices=('audit','selftest','run'),nargs='?',default='audit');p.add_argument('--root',default=m.ROOT);p.add_argument('--source');p.add_argument('--fit-name',default='fit');p.add_argument('--output');p.add_argument('--device',default='cuda');a=p.parse_args(argv)
    torch.set_num_threads(min(4,torch.get_num_threads()))
    if a.command=='selftest':selftest();return
    f,paths,data,parent,identity=lineage(a);out=Path(a.output) if a.output else Path(a.root)/'g1-reader-forensics-0114'
    emit('AUDIT',{'ready':True,'identity':identity,'cases':{k:len(v) for k,v in data.items()},'arms':len(ARMS),'policies':len(policies(parent['selection']['winner'])),'backbone_loaded':False,'new_training':False,'retrospective_test':True,'production_promotion':False})
    if a.command=='run':run(a,f,paths,data,parent,identity,out)
if __name__=='__main__':main()
