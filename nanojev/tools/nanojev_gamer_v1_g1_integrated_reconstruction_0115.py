"""G1-0115 integrated forensic, reconstruction, reduction and blind G1/G2 laboratory.
Research only. Run executes separate develop, prepare, predict and score processes.
Old 0113 validation/test are exposed development; fresh answers are not read by predict.
"""
from __future__ import annotations
import argparse, collections as co, hashlib, json, math, random, subprocess, sys, time
from pathlib import Path
from types import SimpleNamespace
import torch
from safetensors.torch import load_file
from tools import nanojev_gamer_v1_g1_reader_forensics_0114 as f
m=f.m; P=f.P; T=f.T; GH=f.GH; A=P.atlas; C=m.C; B=m.B
ARMS=f.ARMS; BASE=f.BASE; SCHEMA='nanojev.gamer.g1.integrated-reconstruction-0115.v1'; TAG='G1_0115_INTEGRATED'
MODULE='tools.nanojev_gamer_v1_g1_integrated_reconstruction_0115'
read,write,sha,digest=f.read,f.write,f.sha,f.digest
TARGETS={'precision':.995,'recall':.995,'core_rate':.99,'exact_rate':.95,'edge_reachability':.999}
MODES=tuple(P.TEST_MODES)+('newline','numformat','rename','sentence_split','spacing','suffix')
SEED=1153026; ALT_SEED=1154026
PRE_ENDPOINT_FIX_SHA256="2ed0a4c13ebf2957cb704903c10159fccc081589353f97b4411a7103e09beb6f"

def need(ok,msg):
    if not ok:raise ValueError(TAG+'_'+msg)
def emit(event,x):print(TAG+'_'+event+' '+json.dumps(x,sort_keys=True,allow_nan=False),flush=True)
def immutable(path,obj):
    if path.exists():need(read(path)==obj,'SEALED_DRIFT_'+path.name)
    else:write(path,obj)
def output(a):
    root=Path(a.root).resolve(); out=Path(a.output).resolve() if a.output else root/'g1-integrated-reconstruction-0115'
    need(out!=root and out not in root.parents and (root not in out.parents or (out.parent==root and out.name.startswith('g1-integrated-reconstruction-0115'))),'UNSAFE_OUTPUT')
    return out

def parent_paths(a):
    root=Path(a.root);return root/'g1-multilayer-reconstruction-0113'/'fit',root/'g1-reader-forensics-0114'

def check_parent(a):
    folder,old=parent_paths(a);r=read(old/'result.json');ident=r['identity']
    need(r['schema']==f.SCHEMA and r['new_training'] is False and r['production_promotion'] is False and r['test_previously_inspected'] is True,'PARENT_SCOPE')
    need(ident['implementation_sha256']==sha(f.__file__) and ident['parent_report_sha256']==sha(folder/'result.json') and ident['parent_selection_sha256']==sha(folder/'selection.json') and ident['parent_seal_sha256']==sha(folder.parent/'cohort-seal.json') and ident['parent_cache_sha256']==sha(folder.parent/'cache-manifest.json'),'PARENT_LINEAGE')
    need(set(ident['checkpoints'])==set(ARMS),'PARENT_ARMS')
    for arm,h in ident['checkpoints'].items():need(sha(folder/arm/'selected.safetensors')==h,'CHECKPOINT_DRIFT_'+arm)
    for name,key in [('selection.json','selection_sha256'),('selected-case-level.json','case_level_sha256'),('object-evidence.json','object_evidence_sha256'),('test-forensics.json','test_forensics_sha256'),('validation-forensics.json','validation_forensics_sha256')]:need(sha(old/name)==r[key],'PARENT_REPORT_DRIFT_'+name)
    paths,_,sig,_=P.source(a.root)
    # Explicit historical G2 comparator, not the latest ensemble or a certified G2.
    g2=paths['parent_0087']/'training'; rec,_=P.hard._verify_trained(paths,paths['parent_0087'])
    need(rec['head_sha256']==sha(g2/C.HEAD_FILE),'G2_CHECKPOINT_DRIFT')
    ident2={'schema':SCHEMA,'implementation_sha256':sha(__file__),'parent_0114_sha256':sha(old/'result.json'),'parent_0114_identity':ident,'g2_0087_head_sha256':rec['head_sha256'],'g2_manifest_sha256':sha(g2/C.MANIFEST),'frozen_g1':sig,'sources':{Path(z.__file__).name:sha(z.__file__) for z in (m,T,P,A,C,GH,B,P.fuzz())}}
    return folder,old,r,paths,g2,ident2

def sealed_parent(out,parent):
    """Reuse only the exact pre-fix development identity; do not rewrite its seal.

    The compatibility exception is limited to the known endpoint-format repair.
    Prediction shards and their lock separately bind the executing source hash.
    """
    current=parent[-1]; lock=out/'development'/'selection.json'
    if not lock.exists():return parent
    previous=read(lock)['identity']
    if previous==current:return parent
    expected=dict(current,implementation_sha256=PRE_ENDPOINT_FIX_SHA256)
    need(previous==expected,'PRE_FIX_IDENTITY_MISMATCH')
    return (*parent[:-1],previous)

def load_development(a,folder,old,r):
    seal=read(folder.parent/'cohort-seal.json'); result={}
    for split in ('validation','test'):
        rows=seal['groups'][split]; ev={}
        for arm in ARMS:
            x=read(old/'evidence'/split/(arm+'.json'));need(x['header']=={'identity':r['identity'],'split':split,'arm':arm} and x['records_sha256']==digest(x['records']),'EVIDENCE_DRIFT')
            need([i['case_id'] for i in x['records']]==[i['case_id'] for i in rows],'EVIDENCE_ORDER');ev[arm]=x['records']
        result[split]=[(row,{arm:ev[arm][i] for arm in ARMS}) for i,row in enumerate(rows)]
    # Replay the published selected policy before any new analysis.
    spec=f.policies(read(folder/'selection.json')['winner'])[r['selected']['overall']['policy']]
    for split,pairs in result.items():
        pred=[f.reconstruct(e,f.candidate_table(e),spec)[0] for row,e in pairs]
        expect=r['selected']['overall']['metrics'] if split=='test' else r['validation'][r['selected']['overall']['policy']]
        got=f.stats([row for row,e in pairs],pred);need(got==expect,'PARENT_REPLAY_'+split)
    return result,spec

