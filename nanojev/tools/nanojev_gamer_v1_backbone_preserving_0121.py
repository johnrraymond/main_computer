"""0121: matched bare-backbone semantic acquisition, preservation-gated, research only.
No historical adapter in A-E. Historical adapter is used only in H.
"""
from __future__ import annotations
import argparse, collections, hashlib, importlib.util, json, math, os, random, subprocess, sys, tempfile, time
from pathlib import Path
import torch
from torch import nn
from torch.nn import functional as F
from safetensors.torch import save_file, load_file
from tools import nanojev_gamer_v1_backbone_competence_0120 as C

TAG='G12_0121_ACQUISITION'; MODULE='tools.nanojev_gamer_v1_backbone_preserving_0121'
ARMS=('A_bare_readers','B_bare_S','C_bare_SR','D_direct_q','E_new_low_rank_q','H_historical_adapter')
KINDS=('actor','action','quantity','condition','outcome','state','other')
RELS=('owns','refers_to','measures','applies_to'); LAYERS=(14,21,28)
SEED=1212026; SPLITS=('fit','validation','historical_test','exposed0117','fresh_graph','preserve_fit','preserve_dev','preserve_0118','preserve_0119','preserve_0120','fresh_binding')
GRAPH_SPLITS=SPLITS[:5]; BIND_SPLITS=SPLITS[6:]
need=C.need; sha=C.sha; digest=C.digest; read=C.read; imm=C.imm

def emit(k,x):print(TAG+'_'+k+' '+json.dumps(x,sort_keys=True,allow_nan=False),flush=True)
def save(path,values):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True);fd,tmp=tempfile.mkstemp(dir=path.parent,suffix='.tmp');os.close(fd)
    try:save_file({k:v.detach().cpu().contiguous() for k,v in values.items()},tmp);os.replace(tmp,path)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)
def fingerprint(p):
    h=hashlib.sha256();p=Path(p);files=sorted(x for x in p.rglob('*') if x.is_file());need(files and (p/'adapter_config.json').is_file(),'ADAPTER_FILES')
    for x in files:
        rel=x.relative_to(p).as_posix().encode();h.update(len(rel).to_bytes(4,'big')+rel);h.update(bytes.fromhex(sha(x)))
    return h.hexdigest()
def ph(s):return hashlib.sha256(s.encode()).hexdigest()

def context(a):
    root=Path(a.root).resolve();out=Path(a.output).resolve() if a.output else root/'g1-g2-backbone-preserving-0121'
    old=root/'g1-g2-backbone-competence-0120';cal=old/'calibration';s=read(cal/'seal.json');r=read(old/'model-receipt.json');lock=read(cal/'prediction-lock.json')
    need(lock['seal']==sha(cal/'seal.json') and lock['model_receipt']==sha(old/'model-receipt.json'),'0120_CALIBRATION_LOCK')
    need(lock['conditions']=={k:sha(cal/'predictions'/k/'lock.json') for k in C.CONDS},'0120_CALIBRATION_CONDITIONS')
    for k in C.CONDS:
        d=cal/'predictions'/k;need(read(d/'lock.json')['shards']==[sha(d/f'{i:04d}.json') for i in range(s['cases'])],'0120_CALIBRATION_SHARDS')
    need(r['model_id']==s['identity']['model_id'] and r['hidden_size']==1024 and r['commit'],'0120_MODEL_IDENTITY')
    adapter=Path(s['identity']['adapter']);need(fingerprint(adapter)==r['adapter_sha256'],'HISTORICAL_ADAPTER_DRIFT')
    h=root/'g1-multilayer-reconstruction-0113'/'cohort-seal.json';need(h.is_file(),'0113_COHORT_REQUIRED')
    j=root/'g1-g2-joint-reconstruction-0117'/'fresh';p=root/'g1-g2-semantic-addressability-0119'/'fit'
    sources={str(x.relative_to(root)):sha(x) for x in [h,old/'model-receipt.json',cal/'prediction-lock.json',j/'seal.json',p/'seal.json',*(old/k/'seal.json' for k in C.SPLITS)]}
    spec=importlib.util.find_spec('tools.nanojev_gamer_v1_g1_semantic_fuzz_lab_0110');need(spec is not None and spec.origin,'0110_GENERATOR_REQUIRED')
    ident={'generator_source':sha(spec.origin),'source':sha(__file__),'0120_source':sha(C.__file__),'model':r,'adapter':str(adapter),'parents':sources,'epochs':a.epochs,'reader_lr':a.reader_lr,'register_lr':a.register_lr,'weight_lr':a.weight_lr,'preservation_weight':a.preservation_weight,'max_kl':a.max_kl,'fresh_bases':a.fresh_bases,'span_limit':a.span_limit,'device':a.device,'seed':SEED,'arms':list(ARMS),'layers':list(LAYERS)}
    return root,out,ident

def public_graph(r):return {k:r[k] for k in ('case_id','base_id','prompt','prompt_sha256','family','mode') if k in r}
def ganswer(r):return {'case_id':r['case_id'],'prompt_sha256':r['prompt_sha256'],'graph':r['graph']}
def parent_rows(folder,answers=True):
    s=read(folder/'seal.json');need(sha(folder/'prompts.json')==s['prompts'],'PARENT_PROMPTS');rr=read(folder/'prompts.json')
    if not answers:return rr
    need(sha(folder/'answers.SECRET.json')==s['answers'],'PARENT_ANSWERS');gg=read(folder/'answers.SECRET.json');need(len(rr)==len(gg),'PARENT_COUNT')
    for r,g in zip(rr,gg):need(r['case_id']==g['case_id'] and r['prompt_sha256']==g['prompt_sha256'],'PARENT_ALIGNMENT')
    return rr,gg

def new_binding():
    # Separate conditional lane: public candidates specify the task, not its answer.
    # Two new instruction constructions, three-way wording differences from old inventory.
    specs=(('refers_to','action','action',('Juniper','Sorrel'),('Garnet','Beryl'),'aliases','options'),('owns','actor','action',('potter','surveyor'),('Citrine','Jasper'),'actors','available actions'),('applies_to','quantity','action',('53','97'),('Auburn','Teal'),'costs','options'),('measures','quantity','outcome',('41','68'),('receipts','deficit'),'values','outcomes'))
    for rel,lk,rk,ls,rs,ll,rl in specs:
        for structure in range(2):
            for flip in (False,True):
                for para in (False,True):
                    text=ll.capitalize()+': ';anchors=[]
                    for side,items,kind in [('l',ls,lk),('r',rs,rk)]:
                        if side=='r':text+='; '+rl+': '
                        for i,w in enumerate(items):
                            if i:text+=', '
                            start=len(text);text+=w;anchors.append({'id':side+str(i),'kind':kind,'span':[start,len(text)]})
                    x,y=rs[int(flip)],rs[1-int(flip)]
                    if structure==0:
                        text+=f'. Neither {ls[0]} nor {ls[1]} shares a partner with the other. Exclude the pairing {ls[0]} with {y}.' if not para else f'. Each entry has a distinct partner. A proposed link between {y} and {ls[0]} was rejected; use the other assignment.'
                    else:
                        text+=f'. The row for {ls[1]} contains {y}. The remaining row contains the unused entry.' if not para else f'. One row lists {y} beside {ls[1]}; no entry is used twice.'
                    base=f'0121-{rel}-{structure}';mode=('reverse' if flip else 'direct')+('_paraphrase' if para else '');case=base+'-'+mode;h=ph(text)
                    yield {'case_id':case,'base_id':base,'mode':mode,'structure':structure,'relation':rel,'prompt':text,'prompt_sha256':h,'anchors':anchors},{'case_id':case,'prompt_sha256':h,'positive':[int((i!=j) if flip else i==j) for i in range(2) for j in range(2)]}

