"""0117: frozen reader evidence, competitive and joint graph reconstruction.
Research only. Fit optional small admission models on EXPOSED development data.
Independent author holdout is generated only after selection; no fresh labels in predict.
"""
from __future__ import annotations
import argparse, collections as co, copy, math, json, sys, subprocess
from pathlib import Path
from types import SimpleNamespace
import numpy as np
import torch
try:
    from scipy.optimize import milp, Bounds, LinearConstraint, minimize
    from scipy.sparse import coo_matrix
except ImportError as exc:
    raise RuntimeError('0117 requires SciPy >= 1.9 with scipy.optimize.milp in the NanoJEV Python environment') from exc
from safetensors.torch import load_file
from tools import nanojev_gamer_v1_semantic_integrity_probe as I
L=I.lab; G=I.G; f=I.f; m=I.m; T=I.T; P=I.P; B=I.B
read,write,sha,digest=L.read,L.write,L.sha,L.digest
TAG='G12_0117_JOINT'; MODULE='tools.nanojev_gamer_v1_joint_reconstruction_0117'
ARMS=('baseline_0114','baseline_0115','agreement','competitive','contextual','joint_fixed','joint_learned')
REL=tuple(L.CLASSES); NREAD=len(L.ARMS); SEED=1172026
AUTHOR=Path(__file__).with_name('nanojev_gamer_v1_joint_author_0117.py')

def need(x,msg):
    if not x:raise ValueError(TAG+'_'+msg)
def emit(k,x):print(TAG+'_'+k+' '+json.dumps(x,sort_keys=True,allow_nan=False),flush=True)
def imm(p,x):
    if p.exists():need(read(p)==x,'IMMUTABLE_DRIFT_'+str(p))
    else:write(p,x)
def obj(x):return tuple(x)
def edge(x):return (x[0],tuple(x[1]),x[2],tuple(x[3]),x[4])
def endpoints(e):return ((*e[1],e[0]),(*e[3],e[2]))
def pred(objects,edges,**meta):return {'objects':f.sorted_objects(objects),'edges':L.jsedges(edges),**meta}
def logit(p):return math.log(max(1e-6,min(1-1e-6,float(p)))/max(1e-6,1-min(1-1e-6,float(p))))
def f1(a,b):return 2*len(a&b)/max(1,len(a)+len(b)) if a or b else 1.

def setup(a):
    root=Path(a.root).resolve(); q=SimpleNamespace(root=str(root),device=a.device,cached=12,cache_only=False,command=a.command)
    x=I.setup(q);_,source,probe,ctx,sel,seal,pub,ident=x
    ps,pr=I.check(x); pl=read(probe/'prediction-lock.json'); need(pl['execution_source']==sha(I.__file__) and pl['seal']==sha(probe/'seal.json'),'PROBE_LOCK_LINEAGE')
    need(pl['shards']==[sha(probe/'predictions'/f'{i:06d}.json') for i in range(len(pr))],'PROBE_SHARD_LOCK')
    d=source/'fresh'; lock=read(d/'prediction-lock.json');need(lock['seal']==sha(d/'seal.json') and lock['shards']==[sha(d/'predictions'/f'{i:06d}.json') for i in range(len(pub))],'0116_LOCK')
    out=root/'g1-g2-joint-reconstruction-0117'; out.mkdir(exist_ok=True)
    identity={'implementation':sha(__file__),'author_source':sha(AUTHOR),'parent':ident,'probe_seal':sha(probe/'seal.json'),'probe_lock':sha(probe/'prediction-lock.json'),'0116_lock':sha(d/'prediction-lock.json'),'scipy':__import__('scipy').__version__,'device':a.device,'seed':SEED,'scope':'research_only'}
    return x,out,identity,pr

def load_models(a,x,fresh=None):
    root,source,probe,ctx,sel,seal,pub,ident=x
    if fresh is None:
        paths,_,sig,_,_=T.lineage(str(root),{});comp=T.prior.native.load_composite(paths['composite'],device=a.device);model=tok=S=None;ctrl=None
    else:model,tok,comp,ctrl,S,sig,_=T.load(str(root),{'fresh':fresh},a.device)
    need(sig==ctx[0][-1]['frozen_g1'],'FROZEN_G1_DRIFT');comp.head.eval().requires_grad_(False)
    rr={}
    for arm in f.ARMS:
        _,ls,k=m.ARMS[arm];net=m.Reader(comp.head,ls,k).to(a.device);net.load_state_dict(load_file(str(ctx[0][0]/arm/'selected.safetensors')),strict=True);rr[arm]=net.eval().requires_grad_(False)
    nets=L.readers_for(a,source,sel)
    need(all(set(sel['policies'][k]['arms'])<=set(nets) for k in I.G2_POLICIES),'POLICY_ARM_DRIFT')
    return comp,rr,nets,model,tok,S,ctrl

def frozen(models):
    comp,rr,nets,model,tok,S,ctrl=models
    need(all(not p.requires_grad and p.grad is None for n in [comp.head,*rr.values(),*nets.values()]+([model,ctrl] if model is not None else []) for p in n.parameters()),'FROZEN_WEIGHT_VIOLATION')

def capture_g1(a,models,states,p):
    comp,rr,*_=models;ev={}
    for arm,net in rr.items():
        src,ls,_=m.ARMS[arm];hs=[states[f'{src}_{k}'].to(a.device,dtype=torch.float32) for k in ls];ev[arm]=I.capture_probe_reader(comp,net,p,hs)
    return ev

def make_union_view(tab,p):
    # A hypothesis lattice, not a valid graph. Each candidate is mapped independently;
    # the solver must later enforce token and character compatibility. Contextual
    # G2 scores on this lattice are experimental and may shift with false objects.
    keys=sorted(tab);spans=[]
    for k in keys:
        a,b=min(tab[k]['options'],key=lambda z:(z[1],z[0]));s={'kind':k[2],'token_first':a,'token_last':b-1}
        need((*L.GH.token_span_char_bounds(s,p),k[2])==k,'LATTICE_COORDINATE');spans.append(s)
    return {'objects':f.sorted_objects(keys),'spans':spans}

def node_vector(tab,k):
    r=tab[k];ss=r['support'];n=r['n_readers'];v=r['votes']/n;nf=r['families']/r['n_families'];p=r['prob'];comp=max((q['votes']/n for x,q in tab.items() if x!=k and f.overlap(x,k)),default=0.)
    return [v,nf,r['sources']/2,r['native_votes']/n,p,math.tanh(r['margin']/8),v-comp,math.tanh(f.avg([z['competitor_margin'] for z in ss.values()])/8),*[float(a in ss) for a in f.ARMS]]