def normalize_features(prompt,table,k):
    """Gold-free numerical evidence; no case IDs, family, mode, or annotation access."""
    r=table[k]; a,b,kind=k; s=prompt[a:b]; supports=list(r['support'].values()); n=max(1,r['n_readers']); v=r['votes']/n
    competitors=[x for x in table if x!=k and f.overlap(k,x)]; best=max((table[x]['votes']/n for x in competitors),default=0.)
    same=max((table[x]['votes']/n for x in competitors if x[2]==kind),default=0.)
    av=lambda key:f.avg([float(x[key]) for x in supports])
    # Bound extreme logits; retain directional information and agreement interactions.
    clip=lambda x:math.tanh(x/8.)
    return [v,r['families']/max(r['n_families'],1),r['sources']/2,r['native_votes']/n,best,v-best,same,v-same,
            av('probability'),max(x['probability'] for x in supports),min(x['probability'] for x in supports),clip(av('margin')),clip(av('competitor_margin')),clip(max(x['margin'] for x in supports)),
            *[float(kind==z) for z in GH.SPAN_KINDS],a/max(len(prompt),1),b/max(len(prompt),1),math.log1p(b-a),math.log1p(len(s.split())),float(s.isupper()),float(any(c.isdigit() for c in s)),float(any(c.isalpha() for c in s)),float(any(c in '\n;:,.()' for c in s)),float(a==0 or prompt[a-1].isspace()),float(b==len(prompt) or prompt[b:b+1].isspace()),
            *[float(z in r['support']) for z in ARMS]]

def make_table(ev,arms):
    need(set(arms)<=set(ev) and len(arms)>0,'BAD_READER_SUBSET')
    tab={};natives={a:f.objects(ev[a]['native']) for a in arms}; nf=len({a.removeprefix('original_').removeprefix('S_') for a in arms})
    for arm in arms:
        for s in ev[arm]['selected']:
            k=tuple(s['object']);r=tab.setdefault(k,{'support':{},'options':set()});r['support'][arm]=s;r['options'].update(map(tuple,s['token_options']))
    for k,r in tab.items():
        ss=r['support'];r.update(votes=len(ss),families=len({a.removeprefix('original_').removeprefix('S_') for a in ss}),sources=len({m.ARMS[a][0] for a in ss}),native_votes=sum(k in natives[a] for a in arms),prob=f.avg([v['probability'] for v in ss.values()]),margin=f.avg([v['margin'] for v in ss.values()]),n_readers=len(arms),n_families=nf,options=sorted(r['options']))
    return tab

def weights(prompt,tab,spec):
    out={}; model=spec.get('model'); learned=model is not None
    probs={}
    if spec['rule']=='whole':
        arms=spec['arms'];sets={a:{k for k,r in tab.items() if a in r['support']} for a in arms}
        def rank(a):
            agree=f.avg([f.jaccard(sets[a],sets[b]) for b in arms if b!=a]);conf=f.avg([tab[k]['support'][a]['probability'] for k in sets[a]])
            mode=spec.get('whole_mode','consensus');score=agree if mode=='consensus' else conf if mode=='confidence' else (agree+conf)/2
            return score,agree,conf,-arms.index(a)
        winner=max(arms,key=rank);return {k:1. for k in sets[winner]},probs
    if learned and tab:
        keys=list(tab); xx=torch.tensor([normalize_features(prompt,tab,k) for k in keys],dtype=torch.float64)[:,model['columns']]
        z=(xx-torch.tensor(model['mu'],dtype=torch.float64))/torch.tensor(model['sd'],dtype=torch.float64)
        p=(z@torch.tensor(model['w'],dtype=torch.float64)+model['b']).sigmoid().tolist();probs=dict(zip(keys,p))
    for k,r in tab.items():
        v=r['votes'];n=r['n_readers'];nv=r['native_votes'];p=r['prob'];fam=r['families'];nf=r['n_families']; kind=spec['rule'];q=spec.get('threshold',.5)
        if kind=='architecture6':ok=fam>=6
        elif kind=='architecture':ok=fam/nf>=q
        elif kind=='vote':ok=v/n>=q
        elif kind=='cross':ok=v/n>=q and r['sources']==2
        elif kind=='hybrid':ok=v/n>=q or (fam/nf>=max(.25,q-.15) and nv/n>=.25 and p>=spec['confidence'])
        elif kind=='learned':ok=probs[k]>=q
        else:raise ValueError(kind)
        if not ok:continue
        if learned:w=probs[k] if spec['weight']=='probability' else max(.000001,math.log(max(probs[k],1e-8)/max(1-probs[k],1e-8))-math.log(q/(1-q))+.01)
        elif spec.get('weight')=='margin':w=v+2*p+math.tanh(r['margin']/8)+.1*nv/n
        elif spec.get('weight')=='consensus':w=v+p+.1*nv/n
        else:w=v/n+fam/nf+nv/n+.25*p
        out[k]=max(w,1e-6)
    return out,probs

def predict_objects(prompt,ev,spec):
    tab=make_table(ev,spec['arms']);w,_=weights(prompt,tab,spec);return f.schedule(tab,w),tab

def fixed_grid(arms):
    arms=list(arms); specs=[]
    for rule in ('architecture','vote','hybrid','cross'):
        for q in (.18,.25,.35,.45,.55,.65,.75,.85,.95):
            for wt in ('consensus','margin','balanced'):
                specs.append({'rule':rule,'threshold':q,'weight':wt,'confidence':.7,'arms':arms})
    for mode in ('consensus','confidence','combined'):specs.append({'rule':'whole','whole_mode':mode,'arms':arms})
    if len(arms)==len(ARMS):
        specs.insert(0,{'rule':'architecture6','weight':'consensus','arms':arms});specs.append({'rule':'cross','threshold':3/len(ARMS),'weight':'consensus','arms':arms})
    return specs

def metrics(pairs,preds):
    s=f.stats([r for r,e in pairs],preds);return s|{'core_rate':s['core_exact']/s['cases'],'edge_reachability':s['reachable_surface_edges']/max(s['surface_edges'],1)}
def quality(s):return (s['core_exact'],s['exact'],s['f1'])
def pass_targets(s):return all(s[k]>=v for k,v in TARGETS.items())
def prepared(pairs,arms):return [(r,make_table(e,arms)) for r,e in pairs]
def evaluate(pairs,ps,spec):
    pred=[f.schedule(tab,weights(r['prompt'],tab,spec)[0]) for r,tab in ps];return metrics(pairs,pred)

def fit_model(ps,columns):
    x=[];y=[]
    for r,tab in ps:
        g=f.gold(r)
        for k in tab:x.append(normalize_features(r['prompt'],tab,k));y.append(float(k in g))
    X=torch.tensor(x,dtype=torch.float64)[:,columns];Y=torch.tensor(y,dtype=torch.float64);need(bool(Y.min()==0 and Y.max()==1),'DEVELOPMENT_NO_BINARY_LABELS')
    mu=X.mean(0);sd=X.std(0,unbiased=False).clamp_min(.05);X=(X-mu)/sd
    w=torch.zeros(X.shape[1],dtype=X.dtype,requires_grad=True);b=torch.zeros((),dtype=X.dtype,requires_grad=True)
    opt=torch.optim.LBFGS([w,b],lr=1.,max_iter=80,line_search_fn='strong_wolfe')
    def closure():
        opt.zero_grad();loss=torch.nn.functional.binary_cross_entropy_with_logits(X@w+b,Y)+.01*w.square().mean();loss.backward();return loss
    opt.step(closure);need(bool(torch.isfinite(w).all() and torch.isfinite(b)),'NONFINITE_ADMISSION_MODEL')
    return {'columns':columns,'mu':mu.tolist(),'sd':sd.tolist(),'w':w.detach().tolist(),'b':float(b.detach()),'fit_candidates':len(y)}