def prepare(a,ctx):
    root,out,ident=ctx;h=read(root/'g1-multilayer-reconstruction-0113'/'cohort-seal.json')['groups'];groups={}
    for dest,src in [('fit','train'),('validation','validation'),('historical_test','test')]:groups[dest]=([public_graph(r) for r in h[src]],[ganswer(r) for r in h[src]])
    rr,gg=parent_rows(root/'g1-g2-joint-reconstruction-0117'/'fresh');groups['exposed0117']=([public_graph(r) for r in rr],gg)
    # The actual installed historical generator, new seed and a separate held-out base population.
    from tools import nanojev_gamer_v1_g1_semantic_fuzz_lab_0110 as lab
    generated=[]
    for i in range(a.fresh_bases):
        base=lab._base(i,seed=1214026);base['split']='test'
        for mode in ('control','alias+forward','pronoun+forward','caps'):
            row=lab._variant(base,mode);row=dict(row,case_id='0121-'+str(row['case_id']),base_id='0121-'+str(row['base_id']));generated.append(row)
    groups['fresh_graph']=([public_graph(r) for r in generated],[ganswer(r) for r in generated])
    old=root/'g1-g2-backbone-competence-0120'
    for dest,src in [('preserve_dev','calibration'),('preserve_0118','exposed0118'),('preserve_0119','exposed0119'),('preserve_0120','fresh')]:groups[dest]=parent_rows(old/src)
    # Distillation uses only public queries from 0119 fit, never the preservation labels.
    rr=parent_rows(root/'g1-g2-semantic-addressability-0119'/'fit',False)
    groups['preserve_fit']=(rr,None)
    z=list(new_binding());groups['fresh_binding']=([r for r,g in z],[g for r,g in z]);C.validate_rows(*groups['fresh_binding'])
    # Check the fresh rows against all accessible historical prompt populations.
    known={r['prompt_sha256'] for k,(rs,_) in groups.items() if k not in ('fresh_graph','fresh_binding') for r in rs}
    for p in (root/'g1-semantic-fuzz-0110'/'pilot'/'seal.jsonl',root/'g1-semantic-fuzz-0110'/'expansion'/'seal.jsonl'):
        if p.exists():
            with p.open(encoding='utf-8') as f:known.update(json.loads(line)['prompt_sha256'] for line in f if line.strip())
    for p in (root/'g1-s-first-reference-0111'/'cohort-seal.json',root/'g1-backbone-transmission-0112'/'probe'/'cohort-seal.json',root/'g1-backbone-transmission-0112'/'run'/'cohort-seal.json'):
        if p.exists():known.update(r['prompt_sha256'] for rs in read(p)['groups'].values() for r in rs)
    for p in (root/'g1-integrated-reconstruction-0115'/'certification'/'prompts.json',root/'g2-multilayer-reconstruction-0116'/'fresh'/'prompts.json',root/'g1-g2-semantic-addressability-0119'/'validation'/'prompts.json',root/'g1-g2-semantic-contrast-0118'/'fit'/'prompts.json',root/'g1-g2-semantic-contrast-0118'/'validation'/'prompts.json'):
        if p.exists():known.update(r['prompt_sha256'] for r in read(p))
    for k in ('fresh_graph','fresh_binding'):need(not known.intersection(r['prompt_sha256'] for r in groups[k][0]),'HISTORICAL_FRESH_COLLISION_'+k)
    emit('FRESH_EXCLUSION',{'known_prompt_hashes':len(known)})
    seen=set();base_seen=set()
    for name in SPLITS:
        rr,gg=groups[name];keys={r['prompt_sha256'] for r in rr};need(len(keys)==len(rr) and all(ph(r['prompt'])==r['prompt_sha256'] for r in rr),'PROMPT_HASH_'+name)
        # Older independent experiments may share exposed prompts across diagnostic lanes.
        if name in ('fit','validation','fresh_graph','fresh_binding'):need(not seen.intersection(keys),'PROMPT_REUSE_'+name)
        seen.update(keys)
        if name in GRAPH_SPLITS:
            for r in rr:need('graph' not in r,'PUBLIC_GRAPH')
        if name in ('fit','validation','fresh_graph'):
            bases={r['base_id'] for r in rr};need(not base_seen.intersection(bases),'BASE_REUSE');base_seen.update(bases)
        d=out/name;imm(d/'prompts.json',rr)
        if gg is not None:imm(d/'answers.SECRET.json',gg)
        imm(d/'seal.json',{'identity':digest(ident),'split':name,'prompts':sha(d/'prompts.json'),'answers':sha(d/'answers.SECRET.json') if gg is not None else None,'cases':len(rr)})
        emit('SEALED',{'split':name,'cases':len(rr)})
    imm(out/'identity.json',ident)

def cohort(ctx,name,answers=False):
    _,out,ident=ctx;d=out/name;s=read(d/'seal.json');need(s['identity']==digest(ident) and s['prompts']==sha(d/'prompts.json'),'COHORT_DRIFT');rr=read(d/'prompts.json');need(len(rr)==s['cases'] and all(ph(r['prompt'])==r['prompt_sha256'] for r in rr),'PUBLIC_DRIFT')
    if not answers:return rr
    need(s['answers']==sha(d/'answers.SECRET.json'),'ANSWER_DRIFT');gg=read(d/'answers.SECRET.json');need(len(rr)==len(gg) and all((r['case_id'],r['prompt_sha256'])==(g['case_id'],g['prompt_sha256']) for r,g in zip(rr,gg)),'ANSWER_ALIGNMENT');return rr,gg