def surface_scores(states,p,view,nets,sel,context=True):
    feat=L.features(states,view,p);keys=feat['objects'];probs={};clean={}
    with torch.inference_mode():
        for k,net in nets.items():probs[k]=net(feat).softmax(-1).cpu()
        if context:
            # One fixed intervention, removing explicit geometry/type/lexical inputs.
            # Not a retrained context-only reader; OOD sensitivity is recorded.
            for name in ('loc','geom','lex'):feat[name].zero_()
            handles=[net.kinds.register_forward_hook(lambda mod,inp,out:torch.zeros_like(out)) for net in nets.values()]
            try:
                for k,net in nets.items():clean[k]=net(feat).softmax(-1).cpu()
            finally:
                for h in handles:h.remove()
    ind={k:L.individual(keys,q,sel['decoders'][k],sel['caps']) for k,q in probs.items()}
    base={k:L.policy_predict(keys,probs,ind,sel['policies'][k],sel['caps']) for k in I.G2_POLICIES}
    union=set().union(*ind.values());candidates=union|set().union(*base.values());ix={k:i for i,k in enumerate(keys)}
    stack=torch.stack(list(probs.values()));avg=stack.mean(0);cl=torch.stack(list(clean.values())).mean(0) if clean else avg
    entries=[];raw=set();top=(stack[...,1:].argmax(-1)+1).tolist()
    for i,j in ((i,j) for i in range(len(keys)) for j in range(len(keys)) if i!=j):
        for k in {v[i][j] for v in top}:raw.add(L.edge(keys[i],keys[j],REL[k]))
    for e in sorted(candidates):
        a,b=endpoints(e);i,j=ix[a],ix[b];k=REL.index(e[4]);q=stack[:,i,j,k];none=stack[:,i,j,0];other=stack[:,i,j,:].clone();other[:,k]=-1
        votes=[float(e in ind[z]) for z in nets];v=sum(votes)/NREAD;mean=float(q.mean());margin=float((q-none).mean());alt=float((q-other.max(-1).values).mean());contextual=float(cl[i,j,k]-cl[i,j,0]);support=[z for z in nets if e in ind[z]]
        entries.append({'edge':L.jsedges([e])[0],'x':[v,mean,float(q.max()),margin,alt,contextual,*votes,*[float(z) for z in q.tolist()],*[float(e[4]==r) for r in REL[1:]]],'readers':support})
    return {'view':view,'candidates':entries,'union':L.jsedges(union),'raw_top1':L.jsedges(raw),'baseline':{k:L.jsedges(v) for k,v in base.items()}}

def joint_pool(data):
    # Preserve candidates admitted on any of the three endpoint surfaces, even
    # when lattice attention changes their scores. Average only supporting
    # surfaces; retain provenance. The raw inventory remains separate.
    groups=co.defaultdict(list)
    for name,z in [('lattice',data['lattice']),*data['fixed'].items()]:
        for q in z['candidates']:groups[edge(q['edge'])].append((name,q))
    result=[]
    for e,items in sorted(groups.items()):
        # Identical views are not independent evidence. Deduplicate numerical
        # feature vectors before averaging; source names remain auditable.
        vectors={tuple(q['x']) for _,q in items};xs=np.asarray(sorted(vectors),dtype=float)
        result.append({'edge':L.jsedges([e])[0],'x':xs.mean(0).tolist(),'surfaces':[name for name,_ in items],'readers':sorted({r for _,q in items for r in q['readers']})})
    return result

def collect(a,x,models,states,p,mode='normal',seed=SEED):
    if mode!='normal':states=I.transform(states,p,mode,seed)
    ev=capture_g1(a,models,states,p);tab=G.make_table(ev,f.ARMS);views=L.endpoint_views(p['prompt'],ev,x[3][1],p);nets=models[2];sel=x[4];memo={}
    def scores(view):
        k=digest(view)
        if k not in memo:memo[k]=surface_scores(states,p,view,nets,sel)
        return memo[k]
    lattice=make_union_view(tab,p)
    return {'nodes':[{'object':list(k),'x':node_vector(tab,k),'options':[list(z) for z in tab[k]['options']]} for k in sorted(tab)],'decode_errors':{k:v['decode_error'] for k,v in ev.items() if v['decode_error']},'fixed':{k:scores(views[k]) for k in I.ROUTES},'lattice':scores(lattice)}

def fit_logistic(data):
    X=np.array([x for x,y in data],dtype=float); y=np.array([y for x,y in data],dtype=float);need(len(set(y))==2,'FIT_CLASS_MISSING');mu=X.mean(0);sd=np.maximum(X.std(0),.05);z=(X-mu)/sd
    # Class-balanced objective, deterministic ridge. Intercept not penalized.
    w=np.where(y>0,.5/max(1,y.sum()),.5/max(1,len(y)-y.sum()))
    def loss(b):
        h=z@b[:-1]+b[-1];pr=1/(1+np.exp(-np.clip(h,-60,60)));val=np.sum(w*(np.logaddexp(0,h)-y*h))+.02*np.sum(b[:-1]**2);grad=z.T@(w*(pr-y))+.04*b[:-1];return val,np.r_[grad,np.sum(w*(pr-y))]
    r=minimize(loss,np.zeros(z.shape[1]+1),jac=True,method='L-BFGS-B',options={'maxiter':300,'ftol':1e-10});need(r.success and np.isfinite(r.x).all(),'ADMISSION_FIT_FAILED_'+str(r.message));return {'mean':mu.tolist(),'scale':sd.tolist(),'coef':r.x.tolist(),'n':len(y),'positive':int(y.sum())}
def model_score(x,model):
    b=np.asarray(model['coef']);return float(np.dot((np.asarray(x)-model['mean'])/model['scale'],b[:-1])+b[-1])

def weights_node(x):return 2.5*(x[0]-.35)+.5*(x[1]-.5)+.35*x[6]+.15*x[5]
def weights_edge(x,mode):
    v,mean,mx,margin,alt,ctx=x[:6]
    if mode=='agreement':return v-.50
    if mode=='competitive':return .65*margin+.35*alt+.25*(v-.35)-.05
    if mode=='contextual':return .50*margin+.25*alt+.25*ctx+.20*(v-.35)-.04
    return .7*margin+.3*alt+.25*(v-.35)+.10*ctx-.04

def choose_fixed(z,mode):
    # Fixed endpoints; choose strongest positive alternative per ordered pair.
    keys=f.objects(z['view']['objects']);best={}
    for q in z['candidates']:
        e=edge(q['edge']);score=weights_edge(q['x'],mode);k=endpoints(e)
        if score>0 and (k not in best or score>best[k][0]):best[k]=(score,e)
    return pred(keys,{v[1] for v in best.values()})