def learned_grid(ps,arms):
    # The only optional learning is a small candidate-admission calibration model.
    labels=set()
    for r,t in ps:
        g=f.gold(r);labels.update(k in g for k in t)
    if labels!={False,True}:
        emit('CALIBRATION_SKIPPED',{'readers':len(arms),'reason':'candidate labels do not contain both classes'});return []
    r0,t0=next((r,t) for r,t in ps if t);d=len(normalize_features(r0['prompt'],t0,next(iter(t0))));spec=[]
    for columns in (list(range(8)),list(range(14)),list(range(d))):
        model=fit_model(ps,columns)
        for q in (.25,.4,.5,.6,.75,.9):
            for wt in ('probability','logodds'):spec.append({'rule':'learned','threshold':q,'weight':wt,'arms':list(arms),'model':model})
    return spec

def residual(pairs,spec):
    errors=[];auc=co.defaultdict(list);counts=co.Counter();by=co.defaultdict(co.Counter);paired_modes=co.defaultdict(list)
    for r,ev in pairs:
        tab=make_table(ev,spec['arms']);w,prob=weights(r['prompt'],tab,spec);p=set(f.schedule(tab,w));g=f.gold(r);miss=g-p;extra=p-g
        for k,x in tab.items():
            for metric,val in [('prob',x['prob']),('margin',x['margin']),('native_votes',x['native_votes']),('architectures',x['families']),('sources',x['sources'])]:auc[(x['votes'],metric)].append((val,k in g))
        for k in sorted(miss|extra):
            x=tab.get(k);is_miss=k in miss
            if is_miss:
                reason='absent_inventory' if x is None else 'admission_rejected' if k not in w else 'compatibility_selection'
            else:reason=f.error_kind(k,g)
            conflicts=[z for z in p if z!=k and (f.overlap(k,z) or (x and all(not (te<=ss or se<=ts) for ts,te in x['options'] for ss,se in tab[z]['options'])))]
            # Candidate-level forced inclusion audit: retain actual admission weights and give target larger weight than total.
            forced=None
            if is_miss and x is not None:
                q=set(f.schedule(tab,w|{k:sum(w.values())+1}));forced={'selected':k in q,'displaced':f.sorted_objects(p-q),'gold_displaced':f.sorted_objects((p-q)&g)}
            review=not is_miss and reason=='disjoint_extra' and 'alias' in r['mode'] or not is_miss and reason=='disjoint_extra' and 'pronoun' in r['mode']
            counts[('fn' if is_miss else 'fp')+'|'+reason]+=1;by[r['mode']][('fn' if is_miss else 'fp')+'|'+reason]+=1
            errors.append({'case_id':r['case_id'],'base_id':r['base_id'],'mode':r['mode'],'prompt':r['prompt'],'object':list(k),'text':r['prompt'][k[0]:k[1]],'is_missing':is_miss,'mechanism':reason,'votes':x['votes'] if x else 0,'families':x['families'] if x else 0,'support':x['support'] if x else {},'weight':w.get(k),'conflicting_selected':f.sorted_objects(conflicts),'overlapping_gold':f.sorted_objects(z for z in g if f.overlap(k,z)),'forced_inclusion_diagnostic':forced,'relevance_or_annotation_review_candidate':bool(review)})
            paired_modes[r['base_id']].append({'mode':r['mode'],'kind':k[2],'text':r['prompt'][k[0]:k[1]],'mechanism':reason,'missing':is_miss})
    return {'counts':dict(counts),'by_mode':{k:dict(v) for k,v in by.items()},'errors':errors,'paired_base_errors':dict(paired_modes),'conditional_auc':{f'{v}|{k}':{'auc':f.auc(x),'n':len(x),'true':sum(y for _,y in x)} for (v,k),x in sorted(auc.items())}}

