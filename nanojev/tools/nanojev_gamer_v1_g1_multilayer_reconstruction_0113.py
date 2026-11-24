"""G1-0113: cached, matched multilayer semantic reconstruction bakeoff.
Research only. Requires completed 0110/0111/0112. No canonical writes.
audit -> selftest -> cache --device cuda -> fit --device cuda (no Qwen load).
run performs cache then fit. Cache and completed arms are resumable.
"""
from __future__ import annotations
import argparse, collections, copy, hashlib, inspect, json, math, os, random, shutil, tempfile
from pathlib import Path
from types import SimpleNamespace
import torch
from torch import nn
from torch.nn import functional as F
from safetensors import safe_open
from safetensors.torch import save_file, load_file
from tools import nanojev_gamer_v1_g1_backbone_transmission_0112 as t

P=t.prior; B=t.B; C=t.C; GH=t.GH
TAG='G1_0113_MULTILAYER'; SCHEMA='nanojev.gamer.g1.multilayer-reconstruction-0113.v1'
ROOT=t.ROOT; SEEDS={'train':1131026,'validation':1132026,'test':1133026}
LAYERS=(0,7,14,21,28); SOURCES=('original','S')
# Same full G1 architecture, initialization, update count and loss across layer arms.
# L0 is the embedding-only control (original token embeddings, no reference state).
ARMS={'original_frozen':('original',(28,),'frozen'),'S_frozen':('S',(28,),'frozen'),
      'original_residual28':('original',(28,),'residual'),'S_residual28':('S',(28,),'residual'),
      'original_embedding':('original',(0,),'full')}
for src in SOURCES:
    for ls in ((7,),(14,),(21,),(28,),(14,21),(7,14,21,28)):
        ARMS[src+'_L'+'_'.join(map(str,ls))]=(src,ls,'full')

def need(ok,msg):
    if not ok:raise ValueError(TAG+'_'+msg)
def emit(ev,rec):print(TAG+'_'+ev+' '+json.dumps(rec,sort_keys=True,allow_nan=False),flush=True)
def sha(p):return B.sha256(Path(p))
def read(p):return json.loads(Path(p).read_text(encoding='utf-8'))
def write(p,obj):
    p=Path(p); p.parent.mkdir(parents=True,exist_ok=True)
    fd,tmp=tempfile.mkstemp(prefix=p.name+'.',suffix='.tmp',dir=p.parent)
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as f:
            json.dump(obj,f,sort_keys=True,ensure_ascii=False,allow_nan=False,indent=2);f.write('\n');f.flush();os.fsync(f.fileno())
        os.replace(tmp,p)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)
def save(p,weights,meta=None):
    p=Path(p);fd,tmp=tempfile.mkstemp(prefix=p.name+'.',suffix='.tmp',dir=p.parent);os.close(fd)
    try:
        save_file({k:v.detach().cpu().contiguous() for k,v in weights.items()},tmp,metadata=meta)
        os.replace(tmp,p)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)