def load_model(a,ctx,arm):
    from transformers import AutoTokenizer, AutoModelForCausalLM
    r=ctx[2]['model'];kw=dict(revision=r['commit'],local_files_only=True,trust_remote_code=False)
    tok=AutoTokenizer.from_pretrained(r['model_id'],use_fast=True,**kw);dtype=torch.float16 if a.device.startswith('cuda') else torch.float32
    model=AutoModelForCausalLM.from_pretrained(r['model_id'],dtype=dtype,**kw).to(a.device).eval().requires_grad_(False)
    need(getattr(model.config,'_commit_hash',None)==r['commit'] and ph(tok.backend_tokenizer.to_str())==r['tokenizer_sha256'] and digest(tok.chat_template)==r['chat_template_sha256'] and str(dtype)==r['dtype'],'MODEL_DRIFT')
    need(not hasattr(model,'peft_config') and model.config.hidden_size==1024,'BARE_REQUIRED')
    if arm=='H_historical_adapter':
        from peft import PeftModel
        model=PeftModel.from_pretrained(model,ctx[2]['adapter'],is_trainable=False).eval().requires_grad_(False)
    return model,tok

class DeltaLinear(nn.Module):
    """Direct additive matrix or newly initialized rank-4 LoRA, same target.
    Stored as an isolated delta, never merges or edits the original weights.
    """
    def __init__(self,base,lowrank):
        super().__init__();self.base=base.requires_grad_(False);self.lowrank=lowrank;d=base.weight.device;i=base.in_features;o=base.out_features
        if lowrank:
            self.A=nn.Parameter(torch.randn(4,i,device=d)*.02);self.B=nn.Parameter(torch.zeros(o,4,device=d))
        else:self.delta=nn.Parameter(torch.zeros(o,i,device=d))
    def forward(self,x):
        y=self.base(x);z=F.linear(F.linear(x.float(),self.A),self.B)*2 if self.lowrank else F.linear(x.float(),self.delta)
        return y+z.to(y.dtype)
    def changes(self):return {k:v for k,v in self.named_parameters() if not k.startswith('base.')}

class Readers(nn.Module):
    def __init__(self,dim,arm,rms,width=64):
        super().__init__();self.arm=arm;self.mix=nn.Parameter(torch.zeros(3));self.project=nn.Linear(dim,width);self.span=nn.Sequential(nn.Linear(4*width,width),nn.GELU(),nn.Linear(width,width),nn.GELU());self.objects=nn.Linear(width,len(KINDS)+1);self.kind=nn.Embedding(len(KINDS),12)
        self.node=nn.Linear(width+12,width);self.binding=nn.Sequential(nn.Linear(5*width,width),nn.GELU(),nn.Linear(width,len(RELS)))
        self.register_buffer('rms',torch.tensor(float(rms)))
        if arm in ('B_bare_S','C_bare_SR'):self.S=nn.Parameter(torch.randn(dim)*.02)
        if arm=='C_bare_SR':self.R=nn.Parameter(torch.randn(dim)*.02)
    def reg(self,name):
        x=getattr(self,name,None)
        return None if x is None else .05*self.rms*x/(x.square().mean()+1e-8).sqrt()
    def spans(self,h,indices):
        z=F.gelu(self.project(sum(w*F.layer_norm(x.float(),(x.shape[-1],)) for w,x in zip(self.mix.softmax(0),h))))
        if len(indices)==0:return z.new_empty((0,z.shape[-1])),z[-1]
        a,b=indices.T;cum=torch.cat((z.new_zeros((1,z.shape[1])),z.cumsum(0)),0);mean=(cum[b+1]-cum[a])/(b-a+1)[:,None];g=z[-1];return self.span(torch.cat((z[a],mean,z[b],g.expand(len(a),-1)),1)),g
    def pairs(self,feat,g,indices,kinds):
        if not len(indices):return feat.new_empty((0,0,len(RELS)))
        z=F.gelu(self.node(torch.cat((feat[indices],self.kind(kinds)),1)));n=len(z);x=z[:,None,:].expand(n,n,-1);y=z[None,:,:].expand(n,n,-1)
        return self.binding(torch.cat((x,y,x*y,x-y,g.expand(n,n,-1)),2))

def setup_arm(a,ctx,arm):
    torch.manual_seed(SEED);model,tok=load_model(a,ctx,arm);rms=float(model.get_input_embeddings().weight.detach().float().square().mean().sqrt());torch.manual_seed(SEED+1)
    net=Readers(model.config.hidden_size,arm,rms).to(a.device);delta=None
    if arm in ('D_direct_q','E_new_low_rank_q'):
        layer=model.model.layers[-1].self_attn;delta=DeltaLinear(layer.q_proj,arm.startswith('E'));layer.q_proj=delta
    return model,tok,net,delta

def changes(net,delta):return {'reader.'+k:v for k,v in net.state_dict().items()}|({'delta.'+k:v for k,v in delta.changes().items()} if delta else {})
def restore(net,delta,path):
    d=load_file(str(path));net.load_state_dict({k[7:]:v for k,v in d.items() if k.startswith('reader.')},strict=True)
    if delta:
        with torch.no_grad():
            for k,v in delta.changes().items():v.copy_(d['delta.'+k].to(v))
    need(set(d)==set(changes(net,delta)),'CHECKPOINT_KEYS')

def freeze_check(model,delta):
    allowed={id(p) for p in delta.changes().values()} if delta else set();need(all(p.requires_grad==(id(p) in allowed) for p in model.parameters()),'UNAUTHORIZED_BACKBONE_TRAINABILITY');need(all(p.grad is None for p in model.parameters() if id(p) not in allowed),'FROZEN_GRADIENT')

def token_prepare(tok,text,limit):
    formatted=tok.apply_chat_template([{'role':'user','content':text}],tokenize=False,add_generation_prompt=False,enable_thinking=False);start=formatted.rfind(text);need(start>=0,'PROMPT_LOST');v=tok(formatted,add_special_tokens=False,return_offsets_mapping=True,return_tensors='pt');ids=v['input_ids'];off=v['offset_mapping'][0].tolist();need(ids.shape[1]<=2048,'PROMPT_TOO_LONG_NO_TRUNCATION')
    valid=[start<=a<b<=start+len(text) for a,b in off];options={}
    for i,ok in enumerate(valid):
        if not ok:continue
        for j in range(i,min(i+limit,len(off))):
            if not valid[j]:break
            a,b=off[i][0]-start,off[j][1]-start
            while a<b and text[a].isspace():a+=1
            while b>a and text[b-1].isspace():b-=1
            if a<b:options.setdefault((a,b),(i,j))
    keys=list(options);return ids,keys,torch.tensor(list(options.values()),dtype=torch.long).reshape(-1,2)

def hidden(model,ids,net,device):
    ids=ids.to(device);S=net.reg('S');R=net.reg('R');prefix=int(S is not None);kw={'output_hidden_states':True,'use_cache':False,'return_dict':True}
    base=model.get_base_model() if hasattr(model,'get_base_model') else model
    # Decoder only: skip full vocabulary logits during graph training.
    decoder=base.model
    if S is not None or R is not None:
        emb=model.get_input_embeddings()(ids);parts=([S.to(emb).reshape(1,1,-1)] if S is not None else [])+[emb]+([R.to(emb).reshape(1,1,-1)] if R is not None else []);x=torch.cat(parts,1);out=decoder(inputs_embeds=x,attention_mask=torch.ones(x.shape[:2],device=x.device,dtype=torch.long),**kw)
    else:out=decoder(input_ids=ids,attention_mask=torch.ones_like(ids),**kw)
    hs=out.hidden_states;need(len(hs)>max(LAYERS),'LAYERS');return [hs[k][0,prefix:] for k in LAYERS]