def develop(a,out,parent):
    folder,old,r,paths,g2,ident=parent; d=out/'development';d.mkdir(parents=True,exist_ok=True)
    existing=d/'selection.json'
    if existing.exists():
        lock=read(existing);need(lock['identity']==ident and lock['report_sha256']==sha(d/'report.json') and lock['forensics_sha256']==sha(d/'residual.json'),'DEVELOPMENT_DRIFT');emit('DEVELOPMENT_REUSED',{'winner':lock['selected'],'readers':len(lock['policy']['arms'])});return
    dev,oldspec=load_development(a,folder,old,r);baseline={'rule':'architecture6','weight':'consensus','arms':list(ARMS)}
    # Replay the exact old winner against our reimplementation.
    need(oldspec==('architecture',6),'UNEXPECTED_PARENT_POLICY');alltest=dev['test']
    for row,ev in alltest:
        need(predict_objects(row['prompt'],ev,baseline)[0]==f.reconstruct(ev,f.candidate_table(ev),oldspec)[0],'BASELINE_PREDICTION_DRIFT')
    res=residual(alltest,baseline)
    need(sum(v for k,v in res['counts'].items() if k.startswith('fn|'))==r['selected']['overall']['metrics']['fn'] and sum(v for k,v in res['counts'].items() if k.startswith('fp|'))==r['selected']['overall']['metrics']['fp'],'RESIDUAL_REPLAY')
    write(d/'residual.json',res);emit('FORENSICS',res['counts'])
    # Partition the exposed old test bases by a reproducible shuffled assignment, stratified by family.
    fit_ids=set();select_ids=set();rng=random.Random(1151026)
    for family in sorted({r['family'] for r,e in alltest}):
        ids=sorted({r['base_id'] for r,e in alltest if r['family']==family});rng.shuffle(ids);fit_ids.update(ids[:len(ids)//2]);select_ids.update(ids[len(ids)//2:])
    fit=[(r,e) for r,e in alltest if r['base_id'] in fit_ids];sel=[(r,e) for r,e in alltest if r['base_id'] in select_ids];need(fit_ids.isdisjoint(select_ids) and len(fit)>0 and len(sel)>0,'DEV_SPLIT')
    pf=prepared(fit,ARMS);ps=prepared(sel,ARMS); ledger=[]
    state_path=d/'search-state.json'
    if state_path.exists():
        saved=read(state_path);state=saved['state'];need(digest(state)==saved['sha256'],'SEARCH_STATE_HASH_DRIFT')
    else:state={'identity':ident,'fit_bases':sorted(fit_ids),'selection_bases':sorted(select_ids),'trials':{},'fit_scores':{}}
    need(state['identity']==ident and state['fit_bases']==sorted(fit_ids) and state['selection_bases']==sorted(select_ids),'SEARCH_STATE_DRIFT')
    def save_search():write(state_path,{'state':state,'sha256':digest(state)})
    def trial(spec,fitp=pf,selp=ps):
        # Deduplicate; all decisions are made before fresh prompts exist.
        sid=digest(spec);rec=state['trials'].get(sid)
        if rec is None:
            rec={'id':sid,'spec':spec,'fit':evaluate(fit,fitp,spec),'selection':evaluate(sel,selp,spec)};state['trials'][sid]=rec
        else:need(rec['spec']==spec and rec['id']==sid,'TRIAL_STATE_DRIFT')
        if not any(x['id']==sid for x in ledger):ledger.append(rec)
        return rec
    fixed=[trial(x) for x in fixed_grid(ARMS)];save_search();best_fixed=max(fixed,key=lambda x:quality(x['selection']));adaptive=not pass_targets(best_fixed['selection'])
    if adaptive:
        for spec in learned_grid(pf,ARMS):trial(spec)
        save_search()
    emit('FULL_INVENTORY',{'fixed':best_fixed['selection'],'adaptive_calibration':adaptive,'trials':len(ledger)})
    # Build a greedy backward-elimination trajectory using FIT data only.
    # At each size compare every possible one-reader deletion; threshold variants are retested.
    current=list(ARMS); reduced=[];path=[];best_by_fit=max(fixed,key=lambda x:quality(x['fit']))['spec']; seeds=[best_by_fit,{'rule':'vote','threshold':.65,'weight':'consensus','arms':current},{'rule':'architecture','threshold':.65,'weight':'consensus','arms':current}]
    while len(current)>1:
        candidates=[]
        for drop in current:
            subset=[x for x in current if x!=drop];fitp=prepared(fit,subset)
            specs=[dict(s,arms=subset) for s in seeds if s['rule']!='architecture6']
            for rule in ('vote','architecture'):
                for q in (.45,.6,.75):specs.append({'rule':rule,'threshold':q,'weight':'consensus','arms':subset})
            for spec in specs:
                sid=digest(spec);q=state['fit_scores'].get(sid)
                if q is None:q=list(quality(evaluate(fit,fitp,spec)));state['fit_scores'][sid]=q
                candidates.append((tuple(q),spec,drop))
        _,winner,drop=max(candidates,key=lambda x:x[0]);current=winner['arms'];fitp=prepared(fit,current);selp=prepared(sel,current)
        rec=trial(winner,fitp,selp);reduced.append(rec);path.append({'removed':drop,'remaining':current,'fit':rec['fit'],'selection':rec['selection']})
        # Optional targeted calibrator at fixed inventory milestones, not trained on selection.
        if adaptive and len(current) in (12,8,4):
            for spec in learned_grid(fitp,current):trial(spec,fitp,selp)
        save_search()
        emit('REDUCTION',{'remaining':len(current),'removed':drop,'fit':rec['fit'],'selection':rec['selection']})
    for arm in ARMS:
        subset=[arm];trial({'rule':'whole','whole_mode':'consensus','arms':subset},prepared(fit,subset),prepared(sel,subset))
    save_search()
    best=max(ledger,key=lambda x:quality(x['selection'])); b=best['selection']
    # Cost reduction is only accepted within explicitly declared non-inferiority tolerances on development.
    viable=[x for x in ledger if x['selection']['core_exact']>=b['core_exact']-1 and x['selection']['exact']>=b['exact']-1 and x['selection']['f1']>=b['f1']-.001 and x['selection']['reachable_surface_edges']>=b['reachable_surface_edges']-4]
    selected=min(viable,key=lambda x:(len(x['spec']['arms']),len({m.ARMS[a][0] for a in x['spec']['arms']}),-x['selection']['core_exact'],-x['selection']['exact'],-x['selection']['f1']))
    frontier=[]
    for size in sorted({len(x['spec']['arms']) for x in ledger}):
        q=max((x for x in ledger if len(x['spec']['arms'])==size),key=lambda x:quality(x['selection']));frontier.append({'readers':size,'id':q['id'],'metrics':q['selection']})
    report={'identity':ident,'retrospective_only':True,'fit_bases':sorted(fit_ids),'selection_bases':sorted(select_ids),'target_metrics':TARGETS,'adaptive_calibration':adaptive,'trial_count':len(ledger),'trials':ledger,'elimination_path':path,'accuracy_cost_frontier':frontier,'best_unconstrained':best['id'],'selected':selected,'previous_validation_diagnostic':metrics(dev['validation'],[predict_objects(r['prompt'],e,selected['spec'])[0] for r,e in dev['validation']]),'annotation_policy':'Review only; no author labels altered.'}
    write(d/'report.json',report);lock={'identity':ident,'selected':selected['id'],'policy':selected['spec'],'development_metrics':selected['selection'],'report_sha256':sha(d/'report.json'),'forensics_sha256':sha(d/'residual.json'),'target_metrics':TARGETS,'selection_before_fresh_generation':True};immutable(existing,lock);emit('DEVELOPMENT_SELECTED',{'policy':lock['policy'],'metrics':lock['development_metrics'],'trials':len(ledger)})

def selection(out,ident):
    d=out/'development';s=read(d/'selection.json');need(s['identity']==ident and s['selection_before_fresh_generation'] and s['report_sha256']==sha(d/'report.json') and s['forensics_sha256']==sha(d/'residual.json'),'DEVELOPMENT_LOCK_DRIFT');r=read(d/'report.json');need(r['selected']['spec']==s['policy'] and r['selected']['id']==s['selected']==digest(s['policy']) and r['selected']['selection']==s['development_metrics'],'DEVELOPMENT_POLICY_DRIFT');return s

def prepare(a,out,parent):
    folder,old,r,paths,g2,ident=parent;s=selection(out,ident);d=out/'certification';d.mkdir(parents=True,exist_ok=True);lock=d/'seal.json'
    if lock.exists():check_seal(a,out,ident,answers=True);emit('SEAL_REUSED',{'cases':read(lock)['cases']});return
    # Exclude known prior G1/G2 prompts. New base IDs never overlap the earlier cohorts.
    known=A._known_prompts(paths,a.root); old_bases=set();root=Path(a.root)
    def exclude(rows):
        for r in rows:
            known.add(r['prompt_sha256']);old_bases.add(r.get('base_id'))
    for p in (root/'g1-semantic-fuzz-0110'/'pilot'/'seal.jsonl',root/'g1-semantic-fuzz-0110'/'expansion'/'seal.jsonl',root/A.OUTPUT_NAME/'seal'/'prompts.jsonl'):
        if p.is_file():
            with p.open(encoding='utf-8') as h:exclude(json.loads(l) for l in h if l.strip())
    for p in (root/'g1-s-first-reference-0111'/'cohort-seal.json',root/'g1-backbone-transmission-0112'/'probe'/'cohort-seal.json',root/'g1-backbone-transmission-0112'/'run'/'cohort-seal.json',folder.parent/'cohort-seal.json'):
        for rows in read(p)['groups'].values():exclude(rows)
    lab=P.fuzz();rows=[]; rng=random.Random(ALT_SEED);fresh=set();serial=0;accepted=0
    # Preserve family balance even if an entire generated base is rejected for reuse.
    while accepted<a.bases:
        want=accepted%2;need(serial<a.bases*100,'FRESH_BASE_EXHAUSTED');i=serial;serial+=1
        if i%2!=want:continue
        base=lab._base(i,seed=SEED);base['split']='test';suite=[lab._variant(base,mode) for mode in MODES];hs=[v['prompt_sha256'] for v in suite]
        if len(set(hs))!=len(hs) or set(hs)&(known|fresh):continue
        need(all(v['base_id'] not in old_bases for v in suite),'FRESH_BASE_ID_REUSE')
        for v in suite:v['cohort']='0110_generator_new_bases';rows.append(v)
        fresh.update(hs);accepted+=1
    for i in range(a.alternate_bases):
        fam=('allocation','encounter')[i%2]
        # Different author-generation path, not an external human corpus.
        for attempt in range(100):
            suite=A._controlled_suite(fam,rng.randrange(2**31),115000000+i*100+attempt)
            hashes=[P.digest_text(r['prompt']) for r in suite.values()]
            if len(set(hashes))==len(hashes) and not (set(hashes)&known) and not (set(hashes)&fresh):break
        else:raise ValueError(TAG+'_ALTERNATE_FRESHNESS_EXHAUSTED')
        fresh.update(hashes);bid=f'g1-0115-alternate-{i:05d}'
        for mode,v in suite.items():rows.append({'case_id':bid+'-'+mode,'base_id':bid,'family':fam,'mode':mode,'prompt':v['prompt'],'prompt_sha256':P.digest_text(v['prompt']),'graph':v['graph'],'cohort':'0093_generator_new_bases'})
    need(len(rows)==a.bases*len(MODES)+a.alternate_bases*len(A.MODES),'FRESH_CASE_COUNT');hashes=[r['prompt_sha256'] for r in rows];need(len(set(hashes))==len(hashes) and not set(hashes)&known,'FRESH_PROMPT_COLLISION');need(not {r['base_id'] for r in rows}&old_bases and len({r['case_id'] for r in rows})==len(rows),'BASE_OR_CASE_REUSE')
    public=[];answers=[]
    for r in rows:
        need(r['prompt_sha256']==P.digest_text(r['prompt']),'PROMPT_HASH');need(all(0<=k[0]<k[1]<=len(r['prompt']) for k in f.gold(r)),'GOLD_COORDINATES')
        public.append({k:r[k] for k in ('case_id','base_id','family','mode','prompt','prompt_sha256','cohort')});answers.append({'case_id':r['case_id'],'prompt_sha256':r['prompt_sha256'],'graph':r['graph']})
    immutable(d/'prompts.json',public);immutable(d/'answers.SECRET.json',answers)
    seal={'identity':ident,'selection_sha256':sha(out/'development'/'selection.json'),'prompts_sha256':sha(d/'prompts.json'),'answers_sha256':sha(d/'answers.SECRET.json'),'cases':len(public),'bases':a.bases,'alternate_bases':a.alternate_bases,'modes':list(MODES),'seeds':[SEED,ALT_SEED],'gold_read_in_predict':False,'device':a.device,'previously_seen_prompts_excluded':len(known)}
    immutable(lock,seal);emit('SEALED',seal)

def check_seal(a,out,ident,answers=False):
    selection(out,ident);d=out/'certification';s=read(d/'seal.json');need(s['identity']==ident and s['selection_sha256']==sha(out/'development'/'selection.json') and s['prompts_sha256']==sha(d/'prompts.json') and s['bases']==a.bases and s['alternate_bases']==a.alternate_bases and s['device']==a.device,'CERTIFICATION_SEAL_DRIFT')
    # Prediction must NOT read even the contents of the answer file to hash it.
    if answers:need(s['answers_sha256']==sha(d/'answers.SECRET.json'),'ANSWER_DRIFT')
    return s

def token_assignment(objects,tab,p):
    """Earliest-ending compatible mapping; ordered spans imply greedy feasibility."""
    spans=[];end=0
    for k in sorted(f.objects(objects)):
        choices=[(a,b) for a,b in tab[k]['options'] if a>=end];need(bool(choices),'NO_COMPATIBLE_TOKEN_ASSIGNMENT');a,b=min(choices,key=lambda z:(z[1],z[0]));end=b
        sp={'kind':k[2],'token_first':a,'token_last':b-1};bounds=GH.token_span_char_bounds(sp,p);need(bounds is not None and (*bounds,k[2])==tuple(k),'G2_COORDINATE_DRIFT');spans.append(sp)
    return spans

def edges(graph):
    # Match the established G2 binding-only graph contract; do not count latent/G3 edges.
    return A._gold_edges(graph)
def json_edges(xs):return [A._json_edge(e) for e in sorted(xs)]
def edge_set(xs):return {(x[0],tuple(x[1]),x[2],tuple(x[3]),x[4]) for x in xs}
def g2_inference(head,h,p,pred,tab):
    pred=f.objects(pred)  # Accept JSON arrays and native tuples at the G1/G2 boundary.
    spans=token_assignment(pred,tab,p)
    with torch.inference_mode():
        raw=head(h.float(),eligible=p['eligible'].to(h.device),inference_spans=spans,active_stage='G2');graph,surfaces=GH.decode_graph(raw,p,unlocked='G2')
    need({(*n['span'],n['kind']) for n in graph['nodes'] if 'span' in n}==set(pred) and len(surfaces)==len(pred),'G2_ENDPOINT_CONSUMPTION_DRIFT')
    return json_edges(edges(graph))

def predict(a,out,parent):
    folder,old,r,paths,g2,ident=parent;sel=selection(out,ident);seal=check_seal(a,out,ident);d=out/'certification';shards=d/'predictions';shards.mkdir(exist_ok=True);public=read(d/'prompts.json');policy=sel['policy'];sealhash=sha(d/'seal.json');execution_hash=sha(__file__)
    need(len(public)==seal['cases'] and len({r['case_id'] for r in public})==len(public),'PUBLIC_CASE_BIJECTION')
    need(all(set(r)=={'case_id','base_id','family','mode','prompt','prompt_sha256','cohort'} and r['prompt_sha256']==P.digest_text(r['prompt']) for r in public),'PUBLIC_SCHEMA_OR_HASH')
    lock=d/'prediction-lock.json'
    def shard(i,row):
        path=shards/f'{i:06d}.json'
        if not path.exists():return None
        rec=read(path);need(rec['seal_sha256']==sealhash and rec.get('execution_implementation_sha256')==execution_hash and rec['case_id']==row['case_id'] and rec['prompt_sha256']==row['prompt_sha256'] and rec['body_sha256']==digest(rec['body']),'PREDICTION_SHARD_DRIFT');return rec
    if lock.exists():
        prior=read(lock);need(prior['seal_sha256']==sealhash and prior.get('execution_implementation_sha256')==execution_hash and prior['shards']==[sha(shards/f'{i:06d}.json') for i in range(len(public))],'PREDICTION_LOCK_DRIFT');emit('PREDICTIONS_REUSED',{'cases':len(public)});return
    # Freeze all source checkpoints. T.load checks exact original backbone and S lineage.
    model,tok,comp,ctrl,S,sig,lin=T.load(a.root,{'certification':public},a.device);need(sig==ident['frozen_g1'],'LIVE_G1_SOURCE_DRIFT')
    head,manifest=C._load_checkpoint(g2,device=a.device);head.eval().requires_grad_(False);need(manifest['head_sha256']==ident['g2_0087_head_sha256'],'LIVE_G2_SOURCE_DRIFT')
    readers={}
    for arm in ARMS:
        _,ls,kind=m.ARMS[arm];reader=m.Reader(comp.head,ls,kind).to(a.device);reader.load_state_dict(load_file(str(folder/arm/'selected.safetensors'),device='cpu'),strict=True);reader.eval().requires_grad_(False);readers[arm]=reader
    full={'rule':'architecture6','weight':'consensus','arms':list(ARMS)};n=0
    for i,row in enumerate(public):
        if shard(i,row) is not None:continue
        p=B.prepare_prompt(tok,row['prompt']);started=time.perf_counter();original=m.capture(model,p,None,a.device);ref=m.capture(model,p,S,a.device);ev={};timings={}
        for arm,reader in readers.items():
            start=time.perf_counter();src,ls,_=m.ARMS[arm];states=[(original if src=='original' else ref)[k].to(a.device,dtype=torch.float32) for k in ls]
            # No answer, family or mode is passed to the reader or decoder.
            ev[arm]=f.capture_one(comp,reader,({'case_id':row['case_id']},p,None),states);timings[arm]=time.perf_counter()-start
        fulltab=make_table(ev,ARMS);orig=sorted(f.objects([v['object'] for v in ev[BASE]['selected']]));basepred,basetab=predict_objects(row['prompt'],ev,full);newpred,newtab=predict_objects(row['prompt'],ev,policy)
        h=original[28].to(a.device,dtype=torch.float32);body={'arms':{},'reader_union':f.sorted_objects(fulltab),'selected_union':f.sorted_objects(newtab),'selected_reader_count':len(policy['arms']),'reader_decode_errors':{k:v['decode_error'] for k,v in ev.items() if v['decode_error'] is not None},'reader_seconds':timings,'total_seconds':time.perf_counter()-started}
        for name,pr,tab in [('original_frozen',orig,fulltab),('0114_full',basepred,basetab),('0115_selected',newpred,newtab)]:
            body['arms'][name]={'objects':f.sorted_objects(pr),'g2_binding_edges':g2_inference(head,h,p,pr,tab)}
        rec={'seal_sha256':sealhash,'execution_implementation_sha256':execution_hash,'case_id':row['case_id'],'prompt_sha256':row['prompt_sha256'],'body':body,'body_sha256':digest(body)};immutable(shards/f'{i:06d}.json',rec);n+=1
        if n==1 or (i+1)%16==0 or i+1==len(public):emit('PREDICT_PROGRESS',{'done':i+1,'of':len(public),'new_this_run':n})
    need(all(not v.requires_grad and v.grad is None for net in [model,comp.head,head,*readers.values()] for v in net.parameters()),'FROZEN_WEIGHT_AUTHORITY')
    # Recheck source integrity after inference, prior to opening fresh answers.
    need(sha(__file__)==execution_hash and sealed_parent(out,check_parent(a))[-1]==ident,'SOURCE_CHANGED_DURING_PREDICTION')
    need(all(shard(i,r) is not None for i,r in enumerate(public)),'INCOMPLETE_PREDICTIONS')
    immutable(lock,{'seal_sha256':sealhash,'execution_implementation_sha256':execution_hash,'shards':[sha(shards/f'{i:06d}.json') for i in range(len(public))],'cases':len(public),'gold_read':False,'teacher_forced_g1':False});emit('PREDICTIONS_LOCKED',{'cases':len(public),'lock_sha256':sha(lock)})

def edge_count(row,pr,prededges):
    g=edges(row['graph']);p=edge_set(prededges);anchors={(x[2],tuple(x[:2])) for x in pr};reach={e for e in g if (e[0],e[1]) in anchors and (e[2],e[3]) in anchors};need(all((e[0],e[1]) in anchors and (e[2],e[3]) in anchors for e in p),'EDGE_OUTSIDE_PREDICTED_ENDPOINTS')
    return {'cases':1,'tp':len(p&g),'fp':len(p-g),'fn':len(g-p),'exact':int(p==g),'core_exact':int(g<=p),'gold_binding_edges':len(g),'reachable_gold_binding_edges':len(reach),'unreachable_fn':len(g-reach),'reachable_fn':len(reach-p),'strict_surface_graph':int(p==g and set(map(tuple,pr))==f.gold(row))}

def summarize_eval(rows,bodies):
    out={}
    for name in ('original_frozen','0114_full','0115_selected'):
        pp=[b['arms'][name]['objects'] for b in bodies];gm=f.stats(rows,pp);gm|={'core_rate':gm['core_exact']/gm['cases'],'edge_reachability':gm['reachable_surface_edges']/max(gm['surface_edges'],1)}
        e=[edge_count(r,p,b['arms'][name]['g2_binding_edges']) for r,p,b in zip(rows,pp,bodies)];em=P.summarize(e);em['strict_surface_graph_rate']=em['strict_surface_graph']/em['cases'];em['conditional_recall_on_reachable_edges']=em['tp']/max(em['reachable_gold_binding_edges'],1)
        out[name]={'g1':gm,'g2_0087_native_binding':em}
    return out

def score(a,out,parent):
    folder,old,r,paths,g2,ident=parent;sel=selection(out,ident);seal=check_seal(a,out,ident,answers=True);d=out/'certification';lock=read(d/'prediction-lock.json');public=read(d/'prompts.json');answers=read(d/'answers.SECRET.json');need(lock['seal_sha256']==sha(d/'seal.json') and lock.get('execution_implementation_sha256')==sha(__file__) and lock['gold_read'] is False and lock['teacher_forced_g1'] is False and lock['cases']==len(public)==len(answers)==len(lock['shards']),'PREDICTION_COMMITMENT')
    rows=[];bodies=[]
    for i,(r,g) in enumerate(zip(public,answers)):
        path=d/'predictions'/f'{i:06d}.json';need(sha(path)==lock['shards'][i],'LOCKED_SHARD_DRIFT');x=read(path);need(x.get('execution_implementation_sha256')==lock['execution_implementation_sha256'] and x['seal_sha256']==lock['seal_sha256'] and x['case_id']==r['case_id']==g['case_id'] and x['prompt_sha256']==r['prompt_sha256']==g['prompt_sha256'] and x['body_sha256']==digest(x['body']),'SCORE_BIJECTION');rows.append(r|{'graph':g['graph']});bodies.append(x['body'])
    overall=summarize_eval(rows,bodies);strata={}
    for field in ('cohort','family','mode'):
        for val in sorted({r[field] for r in rows}):
            idx=[i for i,r in enumerate(rows) if r[field]==val];strata[field+'|'+val]=summarize_eval([rows[i] for i in idx],[bodies[i] for i in idx])
    selected=[b['arms']['0115_selected']['objects'] for b in bodies];base=[b['arms']['original_frozen']['objects'] for b in bodies];oldpred=[b['arms']['0114_full']['objects'] for b in bodies];oracles=co.Counter();fail=[];g2_fail=[];g2_by_type=co.defaultdict(co.Counter)
    for r,b,p in zip(rows,bodies,selected):
        g=f.gold(r);full=set(map(tuple,b['reader_union']));small=set(map(tuple,b['selected_union']));predset=set(map(tuple,p));oracles.update({'gold':len(g),'full_union_gold':len(g&full),'selected_union_gold':len(g&small),'full_union_core':g<=full,'selected_union_core':g<=small});miss=g-predset;extra=predset-g
        ge=edges(r['graph']);pe=edge_set(b['arms']['0115_selected']['g2_binding_edges']);anchors={(k[2],tuple(k[:2])) for k in p};reachable={e for e in ge if (e[0],e[1]) in anchors and (e[2],e[3]) in anchors};missing=ge-pe;spurious=pe-ge
        for e in ge&pe:g2_by_type[e[4]]['tp']+=1
        for e in spurious:g2_by_type[e[4]]['fp']+=1
        for e in missing:g2_by_type[e[4]]['reachable_fn' if e in reachable else 'unreachable_fn']+=1
        if missing or spurious:g2_fail.append({'case_id':r['case_id'],'base_id':r['base_id'],'mode':r['mode'],'cohort':r['cohort'],'g1_strict':not (miss or extra),'upstream_missing_edges':json_edges(ge-reachable),'reachable_missing_edges':json_edges(reachable-pe),'extra_edges':json_edges(spurious)})
        if miss or extra:fail.append({'case_id':r['case_id'],'base_id':r['base_id'],'mode':r['mode'],'cohort':r['cohort'],'prompt':r['prompt'],'missing':f.sorted_objects(miss),'extra':f.sorted_objects(extra),'missing_from_full_inventory':f.sorted_objects(miss-full),'missing_from_selected_inventory':f.sorted_objects(miss-small),'g2_missing':json_edges(edges(r['graph'])-edge_set(b['arms']['0115_selected']['g2_binding_edges']))})
    write(d/'failures.json',fail);write(d/'g2-failures.json',g2_fail);g1=overall['0115_selected']['g1'];gate={k:{'actual':g1[k],'target':v,'pass':g1[k]>=v} for k,v in TARGETS.items()};timings={arm:sum(b['reader_seconds'][arm] for b in bodies) for arm in ARMS}
    result={'schema':SCHEMA,'identity':ident,'execution_implementation_sha256':lock['execution_implementation_sha256'],'development_selection_sha256':sha(out/'development'/'selection.json'),'cohort_seal_sha256':sha(d/'seal.json'),'prediction_lock_sha256':sha(d/'prediction-lock.json'),'cases':len(rows),'independent_bases':len({r['base_id'] for r in rows}),'selected_policy':sel['policy'],'overall':overall,'strata':strata,'union_oracles':dict(oracles),'paired_vs_original':f.paired(rows,selected,base),'paired_vs_0114':f.paired(rows,selected,oldpred),'research_targets':gate,'all_targets_met':all(x['pass'] for x in gate.values()),'selected_readers':len(sel['policy']['arms']),'full_readers':len(ARMS),'reader_seconds_all':sum(timings.values()),'reader_seconds_selected_subset':sum(timings[x] for x in sel['policy']['arms']),'timing_scope':'Sum of timed per-reader calls inside full comparator; not standalone end-to-end speedup. Both Qwen input paths evaluated for the full comparator.','G2_scope':'Frozen historical G2-0087 native binding classifier, identical original final hidden state across all arms, actual predicted endpoints; not the latest G2 ensemble or a production gate.','certification_scope':'Independent base-disjoint synthetic cohorts from two existing author generators. No external real-world or new-family certification.','reader_decode_errors':dict(co.Counter(a for b in bodies for a in b['reader_decode_errors'])),'g2_binding_errors_by_type':{k:dict(v) for k,v in g2_by_type.items()},'g2_failure_sha256':sha(d/'g2-failures.json'),'failure_sha256':sha(d/'failures.json'),'production_promotion':False,'g3_unlocked':False}
    immutable(d/'result.json',result)
    # Persistent run-specific Markdown record, derived only from locked results.
    report=out/'RESEARCH_RECORD.md';lines=['# G1-0115 integrated reconstruction — measured research record','','Status: completed; research only; no production promotion or G3 unlock.','','## Why and how','','Diagnose 0114 residual errors; optimize on previously exposed base-disjoint development subsets; freeze policy before fresh generation; compare original G1, full 0114 integration and selected 0115 on the same blind population.','',f"Cases: {len(rows)}; independent bases: {result['independent_bases']}; selected readers: {len(sel['policy']['arms'])}/{len(ARMS)}.",'','## Actual results','','| Arm | G1 F1 | Core | Strict | G2 native binding F1 |','|---|---:|---:|---:|---:|']
    for k,v in overall.items():lines.append(f"| {k} | {v['g1']['f1']:.6f} | {v['g1']['core_exact']}/{len(rows)} | {v['g1']['exact']}/{len(rows)} | {v['g2_0087_native_binding']['f1']:.6f} |")
    lines+=['','## Target outcomes','']+[f"- {k}: {v['actual']:.6f} vs {v['target']:.6f}; {'PASS' if v['pass'] else 'NOT MET'}" for k,v in gate.items()]+['','## Interpretation and next decision','','If the full union misses gold, investigate candidate/reader observability. If the reduced union loses gold found by the full union, investigate reduction. If the selected union contains missing objects, investigate evidence admission or compatibility. Review extras for boundaries and annotation relevance; do not silently change labels. If G2 reachable misses persist, diagnose relationship classification rather than assuming G1 is sufficient. This result does not authorize release.','','See development/residual.json, development/report.json, development/selection.json, certification/result.json, certification/failures.json and certification/g2-failures.json.','',f"Result SHA-256: `{sha(d/'result.json')}`."]
    report.write_text('\n'.join(lines)+'\n',encoding='utf-8');emit('RESULT',{'overall':overall,'research_targets':gate,'union_oracles':dict(oracles),'selected_readers':result['selected_readers']});emit('SAVED',{'report':str(d/'result.json'),'sha256':sha(d/'result.json'),'production_promotion':False})

def selftest():
    torch.manual_seed(115);torch.set_num_threads(1);f.selftest()
    p={'prompt':'Alpha 95 cost 70','prompt_start':0,'offsets':[(0,0),(0,5),(6,8),(9,13),(14,16),(0,0)],'eligible':torch.tensor([False,True,True,True,True,False])}
    g={(0,5,'action'),(6,8,'quantity'),(9,13,'outcome'),(14,16,'quantity')};extra=(0,5,'actor');rows=[]
    for i in range(4):
        row={'case_id':str(i),'base_id':str(i),'mode':'control','family':'allocation','prompt':p['prompt'],'graph':{'nodes':[{'id':str(j),'kind':k[2],'span':list(k[:2])} for j,k in enumerate(sorted(g))],'edges':[{'src':'1','dst':'0','type':'applies_to'}]}}
        def item(k):
            a,b=k[:2];q=next(j for j,v in enumerate(p['offsets']) if v==(a,b));return {'object':list(k),'probability':.95 if k in g else .15,'margin':4. if k in g else -4.,'competitor_margin':3. if k in g else -3.,'token_options':[[q,q+1]]}
        ev={a:{'selected':[item(k) for k in sorted(g|({extra} if j==0 else set()))],'native':f.sorted_objects(g),'decode_error':None} for j,a in enumerate(ARMS)};rows.append((row,ev))
    full={'rule':'architecture6','weight':'consensus','arms':list(ARMS)};pred,tab=predict_objects(p['prompt'],rows[0][1],full);need(set(pred)==g,'SELFTEST_BASELINE');need(len(token_assignment(pred,tab,p))==4,'SELFTEST_TOKEN_MAPPING');ps=prepared(rows,ARMS);grid=learned_grid(ps,ARMS);need(any(evaluate(rows,ps,s)['exact']==4 for s in grid),'SELFTEST_CALIBRATION')
    reduced=dict(full,rule='vote',threshold=.6,arms=list(ARMS[:3]));need(set(predict_objects(p['prompt'],rows[0][1],reduced)[0])==g,'SELFTEST_SUBSET');need(not residual(rows,full)['errors'],'SELFTEST_FORENSIC')
    head=GH.GamerGraphHead(hidden_size=16,width=16,layers=1,max_span_tokens=4).eval().requires_grad_(False);h=torch.randn(6,16);out=g2_inference(head,h,p,pred,tab);json_pred=f.sorted_objects(pred);need(g2_inference(head,h,p,json_pred,tab)==out and token_assignment(json_pred,tab,p)==token_assignment(pred,tab,p),'SELFTEST_JSON_ENDPOINT_REGRESSION');count=edge_count(rows[0][0],pred,out);need(count['gold_binding_edges']==1 and count['reachable_gold_binding_edges']==1,'SELFTEST_G2_REAL_HEAD')
    fake={'arms':{k:{'objects':f.sorted_objects(g),'g2_binding_edges':out} for k in ('original_frozen','0114_full','0115_selected')}};z=summarize_eval([rows[0][0]],[fake]);need(z['0115_selected']['g1']['exact']==1,'SELFTEST_SCORE')
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        root=Path(td);(root/'development').mkdir();lock=root/'development'/'selection.json';current={'implementation_sha256':sha(__file__),'other':'fixture'};prior=dict(current,implementation_sha256=PRE_ENDPOINT_FIX_SHA256)
        write(lock,{'identity':prior});need(sealed_parent(root,(current,))[-1]==prior,'SELFTEST_LEGACY_SEAL_REUSE')
        write(lock,{'identity':dict(prior,other='changed')})
        try:sealed_parent(root,(current,))
        except ValueError as e:need('PRE_FIX_IDENTITY_MISMATCH' in str(e),'SELFTEST_WRONG_REFUSAL')
        else:raise AssertionError('SELFTEST_ACCEPTED_CHANGED_SOURCE')
    emit('SELFTEST',{'json_endpoint_regression':True,'exact_legacy_seal_reuse':True,'actual_G1_and_G2_head_cpu':True,'gold_free_reconstruction':True,'targeted_calibration':True,'subset_reduction_policy':True,'residual_ledger':True,'binding_endpoint_mapping':True,'actual_qwen_cuda':False,'actual_runtime_checkpoint':False})

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('command',choices=('audit','selftest','develop','prepare','predict','score','run'),nargs='?',default='audit');p.add_argument('--root',default=m.ROOT);p.add_argument('--output');p.add_argument('--device',default='cuda');p.add_argument('--bases',type=int,default=192);p.add_argument('--alternate-bases',type=int,default=64);a=p.parse_args(argv);need(a.bases>=4 and a.bases%2==0 and a.alternate_bases>=4 and a.alternate_bases%2==0,'BASE_COUNTS_EVEN_AT_LEAST_FOUR');torch.set_num_threads(min(4,torch.get_num_threads()))
    if a.command=='selftest':selftest();return
    out=output(a)
    if a.command=='run':
        # Separate processes avoid retaining development/answer structures during blind inference.
        for phase in ('develop','prepare','predict','score'):
            subprocess.run([sys.executable,'-u','-m',MODULE,phase,'--root',a.root,'--output',str(out),'--device',a.device,'--bases',str(a.bases),'--alternate-bases',str(a.alternate_bases)],check=True)
        return
    parent=sealed_parent(out,check_parent(a));emit('AUDIT',{'ready':True,'phase':a.command,'execution_implementation_sha256':sha(__file__),'reusing_pre_fix_development':parent[-1]['implementation_sha256']==PRE_ENDPOINT_FIX_SHA256,'parent_0114_sha256':parent[-1]['parent_0114_sha256'],'g2_0087_head_sha256':parent[-1]['g2_0087_head_sha256'],'planned_cases':a.bases*len(MODES)+a.alternate_bases*len(A.MODES),'production_promotion':False,'g3_unlocked':False})
    if a.command=='audit':return
    out.mkdir(parents=True,exist_ok=True);{'develop':develop,'prepare':prepare,'predict':predict,'score':score}[a.command](a,out,parent)
if __name__=='__main__':main()