def digest(obj):return hashlib.sha256(json.dumps(obj,sort_keys=True,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode()).hexdigest()

def cohorts(a):
    lab=P.fuzz();groups={}
    for split,n in [('train',a.train_bases),('validation',a.validation_bases),('test',a.test_bases)]:
        need(n>=4 and n%2==0,'BASE_COUNTS_EVEN_AT_LEAST_FOUR')
        modes=P.TRAIN_MODES if split=='train' else P.TEST_MODES
        rows=[]
        for i in range(n):
            base=lab._base(i,seed=SEEDS[split]);base['split']=split
            for mode in modes:
                row=lab._variant(base,mode);need(row['prompt_sha256']==P.digest_text(row['prompt']),'PROMPT_HASH');rows.append(row)
        groups[split]=rows
    rows=sum(groups.values(),[]);need(len({r['prompt_sha256'] for r in rows})==len(rows),'DUPLICATE_PROMPTS')
    need(len({r['case_id'] for r in rows})==len(rows),'DUPLICATE_CASES')
    for x in groups:
        for y in groups:
            if x!=y:need({r['base_id'] for r in groups[x]}.isdisjoint({r['base_id'] for r in groups[y]}),'BASE_LEAKAGE')
    old=P.check_prior_seal(a.root,groups);paths,rec,sig,ck,lin=t.lineage(a.root,groups)
    # Check both previous 0112 cohorts, not just the published result hash.
    seen=set();seen_base=set();parent={}
    for phase in ('probe','run'):
        d=Path(a.root)/'g1-backbone-transmission-0112'/phase;sp=d/'cohort-seal.json';rp=d/'result.json'
        need(sp.is_file() and rp.is_file(),'COMPLETED_0112_REQUIRED_'+phase)
        report=read(rp);s=read(sp)
        need(report['schema']==t.SCHEMA and report['frozen_g1']==sig and report['prior_0111']==lin and report['cohort_seal_sha256']==sha(sp),'0112_LINEAGE_'+phase)
        need(s['frozen_g1']==sig and s['prior_0111']==lin,'0112_SEAL_LINEAGE_'+phase)
        for rs in s['groups'].values():
            seen.update(r['prompt_sha256'] for r in rs);seen_base.update(r['base_id'] for r in rs)
        parent[phase]={'report_sha256':sha(rp),'seal_sha256':sha(sp)}
    need(not any(r['prompt_sha256'] in seen or r['base_id'] in seen_base for r in rows),'0112_COHORT_REUSE')
    sources={Path(m.__file__).name:sha(m.__file__) for m in (t,P,B,C,GH,lab,P.native)}
    seal={'schema':SCHEMA,'groups':groups,'seeds':SEEDS,'frozen_g1':sig,'prior_0110_seal':old,'prior_0111':lin,'prior_0112':parent,
          'source_sha256':sources,'capture_contract_sha256':digest([inspect.getsource(fn) for fn in (capture,pack,unpack,cache_keys)]),'model_id':rec['model_id'],'layers':list(LAYERS),'reference_sources':list(SOURCES)}
    return seal,paths

def output(a):return Path(a.output) if a.output else Path(a.root)/'g1-multilayer-reconstruction-0113'
def ensure_seal(d,seal):
    d.mkdir(parents=True,exist_ok=True);p=d/'cohort-seal.json'
    if p.exists():need(read(p)==seal,'SEALED_COHORT_DRIFT_USE_NEW_OUTPUT')
    else:write(p,seal)
    return sha(p)

def capture(model,p,ref,device):
    ids=p['ids'].to(device);mask=p['attention'].to(device);prefix=int(ref is not None)
    with torch.no_grad():
        if prefix:
            emb=model.get_input_embeddings()(ids)
            need(ref.numel()==emb.shape[-1],'REF_DIM')
            x=torch.cat((ref.to(emb).reshape(1,1,-1),emb),1)
            att=torch.cat((torch.ones_like(mask[:,:1]),mask),1)
            out=model(inputs_embeds=x,attention_mask=att,output_hidden_states=True,use_cache=False)
        else:out=model(input_ids=ids,attention_mask=mask,output_hidden_states=True,use_cache=False)
        hs=out.hidden_states;need(len(hs)==29,'EXPECTED_QWEN_28_LAYERS')
        states={k:hs[k][0,prefix:].detach().cpu().contiguous() for k in LAYERS}
        need(all(v.ndim==2 and v.shape[0]==ids.shape[1] and bool(torch.isfinite(v).all()) for v in states.values()),'INVALID_CAPTURE')
    return states

def pack(p):
    # No gold, graph, family, or answer in the captured preparation metadata.
    return {k:p[k].tolist() if torch.is_tensor(p[k]) else p[k] for k in ('ids','attention','eligible','offsets','prompt_start','prompt','formatted')}
def unpack(q):
    return dict(q,ids=torch.tensor(q['ids'],dtype=torch.long),attention=torch.tensor(q['attention'],dtype=torch.long),eligible=torch.tensor(q['eligible'],dtype=torch.bool))
def shard_path(d,i):return d/'cache'/f'{i:06d}.safetensors'
def cache_keys():return {f'{src}_{k}' for src in SOURCES for k in LAYERS if src!='S' or k!=0}

def validate_shard(path,record,row):
    need(path.is_file() and sha(path)==record['sha256'],'CACHE_SHARD_DRIFT_'+row['case_id'])
    with safe_open(str(path),framework='pt',device='cpu') as f:
        meta=f.metadata();p=unpack(json.loads(meta['prepared']));n=p['ids'].shape[1]
        need(meta['case_id']==row['case_id'] and meta['prompt_sha256']==row['prompt_sha256'] and P.digest_text(p['prompt'])==row['prompt_sha256'],'CACHE_PROMPT_DRIFT')
        need(set(f.keys())==cache_keys() and p['eligible'].shape==(n,) and len(p['offsets'])==n and p['attention'].shape==p['ids'].shape and record.get('tokens',n)==n,'CACHE_ALIGNMENT')
        need(all(tuple(f.get_slice(k).get_shape())==(n,record['dimension']) for k in f.keys()),'CACHE_SHAPE')
    return p

def cached_data(d,seal,ss,complete=True):
    mpath=d/'cache-manifest.json';m=read(mpath) if mpath.exists() else {'schema':SCHEMA,'seal_sha256':ss,'records':{},'complete':False}
    need(m['schema']==SCHEMA and m['seal_sha256']==ss,'CACHE_MANIFEST_DRIFT')
    rows=sum(seal['groups'].values(),[]);need(set(m['records']).issubset({r['case_id'] for r in rows}),'CACHE_UNKNOWN_CASE')
    if complete:need(m['complete'] and len(m['records'])==len(rows),'CACHE_INCOMPLETE_RUN_CACHE_FIRST')
    data={k:[] for k in seal['groups']}
    i=0
    for split,rs in seal['groups'].items():
        for r in rs:
            path=shard_path(d,i);rec=m['records'].get(r['case_id']);i+=1
            if rec is not None:
                need(rec['file']==path.name,'CACHE_INDEX_DRIFT');p=validate_shard(path,rec,r);data[split].append((r,p,path))
    return m,data

def cache(a,seal,d,ss):
    (d/'cache').mkdir(exist_ok=True);m,_=cached_data(d,seal,ss,complete=False)
    rows=sum(seal['groups'].values(),[])
    if m['complete']:
        need(len(m['records'])==len(rows),'CACHE_FALSE_COMPLETE');emit('CACHE_READY',{'cases':len(rows),'reused':True});return
    model,tok,comp,ctrl,s,sig,lin=t.load(a.root,seal['groups'],a.device)
    need(sig==seal['frozen_g1'] and lin==seal['prior_0111'],'LOAD_LINEAGE_DRIFT')
    need(P.frozen_ok(model,comp.head),'FROZEN_VIOLATION')
    for i,r in enumerate(rows):
        if r['case_id'] in m['records']:continue
        p=B.prepare_prompt(tok,r['prompt']);plain=capture(model,p,None,a.device);ref=capture(model,p,s,a.device)
        need(torch.equal(plain[0],ref[0]),'ORIGINAL_EMBEDDINGS_CHANGED_BY_PREFIX')
        if not m['records']:
            # Confirm exact native final-layer path parity for both arms.
            with torch.no_grad():
                h0=B.frozen_context(model,p,a.device).float().cpu();h1=P.with_reference(model,p,s,a.device).cpu()
            pairs=[(h0,plain[28].float()),(h1,ref[28].float())]
            parity=[{'max_abs':float((x-y).abs().max()),'exact':bool(torch.equal(x,y))} for x,y in pairs]
            for x,y in pairs:
                need(torch.allclose(x,y,rtol=.001,atol=.002),'FINAL_LAYER_PATH_PARITY')
                if not torch.equal(x,y):
                    need(P.decoded(comp,x,p,'unlabeled')==P.decoded(comp,y,p,'unlabeled'),'FINAL_DECODE_PARITY')
            m['canonical_path_parity']=parity;emit('PATH_PARITY',{'original':parity[0],'S':parity[1]})
            estimate=sum(v.numel()*v.element_size() for k,v in plain.items())+sum(v.numel()*v.element_size() for k,v in ref.items() if k!=0)
            emit('CACHE_STORAGE_ESTIMATE',{'bytes_at_first_prompt_length':estimate*len(rows),'free_bytes':shutil.disk_usage(d).free,'note':'actual length varies by prompt'})
        tensors={f'original_{k}':v for k,v in plain.items()};tensors.update({f'S_{k}':v for k,v in ref.items() if k!=0})
        path=shard_path(d,i);save(path,tensors,{'case_id':r['case_id'],'prompt_sha256':r['prompt_sha256'],'prepared':json.dumps(pack(p),ensure_ascii=False)})
        rec={'file':path.name,'sha256':sha(path),'dimension':plain[0].shape[1],'tokens':plain[0].shape[0]}
        validate_shard(path,rec,r);m['records'][r['case_id']]=rec;write(d/'cache-manifest.json',m)
        n=len(m['records'])
        if n==1 or n%8==0 or n==len(rows):emit('CACHE_PROGRESS',{'done':n,'of':len(rows),'bytes':sum(shard_path(d,j).stat().st_size for j in range(i+1) if shard_path(d,j).is_file())})
    need(P.frozen_ok(model,comp.head) and P.fuzz()._source_signature(a.root)==sig,'FROZEN_SOURCE_DRIFT')
    need(t.lineage(a.root,seal['groups'])[4]==lin,'REFERENCE_DRIFT')
    m['complete']=True;write(d/'cache-manifest.json',m);emit('CACHE_READY',{'cases':len(rows),'manifest_sha256':sha(d/'cache-manifest.json'),'reused':False})

class Reader(nn.Module):
    def __init__(self,head,layers,kind):
        super().__init__();self.layers=tuple(layers);self.kind=kind;self.head=copy.deepcopy(head).eval().requires_grad_(False)
        if kind=='full':C.trainable_stage(self.head,'G1')
        self.mix=nn.Parameter(torch.zeros(len(layers)),requires_grad=kind=='full' and len(layers)>1)
        self.residual=t.Reader(head.hidden_size) if kind=='residual' else nn.Identity()
    def hidden(self,states):
        need(len(states)==len(self.layers),'READER_LAYER_COUNT')
        if len(states)==1:h=states[0].float()
        else:
            # Per-token normalization avoids domination by arbitrary layer scale.
            # No layer uses other layers not explicitly listed in this arm.
            norm=[F.layer_norm(x.float(),(x.shape[-1],)) for x in states]
            h=sum(w*x for w,x in zip(self.mix.softmax(0),norm))
        return self.residual(h)
    def forward(self,states,p):return self.head(self.hidden(states),eligible=p['eligible'].to(states[0].device),active_stage='G1')

def load_states(item,arm,device):
    _,_,path=item;src,ls,_=ARMS[arm]
    with safe_open(str(path),framework='pt',device='cpu') as f:return [f.get_tensor(f'{src}_{k}').to(device=device,dtype=torch.float32) for k in ls]

def native_objects(raw,p):
    spans=GH.decode_neural_spans(raw['span_logits'],raw['span_valid']);out=[]
    for s in spans:
        b=GH.token_span_char_bounds(s,p)
        if b is not None:out.append([int(b[0]),int(b[1]),s['kind']])
    return out

def observe(comp,reader,item,states,include_native=True):
    r,p,_=item
    # No row fields except prompt coordinates enter the decoder; family is
    # deliberately unlabeled. The author graph is used only AFTER predictions.
    with torch.no_grad():
        h=reader.hidden(states);view=copy.copy(comp);view.head=reader.head
        result={};errors={}
        try:result['composite']=P.decoded(view,h,p,'unlabeled')
        except ValueError as e:
            if str(e) not in ('G1_0082_UNMAPPABLE_CHAR_OBJECT','G1_0082_UNMAPPABLE_NONOVERLAP_TOKEN_SPANS','G1_0082_INVALID_SELECTED_OBJECT','G1_0082_OVERLAPPING_CHAR_OBJECTS','GAMER_GRAPH_TOO_MANY_PREDICTED_OBJECTS'):raise
            errors['composite']=str(e);result['composite']=[]
        if include_native:
            try:result['native']=native_objects(reader.head(h,eligible=p['eligible'].to(h.device),active_stage='G1'),p)
            except ValueError as e:
                if str(e)!='GAMER_GRAPH_TOO_MANY_PREDICTED_OBJECTS':raise
                errors['native']=str(e);result['native']=[]
    return {name:{'counts':P.count(r,objs),'edge_reachability':t.edge_availability(r,objs),'predicted':objs,'decode_error':errors.get(name),'type_errors':type_errors(r,objs)} for name,objs in result.items()}

def type_errors(row,objs):
    gold=collections.Counter((n['span'][0],n['span'][1],n['kind']) for n in row['graph']['nodes'] if 'span' in n)
    pred=collections.Counter(tuple(x) for x in objs);c=collections.Counter()
    for key,n in (gold-pred).items():c['gold_not_exact|'+key[2]]+=n
    for key,n in (pred-gold).items():c['extra|'+key[2]]+=n
    return dict(c)

def summarize(rows,decoder='composite'):
    out=P.summarize([r['decoders'][decoder]['counts'] for r in rows]);e=collections.Counter()
    for r in rows:e.update(r['decoders'][decoder]['edge_reachability'])
    out.update(e); types=collections.Counter()
    for r in rows:types.update(r['decoders'][decoder].get('type_errors',{}))
    out['object_errors_by_kind']=dict(types);out['decode_errors']=sum(r['decoders'][decoder]['decode_error'] is not None for r in rows)
    return out

def evaluate(comp,reader,rows,arm,device,native=True,progress=False):
    reader.eval();out=[]
    for i,item in enumerate(rows,1):
        r=item[0];pred=observe(comp,reader,item,load_states(item,arm,device),native)
        out.append({k:r[k] for k in ('case_id','base_id','family','mode')}|{'decoders':pred})
        if progress and (i==1 or i%32==0 or i==len(rows)):emit('EVAL',{'arm':arm,'done':i,'of':len(rows)})
    return out

def quality(s):return (s['core_exact'],s['exact'],s['f1'],-s['decode_errors'])
def loss_one(reader,item,states,labels):
    r,p,_=item;return C.stage_loss(reader.head,reader(states,p),labels,r,'G1')

def fit_arm(comp,data,arm,a,d):
    src,ls,kind=ARMS[arm];torch.manual_seed(11304)
    reader=Reader(comp.head,ls,kind).to(a.device);trainable=[p for p in reader.parameters() if p.requires_grad]
    hist=[];best=None;selected=0
    if trainable:
        optimizer=torch.optim.AdamW(trainable,lr=a.lr if kind=='full' else a.residual_lr,weight_decay=0.)
        rng=random.Random(11305);order=list(range(len(data['fit_train'])))
        for epoch in range(1,a.epochs+1):
            reader.train();rng.shuffle(order);total=0.
            for j,i in enumerate(order,1):
                item,labels=data['fit_train'][i];optimizer.zero_grad(set_to_none=True)
                loss=loss_one(reader,item,load_states(item,arm,a.device),labels)
                need(bool(torch.isfinite(loss)),'NONFINITE_LOSS_'+arm);loss.backward()
                grad=torch.nn.utils.clip_grad_norm_(trainable,1.);need(bool(torch.isfinite(grad)),'NONFINITE_GRAD_'+arm)
                optimizer.step();total+=float(loss.detach().cpu())
                if j==1 or j%32==0 or j==len(order):emit('LEARN',{'arm':arm,'epoch':epoch,'step':j,'of':len(order),'mean_loss':total/j,'grad_norm':float(grad)})
            val=summarize(evaluate(comp,reader,data['validation'],arm,a.device,native=False));key=quality(val)
            hist.append({'epoch':epoch,'mean_loss':total/len(order),'validation':val})
            if best is None or key>best:
                best=key;selected=epoch;save(d/'selected.safetensors',reader.state_dict())
            emit('VALIDATION',{'arm':arm,'epoch':epoch,'selected_epoch':selected,'metrics':val})
        reader.load_state_dict(load_file(str(d/'selected.safetensors'),device='cpu'),strict=True)
    else:
        val=summarize(evaluate(comp,reader,data['validation'],arm,a.device,native=False));hist.append({'epoch':0,'validation':val})
        save(d/'selected.safetensors',reader.state_dict())
    reader.eval().requires_grad_(False)
    validation=next(x['validation'] for x in hist if x['epoch']==selected)
    return reader,{'arm':arm,'source':src,'layers':list(ls),'reader':kind,'trainable_parameters':sum(p.numel() for p in trainable),'selected_epoch':selected,'validation':validation,'history':hist,'mix_weights':reader.mix.softmax(0).detach().cpu().tolist(),'checkpoint_sha256':sha(d/'selected.safetensors')}

def paired(candidate,baseline):
    need([r['case_id'] for r in candidate]==[r['case_id'] for r in baseline],'PAIRED_ORDER')
    clusters=collections.defaultdict(list);g=h=eg=eh=0
    for a,b in zip(candidate,baseline):
        x=a['decoders']['composite']['counts'];y=b['decoders']['composite']['counts']
        g+=bool(x['core_exact'] and not y['core_exact']);h+=bool(y['core_exact'] and not x['core_exact']);eg+=bool(x['exact'] and not y['exact']);eh+=bool(y['exact'] and not x['exact'])
        clusters[a['base_id']].append(x['core_exact']-y['core_exact'])
    vals=[(sum(v),len(v)) for v in clusters.values()];rng=random.Random(11306);samples=[]
    for _ in range(2000):
        draw=rng.choices(vals,k=len(vals));samples.append(sum(v[0] for v in draw)/sum(v[1] for v in draw))
    samples.sort()
    return {'core_gain':g,'core_harm':h,'exact_gain':eg,'exact_harm':eh,'base_gain':sum(v[0]>0 for v in vals),'base_harm':sum(v[0]<0 for v in vals),'base_tie':sum(v[0]==0 for v in vals),'base_cluster_bootstrap_core_delta_95pct':[samples[50],samples[1949]]}

def fit(a,seal,paths,d,ss):
    m,data=cached_data(d,seal,ss);need(a.fit_name not in ('cache','.','..') and Path(a.fit_name).name==a.fit_name and '/' not in a.fit_name and '\\' not in a.fit_name,'INVALID_FIT_NAME');fd=d/a.fit_name;fd.mkdir(exist_ok=True)
    cfg={'schema':SCHEMA,'seal_sha256':ss,'cache_manifest_sha256':sha(d/'cache-manifest.json'),'implementation_sha256':sha(__file__),'epochs':a.epochs,'lr':a.lr,'residual_lr':a.residual_lr,'arms':{k:[v[0],list(v[1]),v[2]] for k,v in ARMS.items()}}
    if (fd/'config.json').exists():need(read(fd/'config.json')==cfg,'FIT_CONFIG_DRIFT_USE_NEW_OUTPUT')
    else:write(fd/'config.json',cfg)
    if (fd/'result.json').exists():
        report=read(fd/'result.json');need(report['config']==cfg,'RESULT_CONFIG_DRIFT');emit('SAVED',{'report':str(fd/'result.json'),'sha256':sha(fd/'result.json'),'reused':True});return
    # Deliberately do not instantiate tokenizer or Qwen in fit.
    comp=P.native.load_composite(paths['composite'],device=a.device);comp.head.eval().requires_grad_(False)
    data['fit_train']=[];bad=[]
    for item in data['train']:
        try:labels=C.aligned_labels(item[1],item[0]);data['fit_train'].append((item,labels))
        except ValueError as e:
            if str(e)!='GAMER_GRAPH_GOLD_TOKEN_ALIGNMENT_FAILURE':raise
            bad.append(item[0]['case_id'])
    need(len(data['fit_train'])>=.8*len(data['train']),'TOO_MANY_UNALIGNABLE_TRAIN_CASES')
    emit('FIT_START',{'train':len(data['fit_train']),'validation':len(data['validation']),'test':len(data['test']),'arms':len(ARMS),'backbone_loaded':False,'unalignable_train':bad})
    fitted={}
    # Fit and select every arm using only train/validation. Do not evaluate any
    # test case until all selections and the global validation winner are sealed.
    for arm in ARMS:
        ad=fd/arm;ad.mkdir(exist_ok=True);rp=ad/'training.json'
        if rp.exists():
            rec=read(rp);need(rec['config_sha256']==digest(cfg) and rec['arm']==arm and rec['checkpoint_sha256']==sha(ad/'selected.safetensors'),'TRAINED_ARM_DRIFT_'+arm)
        else:
            reader,rec=fit_arm(comp,data,arm,a,ad);rec['config_sha256']=digest(cfg);write(rp,rec);del reader
        fitted[arm]=rec
    winner=max(ARMS,key=lambda name:quality(fitted[name]['validation']))
    selection={'config_sha256':digest(cfg),'winner':winner,'criterion':['core_exact','exact','f1','negative_decode_errors'],'checkpoints':{k:v['checkpoint_sha256'] for k,v in fitted.items()}}
    sp=fd/'selection.json'
    if sp.exists():need(read(sp)==selection,'SELECTION_DRIFT')
    else:write(sp,selection)
    emit('SELECTION',selection)
    cases={};summaries={}
    for arm,(src,ls,kind) in ARMS.items():
        ad=fd/arm;ep=ad/'test.json'
        if ep.exists():
            rec=read(ep);need(rec['selection_sha256']==sha(sp) and rec['checkpoint_sha256']==fitted[arm]['checkpoint_sha256'] and rec['cases_sha256']==digest(rec['cases']),'TEST_ARM_DRIFT');rows=rec['cases']
        else:
            reader=Reader(comp.head,ls,kind).to(a.device);reader.load_state_dict(load_file(str(ad/'selected.safetensors'),device='cpu'),strict=True);reader.eval().requires_grad_(False)
            rows=evaluate(comp,reader,data['test'],arm,a.device,progress=True)
            write(ep,{'selection_sha256':sha(sp),'checkpoint_sha256':fitted[arm]['checkpoint_sha256'],'cases_sha256':digest(rows),'cases':rows});del reader
        need([r['case_id'] for r in rows]==[r[0]['case_id'] for r in data['test']],'TEST_CASE_ORDER')
        cases[arm]=rows;summary={k:summarize(rows,k) for k in ('composite','native')}
        summary['by_mode']={mode:summarize([r for r in rows if r['mode']==mode]) for mode in P.TEST_MODES}
        summary['by_family']={f:summarize([r for r in rows if r['family']==f]) for f in ('allocation','encounter')}
        summaries[arm]=summary;emit('ARM_RESULT',{'arm':arm,**summary['composite'],'native_f1':summary['native']['f1']})
    for arm in ARMS:
        summaries[arm]['paired_vs_original']=paired(cases[arm],cases['original_frozen'])
        summaries[arm]['paired_vs_S']=paired(cases[arm],cases['S_frozen'])
    # Compare each matched S/no-S arm directly, and report the observed oracle
    # ceiling separately; test gold is NEVER a deployed route selector.
    comparisons={}
    for name in ARMS:
        other='original'+name[1:] if name.startswith('S_') else None
        if other in ARMS:comparisons[name+'_vs_'+other]=paired(cases[name],cases[other])
    oracle=sum(any(cases[name][i]['decoders']['composite']['counts']['core_exact'] for name in ARMS) for i in range(len(data['test'])))
    need(all(not p.requires_grad and p.grad is None for p in comp.head.parameters()),'CANONICAL_HEAD_GRADIENT')
    need(P.fuzz()._source_signature(a.root)==seal['frozen_g1'] and t.lineage(a.root,seal['groups'])[4]==seal['prior_0111'],'FROZEN_LINEAGE_CHANGED')
    report={'schema':SCHEMA,'config':cfg,'selection':selection,'training':fitted,'training_unalignable':bad,'arms':summaries,'matched_S_comparisons':comparisons,'oracle_core_ceiling':oracle,'test_cases':len(data['test']),'test_bases':a.test_bases,'test_used_for_training':False,'test_used_for_selection':False,'backbone_loaded_during_fit':False,'production_promotion':False,'g3_unlocked':False,'scope':'synthetic base-disjoint families; edge reachability is not G2 accuracy; intermediate readers are research copies, not canonical replacements'}
    write(fd/'result.json',report);emit('RESULT',{'validation_selected':winner,'test':{k:v['composite'] for k,v in summaries.items()},'oracle_core_ceiling':oracle});emit('SAVED',{'report':str(fd/'result.json'),'sha256':sha(fd/'result.json'),'production_promotion':False})

def selftest():
    torch.manual_seed(113);torch.set_num_threads(1)
    class Tiny(nn.Module):
        def __init__(self):super().__init__();self.emb=nn.Embedding(20,16);self.mix=nn.Linear(16,16)
        def get_input_embeddings(self):return self.emb
        def forward(self,*,inputs_embeds=None,input_ids=None,attention_mask=None,output_hidden_states=True,use_cache=False):
            x=self.emb(input_ids) if inputs_embeds is None else inputs_embeds;n=torch.arange(1,x.shape[1]+1,device=x.device).view(1,-1,1);hs=[x]
            for _ in range(28):x=self.mix(x+x.cumsum(1)/n).tanh();hs.append(x)
            return SimpleNamespace(hidden_states=tuple(hs))
    class FixtureComposite:
        def __init__(self,head):self.head=head
        def predict(self,h,p,*,family,native_stage):
            assert family=='unlabeled' and native_stage=='G1';raw=self.head(h,eligible=p['eligible'],active_stage='G1')
            return {'token_spans':GH.decode_neural_spans(raw['span_logits'],raw['span_valid'])}
    model=Tiny().eval().requires_grad_(False);head=GH.GamerGraphHead(hidden_size=16,width=16,layers=1,max_span_tokens=4).eval().requires_grad_(False);comp=FixtureComposite(head)
    p={'ids':torch.tensor([[1,2,3,4,5,6]]),'attention':torch.ones(1,6,dtype=torch.long),'eligible':torch.tensor([False,True,True,True,True,False]),'offsets':[(0,0),(0,5),(6,8),(9,13),(14,16),(0,0)],'prompt_start':0,'prompt':'Alpha 95 cost 70','formatted':'Alpha 95 cost 70'}
    row={'case_id':'fixture','base_id':'fixture','family':'allocation','mode':'control','prompt':p['prompt'],'prompt_sha256':P.digest_text(p['prompt']),'graph':{'nodes':[{'id':'a','kind':'action','span':[0,5]},{'id':'q','kind':'quantity','span':[6,8]},{'id':'b','kind':'outcome','span':[9,13]},{'id':'r','kind':'quantity','span':[14,16]}],'edges':[{'src':'q','dst':'a','type':'applies_to'}]}}
    a=capture(model,p,None,'cpu');b=capture(model,p,torch.randn(16)*.1,'cpu');need(torch.equal(a[0],b[0]),'EMBEDDING_PARITY');need(a[28].shape==b[28].shape==(6,16),'ALIGNMENT')
    with tempfile.TemporaryDirectory() as folder:
        d=Path(folder);(d/'cache').mkdir();path=shard_path(d,0)
        tensors={f'original_{k}':v for k,v in a.items()}|{f'S_{k}':v for k,v in b.items() if k!=0}
        save(path,tensors,{'case_id':row['case_id'],'prompt_sha256':row['prompt_sha256'],'prepared':json.dumps(pack(p))});rec={'sha256':sha(path),'dimension':16,'file':path.name};q=validate_shard(path,rec,row);need(torch.equal(q['eligible'],p['eligible']),'CACHE_MASK_DRIFT')
        item=(row,q,path);labels=C.aligned_labels(q,row)
        for arm in ARMS:
            src,ls,kind=ARMS[arm];reader=Reader(head,ls,kind);states=load_states(item,arm,'cpu');need(len(states)==len(ls),'LAYER_SELECT')
            if kind!='frozen':
                loss=loss_one(reader,item,states,labels);loss.backward();need(any(v.grad is not None and bool(v.grad.abs().sum()>0) for v in reader.parameters() if v.requires_grad),'MISSING_READER_GRADIENT')
                if len(ls)>1:need(reader.mix.grad is not None,'MIX_GRADIENT')
            res=observe(comp,reader,item,states);need(set(res)=={'composite','native'},'DECODE_ARMS')
        need(all(v.grad is None and not v.requires_grad for v in head.parameters()),'CANONICAL_HEAD_MUTATION')
        # Exercise full fit, validation selection and round-trip without Qwen.
        ad=d/'arm';ad.mkdir();cfg=SimpleNamespace(device='cpu',lr=.0003,residual_lr=.002,epochs=1)
        rd,rep=fit_arm(comp,{'fit_train':[(item,labels)],'validation':[item]},'original_L14_21',cfg,ad)
        need(rep['selected_epoch']==1 and rep['checkpoint_sha256']==sha(ad/'selected.safetensors'),'SELECTED_CHECKPOINT')
        try:validate_shard(path,dict(rec,sha256='bad'),row);raise AssertionError('tamper accepted')
        except ValueError as e:need('CACHE_SHARD_DRIFT' in str(e),'WRONG_TAMPER_REFUSAL')
        z=evaluate(comp,rd,[item],'original_L14_21','cpu');need(paired(z,z)['base_tie']==1,'PAIRING')
    emit('SELFTEST',{'real_G1_head_and_loss':True,'all_reader_arms':len(ARMS),'layer_alignment':True,'cache_hash_tamper_rejected':True,'scoped_cache_handles_and_temp_cleanup':True,'validation_checkpoint_roundtrip':True,'native_and_fixture_composite_decode':True,'frozen_head':True,'actual_qwen_cuda':False,'actual_composite_checkpoint':False})

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('command',choices=('audit','selftest','cache','fit','run'),nargs='?',default='audit');p.add_argument('--root',default=ROOT);p.add_argument('--output');p.add_argument('--fit-name',default='fit');p.add_argument('--device',default='cuda');p.add_argument('--train-bases',type=int,default=16);p.add_argument('--validation-bases',type=int,default=8);p.add_argument('--test-bases',type=int,default=24);p.add_argument('--epochs',type=int,default=2);p.add_argument('--lr',type=float,default=.0003);p.add_argument('--residual-lr',type=float,default=.002)
    a=p.parse_args(argv);need(a.epochs>0 and a.lr>0 and a.residual_lr>0,'INVALID_TRAINING_CONFIG');torch.set_num_threads(min(4,torch.get_num_threads()))
    if a.command=='selftest':selftest();return
    seal,paths=cohorts(a)
    if a.command=='audit':
        emit('AUDIT',{'ready':True,'frozen_g1':seal['frozen_g1'],'prior_0112':seal['prior_0112'],'layers':LAYERS,'arms':ARMS,'cases':{k:len(v) for k,v in seal['groups'].items()},'base_disjoint':True,'production_promotion':False,'fit_requires_backbone':False});return
    d=output(a);ss=ensure_seal(d,seal)
    if a.command in ('cache','run'):cache(a,seal,d,ss)
    if a.command in ('fit','run'):fit(a,seal,paths,d,ss)

if __name__=='__main__':main()