def gold_graph(g):
    nodes={n['id']:(*n['span'],n['kind']) for n in g['graph']['nodes'] if 'span' in n};objects=set(nodes.values());edges={(nodes[e['src']],nodes[e['dst']],e['type']) for e in g['graph']['edges'] if e['type'] in RELS and e['src'] in nodes and e['dst'] in nodes};return objects,edges

def choose(logits,keys,threshold=0.,limit=256):
    val,kind=(logits[:,1:]-logits[:,:1]).max(1);v=val.detach().cpu().tolist();k=kind.detach().cpu().tolist();sel=[]
    for i in sorted(range(len(keys)),key=lambda i:(-v[i],keys[i])):
        if v[i]<=threshold:break
        a,b=keys[i]
        if all(b<=keys[j][0] or keys[j][1]<=a for j,_ in sel):sel.append((i,k[i]))
        if len(sel)>=limit:break
    return sorted(sel,key=lambda t:keys[t[0]])
def as_obj(keys,sel):return [(*keys[i],KINDS[k]) for i,k in sel]
def pair_scores(net,feat,g,sel):
    d=feat.device;return net.pairs(feat,g,torch.tensor([i for i,k in sel],dtype=torch.long,device=d),torch.tensor([k for i,k in sel],dtype=torch.long,device=d))
def decode(net,feat,g,keys,ot=0.,et=0.,prepared=None):
    if prepared is None:
        s=choose(net.objects(feat),keys,ot,limit=257);overflow=len(s)>256;s=s[:256];obj=as_obj(keys,s);scores=pair_scores(net,feat,g,s)
    else:obj,scores,overflow=prepared
    edges=[]
    for i,j,k in (scores>et).nonzero().tolist():
        if i!=j:edges.append([obj[i],obj[j],RELS[k]])
    return {'objects':obj,'edges':edges,'object_limit_reached':overflow}

def train_graph(net,feat,g,keys,answer):
    gold,ge=gold_graph(answer);lookup={x:i for i,x in enumerate(keys)};s=[];targets=torch.zeros(len(keys),dtype=torch.long,device=feat.device)
    for a,b,k in sorted(gold):
        if (a,b) in lookup and k in KINDS:
            i=lookup[(a,b)];v=KINDS.index(k);need(int(targets[i]) in (0,v+1),'GOLD_SPAN_MULTITYPE');targets[i]=v+1;s.append((i,v))
    raw=net.objects(feat);need(len(keys)>0,'NO_SPANS');l1=F.cross_entropy(raw,targets,weight=raw.new_tensor([1.]+[4.]*len(KINDS)))
    # Include false proposed objects as hard negatives; no oracle use at prediction.
    s=sorted(set(s)|set(choose(raw,keys,limit=32)));objs=as_obj(keys,s);z=pair_scores(net,feat,g,s);y=torch.zeros_like(z)
    index={o:i for i,o in enumerate(objs)}
    for x,w,t in ge:
        if x in index and w in index:y[index[x],index[w],RELS.index(t)]=1
    mask=~torch.eye(len(s),dtype=torch.bool,device=feat.device)
    l2=F.binary_cross_entropy_with_logits(z[mask],y[mask],pos_weight=z.new_full((len(RELS),),8.)) if bool(mask.any()) else z.sum()*0
    return l1+l2

def feature(model,net,p,device,cache=None,key=None):
    ids,keys,idx=p
    if cache is not None and key in cache:h=[x.to(device) for x in cache[key]]
    else:
        h=hidden(model,ids,net,device)
        if cache is not None:cache[key]=[x.detach().cpu() for x in h]
    return (*net.spans(h,idx.to(device)),keys)

def metrics(gg,pred):
    c=collections.Counter()
    for g,p in zip(gg,pred):
        go,ge=gold_graph(g);o={tuple(x) for x in p['objects']};e={(tuple(x),tuple(y),t) for x,y,t in p['edges']};c['cases']+=1;c['object_limit_cases']+=bool(p.get('object_limit_reached',False));c['g1_exact']+=o==go;c['g1_core']+=go<=o;c['g2_exact']+=e==ge;c['strict']+=o==go and e==ge;c['reachable']+=sum(x in o and y in o for x,y,t in ge)
        for k,x,y in [('g1',go,o),('g2',ge,e)]:c[k+'_tp']+=len(x&y);c[k+'_fp']+=len(y-x);c[k+'_fn']+=len(x-y)
    d=dict(c)
    for k in ('g1','g2'):
        tp,fp,fn=[c[k+'_'+x] for x in ('tp','fp','fn')];d[k+'_f1']=2*tp/max(1,2*tp+fp+fn);d[k+'_precision']=tp/max(1,tp+fp);d[k+'_recall']=tp/max(1,tp+fn)
    return d

def conditional(net,feat,g,keys,r):
    lookup={x:i for i,x in enumerate(keys)};anchors={x['id']:x for x in r['anchors']};sel=[]
    for id in ('l0','l1','r0','r1'):
        x=anchors[id];k=tuple(x['span']);
        if k not in lookup:return {'mapped':False,'scores':[0.]*4}
        sel.append((lookup[k],KINDS.index(x['kind'])))
    z=pair_scores(net,feat,g,sel);t=RELS.index(r['relation']);return {'mapped':True,'scores':[float(z[i,j,t]) for i in range(2) for j in (2,3)]}

def native(model,tok,r,net,device):
    S=net.reg('S') if net else None;_,qs,_=C.questions(r);v=[C.likelihood(model,tok,x,('A','B'),device,S) for x in qs];m=(v[0]['wide']['margin']-v[1]['wide']['margin'])/2
    return {'scores':[m,-m,-m,m],'choice':v}
def native_logits(model,tok,text,net,device):return C.forward_first(model,C.chat(tok,text),device,net.reg('S') if net else None)[0].logits[0,-1].float()
def kl(student,teacher):return F.kl_div(student.log_softmax(-1),teacher.to(student).log_softmax(-1),reduction='sum',log_target=True).clamp_min(0)