def solve_joint(data,learned=None,scale=1.,bias=0.):
    """Binary graph optimization, using one explicit token mapping per lattice node.
    No family, generator, gold counts or answer-conditioned caps. Incumbents are
    accepted only after independent feasibility checks and carry solver status.
    """
    nodes=data['nodes'];view=data['lattice']['view'];keys=[obj(z['object']) for z in nodes];need(keys==list(map(obj,view['objects'])),'JOINT_NODE_ORDER');n=len(keys);ix={k:i for i,k in enumerate(keys)};sp=view['spans'];candidates=joint_pool(data) if 'fixed' in data else data['lattice']['candidates'];edges=[edge(q['edge']) for q in candidates];N=n+len(edges)
    if not N:return pred(set(),set(),solver={'status':'empty','optimal':True,'objective':0.})
    nw=[weights_node(z['x']) if learned is None else model_score(z['x'],learned['node']) for z in nodes]
    ew=[weights_edge(z['x'],'joint') if learned is None else model_score(z['x'],learned['edge']) for z in candidates]
    w=np.asarray(nw+[scale*(v-bias) for v in ew]);rr=[];cc=[];vv=[];upper=[]
    def row(terms,ub):
        j=len(upper);upper.append(ub)
        for k,v in terms:rr.append(j);cc.append(k);vv.append(v)
    for i,a in enumerate(keys):
        for j in range(i+1,n):
            b=keys[j];token_overlap=sp[i]['token_first']<=sp[j]['token_last'] and sp[j]['token_first']<=sp[i]['token_last']
            if f.overlap(a,b) or token_overlap:row([(i,1),(j,1)],1)
    row([(i,1) for i in range(n)],128)
    pairs=co.defaultdict(list)
    for j,e in enumerate(edges,n):
        a,b=endpoints(e);i,k=ix[a],ix[b];need(i!=k,'SELF_EDGE');row([(j,1),(i,-1)],0);row([(j,1),(k,-1)],0);pairs[i,k].append(j)
    for group in pairs.values():
        if len(group)>1:row([(j,1) for j in group],1)
    mat=coo_matrix((vv,(rr,cc)),shape=(len(upper),N)).tocsc();res=milp(-w,integrality=np.ones(N),bounds=Bounds(np.zeros(N),np.ones(N)),constraints=LinearConstraint(mat,-np.inf,np.asarray(upper)),options={'time_limit':5.,'mip_rel_gap':0.0})
    need(res.x is not None and res.status in (0,1),'NO_FEASIBLE_SOLVER_INCUMBENT_'+str(res.message));chosen=np.rint(res.x).astype(int);need(np.max(np.abs(res.x-chosen))<1e-5 and np.all(mat@chosen<=np.asarray(upper)+1e-7),'SOLVER_INFEASIBLE');o={k for i,k in enumerate(keys) if chosen[i]};e={k for j,k in enumerate(edges,n) if chosen[j]};need(all(set(endpoints(q))<=o for q in e),'EDGE_WITHOUT_NODE')
    return pred(o,e,solver={'status':int(res.status),'optimal':bool(res.status==0),'objective':float(w@chosen),'gap':float(res.mip_gap) if getattr(res,'mip_gap',None) is not None and math.isfinite(res.mip_gap) else None})

def predict_policy(data,arm,model=None):
    if arm.startswith('baseline'):
        z=data['fixed']['0114_full' if arm=='baseline_0114' else '0115_selected'];return pred(f.objects(z['view']['objects']),L.edge_set(z['baseline']['new_integrated']))
    if arm in ('agreement','competitive','contextual'):return choose_fixed(data['fixed']['0115_selected'],arm)
    if arm=='joint_fixed':return solve_joint(data)
    if arm=='joint_learned':need(model is not None,'MODEL_REQUIRED');return solve_joint(data,model,scale=.5,bias=0.)
    raise ValueError(arm)

def audit_graph(r):
    # Structural/coordinate contract only. Does NOT certify linguistic semantics.
    nodes=r['graph']['nodes'];ids={n['id'] for n in nodes};need(len(ids)==len(nodes),'DUP_NODE_ID');surfaces=[n for n in nodes if 'span' in n];o=f.gold(r)
    need(all(0<=n['span'][0]<n['span'][1]<=len(r['prompt']) and n['kind'] in L.KINDS for n in surfaces),'INVALID_AUTHOR_SPAN')
    need(all(e['src'] in ids and e['dst'] in ids for e in r['graph']['edges']),'INVALID_GRAPH_ENDPOINT');g=L.gold(r);need(all(set(endpoints(e))<=o for e in g),'BINDING_ENDPOINT_CONTRACT')
    return {'objects':len(o),'bindings':len(g),'ontology_scope':'implicit_binding_exploratory' if 'implicit' in r['cohort'] else 'structural_validation_only'}

def record_metrics(r,d,pr):
    go=f.gold(r);ge=L.gold(r);po=f.objects(pr['objects']);pe=L.edge_set(pr['edges']);un={obj(q['object']) for q in d['nodes']};raw=L.edge_set(d['lattice']['raw_top1']);fixed=L.edge_set(d['fixed']['0115_selected']['union']);lat=L.edge_set(d['lattice']['union']);reach={e for e in ge if set(endpoints(e))<=po}
    return {'g1':f1(go,po),'g2':f1(ge,pe),'exact':go==po and ge==pe,'object_inventory':len(go&un),'gold_objects':len(go),'edge_inventory_lattice':len(ge&lat),'edge_inventory_joint_pool':len(ge&{edge(q['edge']) for q in joint_pool(d)}),'edge_inventory_fixed':len(ge&fixed),'raw_top1_lattice':len(ge&raw),'gold_edges':len(ge),'upstream_fn':len(ge-reach),'reachable_fn':len(reach-pe),'g1_missing':f.sorted_objects(go-po),'g1_extra':f.sorted_objects(po-go),'g2_missing':L.jsedges(ge-pe),'g2_extra':L.jsedges(pe-ge),'solver':pr.get('solver')}

def contrast(rows,preds):
    # Directional changes at identical surface coordinates for direct/reverse;
    # invariance in canonical author node IDs across paraphrases (unmatched nodes
    # and edges explicitly penalized, never projected away).
    by=co.defaultdict(dict)
    for r,p in zip(rows,preds):by[r['base_id']][r['mode']]=(r,p)
    result=[]
    for base,group in sorted(by.items()):
        if not {'direct','reverse'}<=set(group):continue
        def canonical(r,p):
            ix={(*n['span'],n['kind']):n['id'] for n in r['graph']['nodes'] if 'span' in n}
            def name(k):return ix.get(k,'UNMATCHED:'+str(k))
            return {name(k) for k in f.objects(p['objects'])},{(name(a),name(b),e[4]) for e in L.edge_set(p['edges']) for a,b in [endpoints(e)]}
        def gold_pred(r):return pred(f.gold(r),L.gold(r))
        r1,p1=group['direct'];r2,p2=group['reverse'];g1=L.gold(r1);g2=L.gold(r2);e1=L.edge_set(p1['edges']);e2=L.edge_set(p2['edges']);want={(1,e) for e in g1-g2}|{(2,e) for e in g2-g1};got={(1,e) for e in e1-e2}|{(2,e) for e in e2-e1}
        invariant=[]
        for mode,other in [('direct','direct_paraphrase'),('reverse','reverse_paraphrase')]:
            if other not in group:continue
            ra,pa=group[mode];rb,pb=group[other];oa,ea=canonical(ra,pa);ob,eb=canonical(rb,pb);ga,ha=canonical(ra,gold_pred(ra));gb,hb=canonical(rb,gold_pred(rb));need((ga,ha)==(gb,hb),'PARAPHRASE_GOLD_DRIFT');invariant.append({'mode':mode,'object_agreement':f1(oa,ob),'edge_agreement':f1(ea,eb),'both_correct':(oa,ea)==(ga,ha) and (ob,eb)==(gb,hb)})
        result.append({'base':base,'cohort':r1['cohort'],'expected_changes':len(want),'correct_changes':len(want&got),'spurious_changes':len(got-want),'both_exact':f.objects(p1['objects'])==f.gold(r1) and f.objects(p2['objects'])==f.gold(r2) and e1==g1 and e2==g2,'invariance':invariant})
    return result

def summarize(rows,ds,ps):
    z=[record_metrics(r,d,p) for r,d,p in zip(rows,ds,ps)];bases=co.defaultdict(list)
    for r,q in zip(rows,z):bases[r['base_id']].append(q)
    macro=lambda k:sum(sum(v[k] for v in q)/len(q) for q in bases.values())/max(1,len(bases))
    c=contrast(rows,ps);changes=sum(q['expected_changes'] for q in c);good=sum(q['correct_changes'] for q in c);extra=sum(q['spurious_changes'] for q in c)
    result={'cases':len(rows),'bases':len(bases),'g1':f.stats(rows,[p['objects'] for p in ps]),'g2':L.measure(rows,[p['objects'] for p in ps],[L.edge_set(p['edges']) for p in ps]),'macro_g1':macro('g1'),'macro_g2':macro('g2'),'macro_strict':macro('exact'),'contrasts':c,'inventory':{k:sum(q[k] for q in z) for k in ('object_inventory','gold_objects','edge_inventory_lattice','edge_inventory_joint_pool','edge_inventory_fixed','raw_top1_lattice','gold_edges','upstream_fn','reachable_fn')},'solver_nonoptimal':sum(q['solver'] is not None and not q['solver']['optimal'] for q in z),'decoder_errors':sum(bool(d['decode_errors']) for d in ds)}
    result['selection_score']=.55*result['macro_g2']+.25*result['macro_g1']+.20*(2*good/max(1,changes+good+extra) if changes else result['macro_strict']);return result,z

def development_rows(x,probe_rows):
    root,source,probe,ctx,sel,seal,pub,ident=x;d=source/'fresh';need(sha(d/'answers.SECRET.json')==seal['answers'],'0116_ANSWERS');ans=read(d/'answers.SECRET.json');need(len(ans)==len(pub),'0116_COUNT');result=[]
    for i,(p,g) in enumerate(zip(pub,ans)):
        need((p['case_id'],p['prompt_sha256'])==(g['case_id'],g['prompt_sha256']),'DEV_ALIGNMENT');rec=L.shard(d,i,p,sha(d/'seal.json'));need(rec is not None,'DEV_SHARD');body=rec['body'];part='validation' if int(p['base_id'].rsplit('-',1)[1])%3==2 else 'fit'
        result.append((dict(p,graph=g['graph']),m.unpack(body['prepared']),d/'cache'/f'{i:06d}.safetensors',part,body['endpoint_views'],{k:body['views'][k]['predictions']['new_integrated'] for k in I.ROUTES}))
    ps=read(probe/'seal.json');need(sha(probe/'answers.SECRET.json')==ps['answers'],'PROBE_ANSWERS');pa=read(probe/'answers.SECRET.json');need(len(pa)==len(probe_rows),'PROBE_COUNT')
    for i,(p,g) in enumerate(zip(probe_rows,pa)):
        if p['cache_index'] is not None:continue
        need((p['case_id'],p['prompt_sha256'])==(g['case_id'],g['prompt_sha256']),'PROBE_ALIGNMENT');b=I.validate_record(probe/'predictions'/f'{i:06d}.json',p,sha(probe/'seal.json'));cache=probe/'novel-cache'/f'{i:06d}.safetensors';need(sha(cache)==b['novel_cache_sha256'],'PROBE_CACHE_DRIFT');part='diagnostic' if 'implicit' in p['cohort'] else 'validation' if p['base_id']=='reference-pair' else 'fit';result.append((dict(p,graph=g['graph']),m.unpack(b['prepared']),cache,part,b['g1']['normal']['views'],{k:b['fixed'][k]['normal']['predictions']['new_integrated'] for k in I.ROUTES}))
    need(len({r['prompt_sha256'] for r,*_ in result})==len(result),'DEV_DUPLICATE_PROMPT');parts={k:{r['base_id'] for r,p,c,q,*_ in result if q==k} for k in ('fit','validation','diagnostic')};need(all(not parts[k]&parts[j] for k in parts for j in parts if k!=j),'DEV_BASE_LEAKAGE');return result

def forensic(r,d):
    go=f.gold(r);ge=L.gold(r);nodes={obj(q['object']):q for q in d['nodes']};z=d['fixed']['0115_selected'];fixed={edge(q['edge']):q for q in z['candidates']};lattice={edge(q['edge']):q for q in d['lattice']['candidates']};po=f.objects(z['view']['objects']);pe=L.edge_set(z['baseline']['new_integrated']);raw=L.edge_set(d['lattice']['raw_top1']);records=[]
    for e in sorted(ge-pe):
        a,b=endpoints(e);reason='upstream_endpoint' if not {a,b}<=po else 'available_rejected' if e in fixed else 'reader_admission_absent';alts=[q for k,q in fixed.items() if k!=e and (endpoints(k)==(a,b) or k[4]==e[4] and (endpoints(k)[0]==a or endpoints(k)[1]==b))];alts=sorted(alts,key=lambda q:q['x'][1],reverse=True)[:5]
        records.append({'edge':L.jsedges([e])[0],'reason':reason,'fixed':fixed.get(e),'lattice':lattice.get(e),'raw_top1':e in raw,'competitors':alts})
    return {'case_id':r['case_id'],'prompt':r['prompt'],'missing_nodes':[{'object':list(k),'evidence':nodes.get(k)} for k in sorted(go-po)],'missing_edges':records,'extra_edges':[fixed.get(e,{'edge':L.jsedges([e])[0]}) for e in sorted(pe-ge)]}