def baseline(a,ctx):
    _,out,_=ctx;p=out/'bare-preservation.json';t=out/'teacher.safetensors'
    if p.exists():need(read(p)['teacher_sha256']==sha(t) and read(p)['identity']==digest(ctx[2]),'TEACHER_DRIFT');return
    model,tok=load_model(a,ctx,'A_bare_readers');rr=cohort(ctx,'preserve_fit');dev,gg=cohort(ctx,'preserve_dev',True)
    # One direct and one reverse per relation and each of four fit structures, both answer orders.
    teacher_rows=[r for r in rr if r['mode'] in ('direct','reverse')];texts=[q for r in teacher_rows for q in C.questions(r)[1]];devtexts=[q for r in dev for q in C.questions(r)[1]]
    with torch.no_grad():
        tensors={f'fit{i}':native_logits(model,tok,x,None,a.device).cpu() for i,x in enumerate(texts)};tensors.update({f'dev{i}':native_logits(model,tok,x,None,a.device).cpu() for i,x in enumerate(devtexts)});pred=[native(model,tok,r,None,a.device) for r in dev]
    # Replay exact prior calibration through the same likelihood interface.
    prev=ctx[0]/'g1-g2-backbone-competence-0120'/'calibration'/'predictions'/'bare';old=[read(prev/f'{i:04d}.json')['body'] for i in range(len(dev))];prior=[C.rank_scores(b,'choice_wide') for b in old];new=[x['scores'] for x in pred];rank=C.ranks(dev,gg,new);old_rank=C.ranks(dev,gg,prior);drift=max(abs(x-y) for a,b in zip(prior,new) for x,y in zip(a,b));need(old_rank['exact_flags']==rank['exact_flags'] and drift<=.05,'0120_BARE_REPLAY_DRIFT');emit('BARE_REPLAY',{'max_abs_margin_drift':drift,'exact_decision_parity':True})
    save(t,tensors);imm(p,{'replay_max_abs':drift,'teacher_sha256':sha(t),'texts':texts,'devtexts':devtexts,'native':rank,'answers_mass':sum(x['wide']['answer_mass'] for b in pred for x in b['choice'])/(2*len(dev)),'identity':digest(ctx[2])});emit('BARE_PRESERVATION',C.compact(rank));del model

def gate(a,ctx,model,tok,net,teacher,ref):
    rr,gg=cohort(ctx,'preserve_dev',True)
    with torch.no_grad():
        pr=[native(model,tok,r,net,a.device) for r in rr];div=[float(kl(native_logits(model,tok,x,net,a.device),teacher[f'dev{i}'])) for i,x in enumerate(ref['devtexts'])]
    m=C.ranks(rr,gg,[x['scores'] for x in pr]);lost=sum(a and not b for a,b in zip(ref['native']['exact_flags'],m['exact_flags']));mass=sum(x['wide']['answer_mass'] for b in pr for x in b['choice'])/(2*len(rr));mean=sum(div)/len(div)
    return {'native':m,'lost_bare_correct':lost,'mean_kl':mean,'answer_mass':mass,'passed':lost==0 and mean<=a.max_kl and mass>=.9*ref['answers_mass']}