def develop(a,setup_data):
    x,out,ident,pr=setup_data;records=development_rows(x,pr);cache=out/'development-cache';cache.mkdir(exist_ok=True);models=None;data=[];rows=[];parts=[];receipts=[]
    for i,(row,p,path,part,views,baseline) in enumerate(records):
        audit_graph(row);dest=cache/f'{i:06d}.json';header={'identity':ident,'prompt':row['prompt_sha256'],'cache':sha(path),'part':part}
        if dest.exists():
            z=read(dest);need(z['header']==header and z['digest']==digest(z['body']),'DEV_EVIDENCE_DRIFT');d=z['body']
        else:
            if models is None:models=load_models(a,x)
            d=collect(a,x,models,load_file(str(path)),p);imm(dest,{'header':header,'body':d,'digest':digest(d)})
        need(all(d['fixed'][k]['view']==views[k] and d['fixed'][k]['baseline']['new_integrated']==baseline[k] for k in I.ROUTES),'BASELINE_REPLAY_DRIFT_'+row['case_id']);need(not d['decode_errors'],'NORMAL_DECODER_FAILURE_'+row['case_id']);data.append(d);rows.append(row);parts.append(part);receipts.append(sha(dest));emit('DEVELOP_EVIDENCE',{'done':i+1,'of':len(records),'case':row['case_id'],'part':part,'nodes':len(d['nodes']),'lattice_edges':len(d['lattice']['candidates'])})
    if models is not None:frozen(models)
    # Fit only on training base IDs. No implicit-ownership annotations in fit or selection.
    nd=[];ed=[]
    for r,d,part in zip(rows,data,parts):
        if part!='fit':continue
        go=f.gold(r);ge=L.gold(r);nd.extend((q['x'],int(obj(q['object']) in go)) for q in d['nodes']);ed.extend((q['x'],int(edge(q['edge']) in ge)) for q in joint_pool(d))
    model={'node':fit_logistic(nd),'edge':fit_logistic(ed)};report={'identity':ident,'cache_sha256':receipts,'split':{p:sorted({r['base_id'] for r,q in zip(rows,parts) if p==q}) for p in set(parts)},'ontology_audit':[dict(case_id=r['case_id'],**audit_graph(r)) for r in rows],'arms':{},'forensics':[forensic(r,d) for r,d in zip(rows,data)]}
    for arm in ARMS:
        pp=[predict_policy(d,arm,model) for d in data];results={}
        for part in ('fit','validation','diagnostic'):
            ids=[i for i,q in enumerate(parts) if q==part];z,_=summarize([rows[i] for i in ids],[data[i] for i in ids],[pp[i] for i in ids]);results[part]=z
        report['arms'][arm]=results;v=results['validation'];emit('BAKEOFF',{'arm':arm,'validation_score':v['selection_score'],'g1':v['g1']['f1'],'g2':v['g2']['f1'],'strict':v['g2']['strict_surface_graph'],'contrasts':v['contrasts'],'solver_nonoptimal':v['solver_nonoptimal']})
    # Ties prefer simpler earlier arm. Do not choose on author diagnostic or fresh results.
    winner=max(ARMS,key=lambda k:(report['arms'][k]['validation']['selection_score'],-ARMS.index(k)));report['winner']=winner;imm(out/'development.json',report);selection={'identity':ident,'development_sha256':sha(out/'development.json'),'winner':winner,'model':model,'arms':list(ARMS),'selection_rule':'0.55 macro G2 F1 + 0.25 macro G1 F1 + 0.20 directional change F1 (or strict if no contrast); base-disjoint development validation only','frozen_readers':True};imm(out/'selection.json',selection);emit('SELECTED',{'arm':winner,'selection':sha(out/'selection.json'),'validation':report['arms'][winner]['validation']})

def selection(out,ident):
    s=read(out/'selection.json');need(s['identity']==ident and s['development_sha256']==sha(out/'development.json') and s['winner'] in ARMS and s['arms']==list(ARMS),'SELECTION_DRIFT');return s

def prepare(a,setup_data):
    x,out,ident,pr=setup_data;s=selection(out,ident);d=out/'fresh';d.mkdir(exist_ok=True)
    if (d/'seal.json').exists():check_fresh(out,ident,True);emit('SEAL_REUSED',{'cases':read(d/'seal.json')['cases']});return
    # Import author keys in this process only. Source hash committed in identity.
    from tools.nanojev_gamer_v1_joint_author_0117 import cases
    root,source,probe,ctx,sel,seal,pub,_=x;known=L.A._known_prompts(ctx[0][3],str(root));known.update(r['prompt_sha256'] for r in pub+pr)
    known.update(r['prompt_sha256'] for r in read(root/'g1-integrated-reconstruction-0115'/'certification'/'prompts.json'))
    for p in (root/'g1-semantic-fuzz-0110'/'pilot'/'seal.jsonl',root/'g1-semantic-fuzz-0110'/'expansion'/'seal.jsonl'):
        if p.is_file():
            with p.open(encoding='utf-8') as h:known.update(json.loads(line)['prompt_sha256'] for line in h if line.strip())
    for p in (root/'g1-s-first-reference-0111'/'cohort-seal.json',root/'g1-backbone-transmission-0112'/'probe'/'cohort-seal.json',root/'g1-backbone-transmission-0112'/'run'/'cohort-seal.json',ctx[0][0].parent/'cohort-seal.json'):
        known.update(r['prompt_sha256'] for group in read(p)['groups'].values() for r in group)
    rows=cases();need(len(rows)==28 and len({r['base_id'] for r in rows})==7,'AUTHOR_POPULATION');ids=set();public=[];secret=[];audit=[]
    for r in rows:
        need(r['prompt_sha256'] not in known and r['case_id'] not in ids,'HOLDOUT_COLLISION');known.add(r['prompt_sha256']);ids.add(r['case_id']);audit.append(dict(case_id=r['case_id'],**audit_graph(r)));public.append({k:v for k,v in r.items() if k!='graph'});secret.append({'case_id':r['case_id'],'prompt_sha256':r['prompt_sha256'],'graph':r['graph']})
    # Ensure matched geometry and real meaning change, plus exact paraphrase gold equivalence.
    ct=contrast(rows,[pred(f.gold(r),L.gold(r)) for r in rows]);need(len(ct)==7 and all(q['expected_changes']>0 and q['correct_changes']==q['expected_changes'] and len(q['invariance'])==2 and all(v['both_correct'] for v in q['invariance']) for q in ct),'AUTHOR_CONTRAST_INVALID')
    for base in {r['base_id'] for r in rows}:
        q={r['mode']:r for r in rows if r['base_id']==base};need(f.gold(q['direct'])==f.gold(q['reverse']),'CONTRAST_GEOMETRY_DRIFT')
    imm(d/'prompts.json',public);imm(d/'answers.SECRET.json',secret);imm(d/'annotation-audit.json',audit);imm(d/'seal.json',{'identity':ident,'selection':sha(out/'selection.json'),'prompts':sha(d/'prompts.json'),'answers':sha(d/'answers.SECRET.json'),'annotation_audit':sha(d/'annotation-audit.json'),'cases':len(rows),'bases':7,'holdout_scope':'seven new authored structures; lexical/structural novelty not exhaustive','implicit_bindings_exploratory':True});emit('SEALED',{'cases':len(rows),'bases':7,'winner':s['winner']})

def check_fresh(out,ident,answers=False):
    s=selection(out,ident);d=out/'fresh';z=read(d/'seal.json');need(z['identity']==ident and z['selection']==sha(out/'selection.json') and z['prompts']==sha(d/'prompts.json'),'FRESH_SEAL_DRIFT')
    if answers:need(z['answers']==sha(d/'answers.SECRET.json') and z['annotation_audit']==sha(d/'annotation-audit.json'),'FRESH_ANSWER_DRIFT')
    rows=read(d/'prompts.json');need(len(rows)==z['cases'] and len({r['prompt_sha256'] for r in rows})==len(rows),'FRESH_COUNT')
    for r in rows:need(set(r)=={'case_id','base_id','family','cohort','mode','prompt','prompt_sha256'} and P.digest_text(r['prompt'])==r['prompt_sha256'],'PUBLIC_SCHEMA')
    return s,d,z,rows

def fresh_shard(path,r,lh):
    q=read(path);need(q['seal']==lh and q['case_id']==r['case_id'] and q['prompt_sha256']==r['prompt_sha256'] and q['body_sha256']==digest(q['body']),'FRESH_SHARD_DRIFT');return q['body']

def predict(a,setup_data):
    x,out,ident,pr=setup_data;s,d,z,rows=check_fresh(out,ident);lh=sha(d/'seal.json');(d/'predictions').mkdir(exist_ok=True);(d/'cache').mkdir(exist_ok=True);models=None
    for i,r in enumerate(rows):
        path=d/'predictions'/f'{i:06d}.json';cache=d/'cache'/f'{i:06d}.safetensors'
        if path.exists():b=fresh_shard(path,r,lh);need(b['cache_sha256']==sha(cache),'FRESH_CACHE_DRIFT');continue
        if models is None:models=load_models(a,x,rows)
        comp,rr,nets,model,tok,S,ctrl=models;p=B.prepare_prompt(tok,r['prompt']);ori=m.capture(model,p,None,a.device);ref=m.capture(model,p,S,a.device);states={f'original_{k}':v for k,v in ori.items()};states.update({f'S_{k}':v for k,v in ref.items() if k!=0});m.save(cache,states,{'case_id':r['case_id'],'prompt_sha256':r['prompt_sha256']});normal=collect(a,x,models,states,p)
        pp={arm:predict_policy(normal,arm,s['model']) for arm in ARMS};ab={}
        for mode in ('zero_all','shuffle_tokens','embedding_only','S_replaced'):
            q=collect(a,x,models,states,p,mode,SEED+i);ab[mode]={'evidence':q,'predictions':{k:predict_policy(q,k,s['model']) for k in sorted({'baseline_0115',s['winner']})}}
        # Fixed normal endpoints, independent of G1 ablation. For joint winner,
        # the comparable lane uses original normal G1 evidence and corrupted G2 states.
        # This is a causal intervention, not a deployable policy.
        fixed={}
        for mode in ('zero_all','shuffle_tokens','embedding_only','S_replaced'):
            st=I.transform(states,p,mode,SEED+i);q={'nodes':normal['nodes'],'decode_errors':normal['decode_errors'],'fixed':{k:surface_scores(st,p,v['view'],nets,x[4]) for k,v in normal['fixed'].items()},'lattice':surface_scores(st,p,normal['lattice']['view'],nets,x[4])};fixed[mode]={'evidence':q,'predictions':{k:predict_policy(q,k,s['model']) for k in sorted({'baseline_0115',s['winner']})}}
        body={'cache_sha256':sha(cache),'prepared':m.pack(p),'normal':normal,'predictions':pp,'end_to_end_ablations':ab,'fixed_g1_ablations':fixed};imm(path,{'seal':lh,'case_id':r['case_id'],'prompt_sha256':r['prompt_sha256'],'body':body,'body_sha256':digest(body)});emit('FRESH_PROGRESS',{'done':i+1,'of':len(rows),'case':r['case_id'],'normal_decoder_errors':normal['decode_errors']})
    if models is not None:frozen(models)
    need(setup(a)[2]==ident,'PARENT_DRIFT');imm(d/'prediction-lock.json',{'seal':lh,'shards':[sha(d/'predictions'/f'{i:06d}.json') for i in range(len(rows))],'answer_guard':'inherited_python_audit_hook','reader_training':False});emit('LOCKED',{'cases':len(rows)})

def post_lock_oracle(a,x,rows,bodies,d):
    # Diagnostic only: operational predictions have already been hash-locked.
    # Gold endpoints do not participate in the chosen joint policy or in fit.
    nets=L.readers_for(a,x[1],x[4]);records=[];bad=[];groups=co.defaultdict(list)
    for i,(r,b) in enumerate(zip(rows,bodies)):
        p=m.unpack(b['prepared']);view,missing=L.oracle_view(r,p,int(L.GH.DEFAULT_MAX_SPAN_TOKENS))
        if view is None:
            bad.append({'case_id':r['case_id'],'objects':missing});continue
        path=d/'cache'/f'{i:06d}.safetensors';need(sha(path)==b['cache_sha256'],'ORACLE_CACHE_DRIFT');v=surface_scores(load_file(str(path)),p,view,nets,x[4],context=False);g=L.gold(r);u=L.edge_set(v['union']);raw=L.edge_set(v['raw_top1']);predicted=L.edge_set(v['baseline']['new_integrated']);records.append({'case_id':r['case_id'],'cohort':r['cohort'],'gold_edges':len(g),'individual_union_gold':len(g&u),'raw_top1_gold':len(g&raw),'selected_gold':len(g&predicted),'absent_from_union':L.jsedges(g-u),'present_but_rejected':L.jsedges((g&u)-predicted),'baseline':v['baseline']});groups[r['cohort']].append((r,v));emit('POST_LOCK_ORACLE',{'case':r['case_id'],'gold_edges':len(g),'inventory_gold':len(g&u),'selected_gold':len(g&predicted)})
    return {'scope':'POST_LOCK_GOLD_ENDPOINTS_DIAGNOSTIC_NOT_OPERATIONAL','prediction_lock_sha256':sha(d/'prediction-lock.json'),'unalignable':bad,'cases':records,'cohorts':{k:{'baseline':L.measure([r for r,v in q],[v['view']['objects'] for r,v in q],[L.edge_set(v['baseline']['new_integrated']) for r,v in q]),'gold_edges':sum(len(L.gold(r)) for r,v in q),'inventory_gold':sum(len(L.gold(r)&L.edge_set(v['union'])) for r,v in q)} for k,q in groups.items()}}