def arm_train(a,ctx,arm):
    _,out,_=ctx;folder=out/'arms'/arm;done=folder/'training.json';selected=folder/'selected.safetensors'
    if done.exists():need(read(done)['checkpoint']==sha(selected) and read(done)['identity']==digest(ctx[2]),'TRAINING_DRIFT');emit('REUSED_ARM',{'arm':arm});return
    folder.mkdir(parents=True,exist_ok=True);model,tok,net,delta=setup_arm(a,ctx,arm);freeze_check(model,delta)
    fit,fg=cohort(ctx,'fit',True);val,vg=cohort(ctx,'validation',True);prep={r['case_id']:token_prepare(tok,r['prompt'],a.span_limit) for r in fit+val}
    # All arms use identical reader initialization, graph order, objective and epochs.
    # Static states cached only when no graph gradient into the transmitter is required.
    cache={} if arm in (ARMS[0],ARMS[-1]) else None
    reg=[p for n,p in net.named_parameters() if n in ('S','R')];heads=[p for n,p in net.named_parameters() if n not in ('S','R')];groups=[{'params':heads,'lr':a.reader_lr}]
    if reg:groups.append({'params':reg,'lr':a.register_lr})
    if delta:groups.append({'params':list(delta.changes().values()),'lr':a.weight_lr})
    opt=torch.optim.AdamW(groups,weight_decay=0.);params=[p for group in groups for p in group['params']]
    ref=read(out/'bare-preservation.json');teacher=load_file(str(out/'teacher.safetensors'));teacher_order=list(range(len(ref['texts'])));random.Random(SEED+99).shuffle(teacher_order);history=[];best=None
    for epoch in range(1,a.epochs+1):
        net.train();order=list(range(len(fit)));random.Random(SEED+epoch).shuffle(order);total=0.;pres=0.;steps=0
        for j,i in enumerate(order):
            r=fit[i];opt.zero_grad(set_to_none=True);f,g,keys=feature(model,net,prep[r['case_id']],a.device,cache,r['case_id']);loss=train_graph(net,f,g,keys,fg[i]);loss.backward();total+=float(loss.detach());
            # Same teacher objective for transmitter-changing arms, not trained on dev/fresh labels.
            if (delta is not None or reg) and (j%4==0):
                tindex=teacher_order[((epoch-1)*math.ceil(len(fit)/4)+j//4)%len(teacher_order)];v=kl(native_logits(model,tok,ref['texts'][tindex],net,a.device),teacher[f'fit{tindex}']);(a.preservation_weight*v).backward();pres+=float(v.detach());steps+=1
            norm=torch.nn.utils.clip_grad_norm_(params,1.);need(bool(torch.isfinite(norm)) and math.isfinite(total),'NONFINITE_TRAINING_'+arm);freeze_check(model,delta);opt.step()
            if j==0 or (j+1)%24==0 or j+1==len(order):emit('LEARN',{'arm':arm,'epoch':epoch,'done':j+1,'of':len(order),'graph_loss':total/(j+1),'preservation_kl':pres/max(steps,1)})
        opt.zero_grad(set_to_none=True);net.eval();grid={(ot,et):[] for ot in (0.,.5,1.) for et in (0.,1.,2.)}
        with torch.no_grad():
            for r in val:
                f,g,keys=feature(model,net,prep[r['case_id']],a.device,cache,r['case_id']);raw=net.objects(f)
                for ot in (0.,.5,1.):
                    s=choose(raw,keys,ot,limit=257);overflow=len(s)>256;s=s[:256];prepared=(as_obj(keys,s),pair_scores(net,f,g,s),overflow)
                    for et in (0.,1.,2.):grid[ot,et].append(decode(net,f,g,keys,ot,et,prepared))
        trial={k:metrics(vg,p) for k,p in grid.items()};thresholds=max(trial,key=lambda k:(trial[k]['strict'],trial[k]['g2_f1'],trial[k]['g1_f1']));vm=trial[thresholds];pg=gate(a,ctx,model,tok,net,teacher,ref);record={'epoch':epoch,'thresholds':list(thresholds),'validation_grid':[{ 'thresholds':list(k),'metrics':v} for k,v in trial.items()],'validation':vm,'preservation':pg,'graph_loss':total/len(fit),'teacher_kl':pres/max(steps,1)};history.append(record);emit('EPOCH',{'arm':arm,**record})
        key=(pg['passed'],vm['strict'],vm['g2_f1'],vm['g1_f1']);
        if best is None or key>best[0]:save(selected,changes(net,delta));best=(key,record)
    imm(done,{'identity':digest(ctx[2]),'arm':arm,'checkpoint':sha(selected),'selected':best[1],'history':history,'trainable_reader_parameters':sum(p.numel() for p in net.parameters()),'trainable_backbone_delta_parameters':sum(p.numel() for p in delta.changes().values()) if delta else 0});freeze_check(model,delta)

def selection(a,ctx):
    _,out,ident=ctx;records={arm:read(out/'arms'/arm/'training.json') for arm in ARMS};need(all(v['identity']==digest(ident) and v['checkpoint']==sha(out/'arms'/k/'selected.safetensors') for k,v in records.items()),'SELECTION_DRIFT');eligible=[k for k,v in records.items() if k!=ARMS[-1] and v['selected']['preservation']['passed']]
    winner=max(eligible,key=lambda k:(records[k]['selected']['validation']['strict'],records[k]['selected']['validation']['g2_f1'],records[k]['selected']['validation']['g1_f1'])) if eligible else None
    x={'identity':digest(ident),'winner':winner,'eligible':eligible,'training':{k:sha(out/'arms'/k/'training.json') for k in ARMS}};imm(out/'selection.json',x);emit('SELECTED',x);return x

def predict(a,ctx,arm):
    _,out,ident=ctx;sel=read(out/'selection.json');folder=out/'arms'/arm;rec=read(folder/'training.json');need(sel['identity']==digest(ident) and sel['training'][arm]==sha(folder/'training.json') and rec['checkpoint']==sha(folder/'selected.safetensors'),'PREDICT_LINEAGE');C.guard()
    model,tok,net,delta=setup_arm(a,ctx,arm);restore(net,delta,folder/'selected.safetensors');net.eval();freeze_check(model,delta);manifest={}
    for split in GRAPH_SPLITS[2:]+BIND_SPLITS:
        rr=cohort(ctx,split);d=folder/'predictions'/split;head={'selection':sha(out/'selection.json'),'seal':sha(out/split/'seal.json'),'checkpoint':rec['checkpoint']};shards=[]
        with torch.no_grad():
            for i,r in enumerate(rr):
                p=d/f'{i:04d}.json'
                if p.exists():z=read(p);need(z['header']==head and z['prompt']==r['prompt_sha256'] and z['digest']==digest(z['body']),'SHARD_DRIFT')
                else:
                    f,g,keys=feature(model,net,token_prepare(tok,r['prompt'],a.span_limit),a.device)
                    body=decode(net,f,g,keys,*rec['selected']['thresholds']) if split in GRAPH_SPLITS else {'conditional':conditional(net,f,g,keys,r),'native':native(model,tok,r,net,a.device)}
                    imm(p,{'header':head,'prompt':r['prompt_sha256'],'body':body,'digest':digest(body)})
                shards.append(sha(p))
                if i==0 or (i+1)%16==0 or i+1==len(rr):emit('PREDICT',{'arm':arm,'split':split,'done':i+1,'of':len(rr)})
        imm(d/'lock.json',{'header':head,'shards':shards});manifest[split]=sha(d/'lock.json')
    imm(folder/'prediction-lock.json',manifest);freeze_check(model,delta)

def score(a,ctx):
    _,out,ident=ctx;sel=read(out/'selection.json');results={};ref=read(out/'bare-preservation.json')
    # Verify every arm and every prediction lock before opening evaluation answers.
    for arm in ARMS:
        folder=out/'arms'/arm;rec=read(folder/'training.json');need(sel['training'][arm]==sha(folder/'training.json') and rec['checkpoint']==sha(folder/'selected.safetensors'),'FINAL_CHECKPOINT_DRIFT');lock=read(folder/'prediction-lock.json')
        for split in GRAPH_SPLITS[2:]+BIND_SPLITS:
            d=folder/'predictions'/split;need(lock[split]==sha(d/'lock.json'),'PREDICTION_LOCK');z=read(d/'lock.json');rr=cohort(ctx,split);need(z['header']=={'selection':sha(out/'selection.json'),'seal':sha(out/split/'seal.json'),'checkpoint':rec['checkpoint']} and z['shards']==[sha(d/f'{i:04d}.json') for i in range(len(rr))],'SHARD_LOCK')
    for arm in ARMS:
        folder=out/'arms'/arm;results[arm]={'development':read(folder/'training.json'),'cohorts':{}}
        for split in GRAPH_SPLITS[2:]+BIND_SPLITS:
            rr,gg=cohort(ctx,split,True);d=folder/'predictions'/split;pp=[]
            for i,r in enumerate(rr):
                z=read(d/f'{i:04d}.json');need(z['prompt']==r['prompt_sha256'] and z['digest']==digest(z['body']),'FINAL_BODY_DRIFT');pp.append(z['body'])
            if split in GRAPH_SPLITS:m=metrics(gg,pp)
            else:m={'conditional':C.ranks(rr,gg,[p['conditional']['scores'] for p in pp]),'mapped':sum(p['conditional']['mapped'] for p in pp),'native':C.ranks(rr,gg,[p['native']['scores'] for p in pp]),'order':C.order_metrics(gg,[{'normal':{'choice':p['native']['choice']}} for p in pp],'wide')}
            results[arm]['cohorts'][split]=m;emit('COHORT',{'arm':arm,'split':split,**m})
    # A is the matched original-transmitter reference. No retrospective winner selection.
    for arm,v in results.items():
        v['heldout_preservation']={s:sum(x and not y for x,y in zip(results[ARMS[0]]['cohorts'][s]['native']['exact_flags'],v['cohorts'][s]['native']['exact_flags'])) for s in BIND_SPLITS}
        v['heldout_native_gains']={s:sum(not x and y for x,y in zip(results[ARMS[0]]['cohorts'][s]['native']['exact_flags'],v['cohorts'][s]['native']['exact_flags'])) for s in BIND_SPLITS}
        v['preservation_review_passed']=v['development']['selected']['preservation']['passed'] and not any(v['heldout_preservation'].values())
    result={'identity':digest(ident),'selection':sel,'results':results,'certification':False,'production_modified':False,'status':'RESEARCH_REVIEW_REQUIRED'};imm(out/'result.json',result)
    lines=['# 0121 — Backbone-preserving acquisition','', 'Research-only. Matched new readers; historical LoRA excluded from A–E.','',f"Validation-selected arm: {sel['winner']}",'','| Arm | G1 fresh F1 | G2 fresh F1 | Strict fresh | Heldout preservation |','|---|---:|---:|---:|---|']
    for arm,v in results.items():
        m=v['cohorts']['fresh_graph'];lines.append(f"| {arm} | {m['g1_f1']:.4f} | {m['g2_f1']:.4f} | {m['strict']}/{m['cases']} | {v['preservation_review_passed']} |")
    lines+=['','Historical graph test and 0117 are exposed diagnostics. Fresh graph uses a new seed of the historical generator; structural novelty tested separately in the conditional lane. Preservation is scoped to the tested bindings, not all language competence. No G3 promotion.']
    (out/'RESEARCH_RECORD.md').write_text('\n'.join(lines)+'\n',encoding='utf-8');emit('RESULT',{'path':str(out/'result.json'),'winner':sel['winner'],'certification':False})

def fixture_backbone():
    # Tiny causal decoder with the same input/output shape contract as the live path.
    from types import SimpleNamespace
    class Decoder(nn.Module):
        def __init__(self,emb):
            super().__init__();self.emb=emb;self.layers=nn.ModuleList([nn.Module()]);self.layers[0].self_attn=nn.Module();self.layers[0].self_attn.q_proj=nn.Linear(8,8,bias=False)
        def forward(self,input_ids=None,inputs_embeds=None,**kw):
            x=self.emb(input_ids) if inputs_embeds is None else inputs_embeds
            v=self.layers[0].self_attn.q_proj(x.cumsum(1)/torch.arange(1,x.shape[1]+1,device=x.device)[None,:,None]);return SimpleNamespace(hidden_states=tuple(x if i==0 else v for i in range(29)))
    class Model(nn.Module):
        def __init__(self):
            super().__init__();self.emb=nn.Embedding(32,8);self.model=Decoder(self.emb);self.lm_head=nn.Linear(8,32,bias=False)
        def get_input_embeddings(self):return self.emb
        def forward(self,input_ids=None,inputs_embeds=None,**kw):
            return SimpleNamespace(logits=self.lm_head(self.model(input_ids,inputs_embeds).hidden_states[-1]))
    return Model().eval().requires_grad_(False)

def fixture_features():
    ids=torch.tensor([[2,3,4,5]]);keys=[(0,1),(2,3),(4,5),(6,7)];idx=torch.arange(4).repeat(2,1).T.contiguous();p=(ids,keys,idx)
    for arm in ARMS:
        torch.manual_seed(SEED);model=fixture_backbone();net=Readers(8,arm,.1);delta=None
        if arm in ARMS[3:5]:
            m=model.model.layers[0].self_attn;delta=DeltaLinear(m.q_proj,arm==ARMS[4]);m.q_proj=delta
        f,g,_=feature(model,net,p,'cpu');(f.square().mean()+g.square().mean()).backward();freeze_check(model,delta)
        for name in ('S','R'):
            if hasattr(net,name):need(getattr(net,name).grad is not None and bool(torch.isfinite(getattr(net,name).grad).all()) and float(getattr(net,name).grad.norm())>0,'REGISTER_GRADIENT_'+name)
        if delta:need(any(float(v.grad.norm())>0 for v in delta.changes().values()),'MODEL_DELTA_GRADIENT')
        with torch.no_grad():need(len(decode(net,f,g,keys)['objects'])<=4,'FIXTURE_DECODE')

def fixture_guard():
    # Run the irreversible audit hook in a separate process; do not disable it in prediction.
    with tempfile.TemporaryDirectory() as d:
        p=Path(d)/'answers.SECRET.json';p.write_text('[]',encoding='utf-8');q=Path(d)/'prompts.json';q.write_text('[]',encoding='utf-8')
        code="from pathlib import Path; from tools.nanojev_gamer_v1_backbone_competence_0120 import guard; import sys; guard(); assert Path(sys.argv[2]).read_text()=='[]'\ntry: Path(sys.argv[1]).read_text()\nexcept PermissionError: pass\nelse: raise AssertionError('ANSWER_READ_ALLOWED')"
        subprocess.run([sys.executable,'-c',code,str(p),str(q)],check=True)

def fixture_lifecycle():
    # Exercise production train/selection/predict/score functions on a tiny causal model.
    # No PEFT or HF tokenizer is emulated as a claim of live compatibility.
    from types import SimpleNamespace
    import contextlib, io
    def fake_setup(a,ctx,arm):
        torch.manual_seed(SEED);model=fixture_backbone();torch.manual_seed(SEED+1);net=Readers(8,arm,.1);delta=None
        if arm in ARMS[3:5]:
            layer=model.model.layers[-1].self_attn;delta=DeltaLinear(layer.q_proj,arm==ARMS[4]);layer.q_proj=delta
        return model,None,net,delta
    def fake_prepare(tok,text,limit):
        # Word-coordinate mapping plus exact anchor spans, all input-derived.
        import re
        keys=[m.span() for m in re.finditer(r"[A-Za-z0-9]+",text)];ids=torch.tensor([[sum(map(ord,text[a:b]))%32 for a,b in keys]]);idx=torch.arange(len(keys)).repeat(2,1).T.contiguous();return ids,keys,idx
    def fake_logits(model,tok,text,net,device):
        ids=torch.tensor([[ord(x)%32 for x in text]]);S=net.reg('S') if net else None;return C.forward_first(model,ids,device,S)[0].logits[0,-1].float()
    def fake_native(model,tok,r,net,device):
        vs=[]
        for q in C.questions(r)[1]:
            z=fake_logits(model,tok,q,net,device);vs.append({'wide':{'margin':float((z[0]-z[1]).detach()),'answer_mass':float(z.softmax(-1)[:2].sum().detach())}})
        m=(vs[0]['wide']['margin']-vs[1]['wide']['margin'])/2;return {'choice':vs,'scores':[m,-m,-m,m]}
    names=('setup_arm','token_prepare','native_logits','native');old={k:globals()[k] for k in names};oldguard=C.guard
    try:
        globals().update(zip(names,(fake_setup,fake_prepare,fake_logits,fake_native)));C.guard=lambda:None # guard independently tested in a child
        with tempfile.TemporaryDirectory() as td,contextlib.redirect_stdout(io.StringIO()):
            out=Path(td);ctx=(out,out,{'fixture':True});a=SimpleNamespace(device='cpu',epochs=1,reader_lr=.001,register_lr=.001,weight_lr=.001,preservation_weight=1.,max_kl=.1,span_limit=16)
            r={'case_id':'fixture','base_id':'fixture','prompt':'actor action','prompt_sha256':ph('actor action')};g={'case_id':'fixture','prompt_sha256':r['prompt_sha256'],'graph':{'nodes':[{'id':'a','kind':'actor','span':[0,5]},{'id':'b','kind':'action','span':[6,12]}],'edges':[{'src':'a','dst':'b','type':'owns'}]}}
            b=list(new_binding())[:2];rr=[r for r,g in b];gg=[g for r,g in b]
            for name in SPLITS:
                rows,ans=([r],[g]) if name in GRAPH_SPLITS else (rr,gg);d=out/name;imm(d/'prompts.json',rows);imm(d/'answers.SECRET.json',ans);imm(d/'seal.json',{'identity':digest(ctx[2]),'prompts':sha(d/'prompts.json'),'answers':sha(d/'answers.SECRET.json'),'cases':len(rows)})
            model,tok,net,_=fake_setup(a,ctx,ARMS[0]);texts=[q for r in rr for q in C.questions(r)[1]]
            with torch.no_grad():
                teacher={f'{k}{i}':fake_logits(model,tok,x,None,'cpu') for k in ('fit','dev') for i,x in enumerate(texts)};p=[fake_native(model,tok,r,None,'cpu') for r in rr]
            save(out/'teacher.safetensors',teacher);imm(out/'bare-preservation.json',{'texts':texts,'devtexts':texts,'native':C.ranks(rr,gg,[x['scores'] for x in p]),'answers_mass':sum(x['wide']['answer_mass'] for b in p for x in b['choice'])/(2*len(p))})
            for arm in ARMS:arm_train(a,ctx,arm)
            sel=selection(a,ctx);need(ARMS[0] in sel['eligible'] and ARMS[-1] not in sel['eligible'],'FIXTURE_ELIGIBILITY')
            for arm in ARMS:predict(a,ctx,arm)
            score(a,ctx);v=read(out/'result.json');need(set(v['results'])==set(ARMS) and not v['certification'],'FIXTURE_REPORT')
            # Ensure completed training and prediction shards actually resume.
            arm_train(a,ctx,ARMS[0]);predict(a,ctx,ARMS[0]);score(a,ctx)
            file=out/'arms'/ARMS[0]/'predictions'/'fresh_graph'/'0000.json';file.write_text('{}',encoding='utf-8')
            try:score(a,ctx)
            except (RuntimeError,ValueError,KeyError):pass
            else:raise AssertionError('TAMPER_ACCEPTED')
    finally:globals().update(old);C.guard=oldguard

def selftest():
    torch.manual_seed(SEED);torch.set_num_threads(1);n=Readers(8,ARMS[2],.1);keys=[(0,1),(2,3),(4,5),(6,7)];idx=torch.tensor([[i,i] for i in range(4)]);h=[torch.randn(5,8) for _ in LAYERS];f,g=n.spans(h,idx)
    gold={'graph':{'nodes':[{'id':'x','span':[0,1],'kind':'actor'},{'id':'y','span':[2,3],'kind':'action'}],'edges':[{'src':'x','dst':'y','type':'owns'}]}};loss=train_graph(n,f,g,keys,gold);loss.backward();need(math.isfinite(float(loss.detach())) and n.objects.weight.grad is not None and n.binding[-1].weight.grad is not None,'READER_GRAD');need(n.reg('S').shape==(8,) and n.reg('R').shape==(8,),'REGISTERS')
    for lowrank in (False,True):
        base=nn.Linear(8,6);x=torch.randn(3,8);d=DeltaLinear(base,lowrank);need(torch.equal(d(x),base(x)),'ZERO_DELTA');d(x).sum().backward();need(all(v.grad is not None for v in d.changes().values()) and base.weight.grad is None,'DELTA_GRAD')
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'arm.safetensors';save(p,changes(n,d));restore(n,d,p);need(set(load_file(str(p)))==set(changes(n,d)),'SAVE_ROUNDTRIP')
    fixture_features();fixture_guard();fixture_lifecycle();b=list(new_binding());C.validate_rows([r for r,g in b],[g for r,g in b]);need(len(b)==32,'FRESH_BINDING_COUNT');need(float(kl(torch.ones(6),torch.ones(6)))==0,'KL_IDENTITY');m=metrics([gold],[{'objects':[(0,1,'actor'),(2,3,'action')],'edges':[[(0,1,'actor'),(2,3,'action'),'owns']]}]);need(m['strict']==1 and m['g2_f1']==1,'SCORING');emit('SELFTEST',{'reader_gradient':True,'zero_delta_and_frozen_base':True,'direct_and_low_rank_gradient':True,'checkpoint_roundtrip':True,'fresh_binding_contract':True,'graph_scoring':True,'kl_identity':True,'six_arm_decoder_gradient_fixture':True,'S_R_gradient_through_causal_decoder':True,'answer_guard_subprocess':True,'six_arm_train_select_predict_score_resume_tamper_fixture':True,'actual_qwen_cuda_peft':False})

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('command',choices=('audit','selftest','run','prepare','baseline','train','select','predict','score'),nargs='?',default='audit');p.add_argument('--root',default='../runtime/reports/gamer-v1');p.add_argument('--output');p.add_argument('--device',default='cuda');p.add_argument('--epochs',type=int,default=2);p.add_argument('--reader-lr',type=float,default=.0003);p.add_argument('--register-lr',type=float,default=.0003);p.add_argument('--weight-lr',type=float,default=.00001);p.add_argument('--preservation-weight',type=float,default=1.);p.add_argument('--max-kl',type=float,default=.1);p.add_argument('--fresh-bases',type=int,default=12);p.add_argument('--span-limit',type=int,default=16);p.add_argument('--arm',choices=ARMS);a=p.parse_args();torch.set_num_threads(min(4,torch.get_num_threads()));need(a.epochs>0 and a.fresh_bases>=4 and a.span_limit>=1 and min(a.reader_lr,a.register_lr,a.weight_lr)>0 and a.max_kl>=0 and a.preservation_weight>=0,'CONFIG')
    if a.command=='selftest':return selftest()
    ctx=context(a);root,out,ident=ctx
    if a.command=='audit':
        emit('AUDIT',{'ready':True,'identity':ident,'historical_adapter_only':ARMS[-1],'research_only':True,'model_loaded':False});return
    if a.command=='run':
        args=['--root',str(root),'--output',str(out),'--device',a.device,'--epochs',str(a.epochs),'--reader-lr',str(a.reader_lr),'--register-lr',str(a.register_lr),'--weight-lr',str(a.weight_lr),'--preservation-weight',str(a.preservation_weight),'--max-kl',str(a.max_kl),'--fresh-bases',str(a.fresh_bases),'--span-limit',str(a.span_limit)]
        def phase(cmd,arm=None):subprocess.run([sys.executable,'-u','-m',MODULE,cmd,*args,*(['--arm',arm] if arm else [])],check=True)
        phase('prepare');phase('baseline')
        for arm in ARMS:phase('train',arm)
        phase('select')
        for arm in ARMS:phase('predict',arm)
        phase('score');return
    if a.command=='prepare':return prepare(a,ctx)
    need(read(out/'identity.json')==ident,'RUN_IDENTITY_DRIFT')
    if a.command=='baseline':return baseline(a,ctx)
    if a.command=='train':need(a.arm is not None,'ARM_REQUIRED');return arm_train(a,ctx,a.arm)
    if a.command=='select':return selection(a,ctx)
    if a.command=='predict':need(a.arm is not None,'ARM_REQUIRED');return predict(a,ctx,a.arm)
    if a.command=='score':return score(a,ctx)
if __name__=='__main__':main()