def score(a,setup_data):
    x,out,ident,pr=setup_data;s,d,z,rows=check_fresh(out,ident,True);lh=sha(d/'seal.json');lock=read(d/'prediction-lock.json');need(lock['seal']==lh and lock['shards']==[sha(d/'predictions'/f'{i:06d}.json') for i in range(len(rows))],'PREDICTION_LOCK_DRIFT');gold=read(d/'answers.SECRET.json');need(len(gold)==len(rows),'ANSWER_COUNT');bodies=[]
    for i,(r,g) in enumerate(zip(rows,gold)):
        need((r['case_id'],r['prompt_sha256'])==(g['case_id'],g['prompt_sha256']),'ANSWER_ALIGNMENT');r['graph']=g['graph'];b=fresh_shard(d/'predictions'/f'{i:06d}.json',r,lh);need(b['cache_sha256']==sha(d/'cache'/f'{i:06d}.safetensors'),'CACHE_DRIFT');bodies.append(b)
    result={'identity':ident,'winner':s['winner'],'selection':sha(out/'selection.json'),'seal':lh,'lock':sha(d/'prediction-lock.json'),'cohorts':{},'certification':False,'g3_unlocked':False,'production_promotion':False,'reader_training':False,'small_admission_training':True,'limitations':['author holdout is small and structural novelty is not exhaustively proven','implicit ownership and ordered quantity annotations are exploratory','one token mapping per lattice object may restrict compatible reconstruction','lattice G2 context includes competing/overlapping object hypotheses and differs from training distribution','ablation scores measure frozen-pipeline sensitivity, not pure semantic capacity','Python answer-open guard is not OS isolation']}
    for cohort in ('all','heldout_explicit','heldout_implicit_ownership','heldout_implicit_quantity'):
        ids=[i for i,r in enumerate(rows) if cohort=='all' or r['cohort']==cohort];rr=[rows[i] for i in ids];bb=[bodies[i] for i in ids];ds=[b['normal'] for b in bb];report={'arms':{},'ablations':{}};result['cohorts'][cohort]=report
        for arm in ARMS:
            z,details=summarize(rr,ds,[b['predictions'][arm] for b in bb]);report['arms'][arm]=z;report['arms'][arm]['case_details']=[dict(case_id=r['case_id'],**q) for r,q in zip(rr,details)]
        for lane in ('end_to_end_ablations','fixed_g1_ablations'):
            report['ablations'][lane]={}
            for mode in bb[0][lane]:
                zz=[b[lane][mode] for b in bb];report['ablations'][lane][mode]={k:summarize(rr,[v['evidence'] for v in zz],[v['predictions'][k] for v in zz])[0] for k in zz[0]['predictions']}
        w=report['arms'][s['winner']];b=report['arms']['baseline_0115'];emit('COHORT',{'cohort':cohort,'winner':s['winner'],'baseline_g1':b['g1']['f1'],'selected_g1':w['g1']['f1'],'baseline_g2':b['g2']['f1'],'selected_g2':w['g2']['f1'],'inventory':w['inventory'],'contrasts':w['contrasts'],'strict':w['g2']['strict_surface_graph'],'normal_decoder_errors':w['decoder_errors']})
    result['forensics']=[forensic(r,b['normal']) for r,b in zip(rows,bodies)];result['gold_endpoint_diagnostic']=post_lock_oracle(a,x,rows,bodies,d);w=result['cohorts']['heldout_explicit']['arms'][s['winner']];base=result['cohorts']['heldout_explicit']['arms']['baseline_0115'];result['status']='BLOCKED_NORMAL_DECODER_FAILURE' if result['cohorts']['all']['arms'][s['winner']]['decoder_errors'] else 'DIAGNOSTIC_REVIEW_REQUIRED';result['selected_improves_explicit_g2']=w['g2']['f1']>base['g2']['f1'];imm(out/'result.json',result)
    (out/'RESEARCH_RECORD.md').write_text('# 0117 joint semantic reconstruction\n\nSelected before fresh prediction: `'+s['winner']+'`.\n\nExplicit held-out G1 F1: '+str(w['g1']['f1'])+'; G2 F1: '+str(w['g2']['f1'])+'; strict graphs: '+str(w['g2']['strict_surface_graph'])+'/'+str(w['cases'])+'.\n\nNo reader/backbone updates. Candidate inventory, comparative evidence, ablations and selection provenance are in result.json and development.json. This is not certification.\n',encoding='utf-8');emit('RESULT',{'status':result['status'],'winner':s['winner'],'path':str(out/'result.json'),'certification':False})

def selftest():
    from tools.nanojev_gamer_v1_joint_author_0117 import cases
    rows=cases();need(len(rows)==28,'AUTHOR_COUNT');[audit_graph(r) for r in rows];ct=contrast(rows,[pred(f.gold(r),L.gold(r)) for r in rows]);need(len(ct)==7 and all(v['correct_changes']==v['expected_changes'] and v['both_exact'] and all(i['both_correct'] for i in v['invariance']) for v in ct),'CONTRAST_SELFTEST')
    for base in {r['base_id'] for r in rows}:
        z={r['mode']:r for r in rows if r['base_id']==base};need(f.gold(z['direct'])==f.gold(z['reverse']),'MATCHED_GEOMETRY_SELFTEST')
    # Actual optimizer: relationship reward must rescue a low-vote endpoint;
    # competing overlapping object is incompatible. No answer involved in solve.
    a=(0,2,'actor');b=(3,5,'action');c=(3,6,'action');keys=[a,b,c];v={'objects':f.sorted_objects(keys),'spans':[{'token_first':0,'token_last':0,'kind':'actor'},{'token_first':1,'token_last':1,'kind':'action'},{'token_first':1,'token_last':2,'kind':'action'}]};xhi=[.8,.8,1.,.8,.8,.5,.4,.5]+[1.]*17;xlo=[.30,.4,.5,.2,.5,0.,-.1,0.]+[0.]*17;xe=[1.,.99,.99,.99,.99,.99]+[1.]*NREAD+[.99]*NREAD+[1.]*4
    data={'nodes':[{'object':list(k),'x':x} for k,x in zip(keys,[xhi,xlo,xlo])],'lattice':{'view':v,'candidates':[{'edge':L.jsedges([L.edge(a,b,'owns')])[0],'x':xe}]}};q=solve_joint(data);need(f.objects(q['objects'])=={a,b} and L.edge_set(q['edges'])=={L.edge(a,b,'owns')},'JOINT_RESCUE_SELFTEST')
    model=fit_logistic([([0.,0.],0),([.1,.2],0),([.9,.8],1),([1.,1.],1)]);need(model_score([1.,1.],model)>model_score([0.,0.],model),'FIT_SELFTEST')
    # Real 0116 reader forward on arbitrary features, including empty endpoint view.
    p={'prompt':'AA BB','prompt_start':0,'offsets':[(0,2),(3,5)],'eligible':torch.tensor([True,True])};view={'objects':[[0,2,'actor'],[3,5,'action']],'spans':[{'kind':'actor','token_first':0,'token_last':0},{'kind':'action','token_first':1,'token_last':1}]};states={k:torch.randn(2,8) for k in m.cache_keys()};nets={k:L.Reader(8,k).eval().requires_grad_(False) for k in L.ARMS};spec={'decoders':{k:{'threshold':.2,'margin':0.,'caps':False} for k in nets},'policies':{k:{'arms':list(nets),'rule':'mean','threshold':.2,'margin':0.,'caps':False} for k in I.G2_POLICIES},'caps':{}}
    z=surface_scores(states,p,view,nets,spec);empty=surface_scores(states,p,{'objects':[],'spans':[]},nets,spec)
    need(z['baseline']==I.g2_extract(states,p,view,nets,spec,'normal')['predictions'],'REAL_BASELINE_PARITY');need(not empty['candidates'],'EMPTY_CANDIDATES')
    fixture={'nodes':[{'object':list(a),'x':xhi},{'object':list(b),'x':xhi}],'lattice':z,'fixed':{k:z for k in I.ROUTES},'decode_errors':{}}
    # Exercises report code on real reader output; the gold fixture is separate.
    r={'case_id':'fixture','base_id':'fixture','cohort':'fixture','family':'binding_fragment','mode':'fixture','prompt':'AA BB','graph':{'nodes':[{'id':'a','kind':'actor','span':[0,2]},{'id':'b','kind':'action','span':[3,5]}],'edges':[{'src':'a','dst':'b','type':'owns'}]}}
    fixture['nodes']=[{'object':v,'x':xhi} for v in view['objects']]
    ov,missing=L.oracle_view(r,p,4);need(ov==view and not missing,'POST_LOCK_ORACLE_MAPPING')
    for arm in ARMS[:-1]:
        pp=predict_policy(fixture,arm);report,_=summarize([r],[fixture],[pp]);need(report['cases']==1,'REPORT_SELFTEST')
    need(len(joint_pool(fixture))==len(z['candidates']),'POOL_DEDUP');need(bool(forensic(r,fixture)),'FORENSIC_SELFTEST')
    from unittest.mock import patch
    from tempfile import TemporaryDirectory
    p['prompt_sha256']=P.digest_text(p['prompt'])
    ev={k:{'selected':[{'object':list(q),'token_options':[(i,i+1)],'probability':.95,'margin':3.,'competitor_margin':2.} for i,q in enumerate(map(tuple,view['objects']))],'native':view['objects'],'decode_error':None} for k in f.ARMS}
    cx=[None,None,None,(None,L.BASE_SPEC),spec];mods=(None,None,nets,None,None,None,None);args=SimpleNamespace(device='cpu')
    with patch.object(sys.modules[__name__],'capture_g1',return_value=ev):
        zz=collect(args,cx,mods,states,p);need(zz['fixed']['0115_selected']['view']==view and zz['lattice']['view']==view,'COLLECT_VIEW_DRIFT');need(zz['fixed']['0115_selected']['baseline']==z['baseline'],'COLLECT_BASELINE_DRIFT')
        for mode in ('zero_all','shuffle_tokens','embedding_only','S_replaced'):collect(args,cx,mods,states,p,mode)
    with TemporaryDirectory() as tmp:
        path=Path(tmp)/'answers.SECRET.json';path.write_text('[]',encoding='utf-8');public=Path(tmp)/'prompts.json';public.write_text('[]',encoding='utf-8');I.answer_guard();need(public.read_text(encoding='utf-8')=='[]','PUBLIC_BLOCKED')
        try:path.read_text(encoding='utf-8')
        except PermissionError:pass
        else:raise ValueError(TAG+'_FRESH_ANSWER_ACCESS')
    emit('SELFTEST',{'actual_joint_milp':True,'joint_endpoint_rescue':True,'actual_0116_reader_forward':True,'actual_baseline_parity':True,'scoring_and_forensics':True,'pool_dedup':True,'full_evidence_pipeline_fixture':True,'answer_guard':True,'post_lock_oracle_mapping':True,'empty_view':True,'admission_fit':True,'author_cases':len(rows),'author_contrasts':len(ct),'cuda_tested':False,'runtime_checkpoints_tested':False})

def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('command',choices=('audit','selftest','develop','prepare','predict','score','run'));ap.add_argument('--root',default=m.ROOT);ap.add_argument('--device',default='cuda');a=ap.parse_args();torch.set_num_threads(min(4,torch.get_num_threads()))
    if a.command=='selftest':selftest();return
    if a.command=='run':
        for phase in ('develop','prepare','predict','score'):
            subprocess.run([sys.executable,'-u','-m',MODULE,phase,'--root',str(Path(a.root).resolve()),'--device',a.device],check=True)
        return
    if a.command=='predict':I.answer_guard()
    st=setup(a)
    if a.command=='audit':emit('AUDIT',{'ready':True,'identity':st[2],'development_cases':len(st[0][6])+sum(r['cache_index'] is None for r in st[3]),'fresh_cases':28,'frozen_readers':True,'g3_unlocked':False});return
    if a.command=='develop' and (st[1]/'selection.json').exists():selection(st[1],st[2]);emit('DEVELOPMENT_REUSED',{'winner':read(st[1]/'selection.json')['winner']});return
    {'develop':develop,'prepare':prepare,'predict':predict,'score':score}[a.command](a,st)
if __name__=='__main__':main()
