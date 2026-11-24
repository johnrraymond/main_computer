"""G1-0111: port of the historical S-first sparse reference register.

Run audit, selftest, then run --device cuda from nanojev/.
Requires the existing G1-0110 fuzz lab (and its G2-0109 dependency).
No change to canonical checkpoints, backbone, composite decoder, or stage gates.
Historical source: nanojev_code_sparse_register_k1000_s_first_train.py
and nanojev_code_sparse_register_k1000_s_first_r2_full_head_train.py.
The arithmetic-specific observation/readout is replaced with G1 span evidence.
"""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import random
from types import SimpleNamespace
import torch
from torch import nn
from torch.nn import functional as F
from tools import nanojev_gamer_v1_backbone_first as backbone
from tools import nanojev_gamer_v1_graph_curriculum as curriculum
from tools import nanojev_gamer_v1_graph_head as gh
from tools import nanojev_gamer_v1_g1_native_composite_0082 as native
from tools import nanojev_gamer_v1_g2_error_atlas_0093 as atlas
from tools import nanojev_gamer_v1_g2_hard_negative_learning_0087 as hard

TAG='G1_0111_S_FIRST'
SCHEMA='nanojev.gamer.g1.s-first-reference-0111.v1'
ROOT=atlas.DEFAULT_ROOT
TRAIN_SEED=1112026
TEST_SEED=1113026
TRAIN_MODES=('control','alias','pronoun','forward','caps','prefix')
TEST_MODES=TRAIN_MODES+('alias+forward','pronoun+forward')
ARMS=('original','zero_reference','static_S','dynamic_S_R1','negative_reference')
HISTORICAL=('nanojev_code_sparse_register_k1000_s_first_train.py',
            'nanojev_code_sparse_register_k1000_s_first_r2_full_head_train.py')


def require(ok,why):
    if not ok:raise ValueError(TAG+'_'+why)


def emit(event,data):
    print(TAG+'_'+event+' '+json.dumps(data,sort_keys=True,allow_nan=False),flush=True)


def digest_text(text):return hashlib.sha256(text.encode('utf-8')).hexdigest()


def write_new(path,obj):
    path=Path(path)
    with path.open('x',encoding='utf-8') as f:
        json.dump(obj,f,ensure_ascii=False,sort_keys=True,indent=2,allow_nan=False)
        f.write('\n')


def fuzz():
    # Deferred so the numerical transmission selftest can run independently.
    from tools import nanojev_gamer_v1_g1_semantic_fuzz_lab_0110 as lab
    return lab


def cohorts():
    lab=fuzz();groups={}
    for name,seed,modes in [('train',TRAIN_SEED,TRAIN_MODES),('test',TEST_SEED,TEST_MODES)]:
        rows=[]
        for i in range(8):
            base=lab._base(i,seed=seed);base['split']=name
            for mode in modes:
                row=lab._variant(base,mode)
                require(row['prompt_sha256']==digest_text(row['prompt']),'PROMPT_HASH_MISMATCH')
                rows.append(row)
        groups[name]=rows
    a,b=groups['train'],groups['test'];allrows=a+b
    require(len(a)==48 and len(b)==64,'COHORT_COUNT')
    require(len({r['prompt_sha256'] for r in allrows})==len(allrows),'DUPLICATE_PROMPTS')
    require({r['base_id'] for r in a}.isdisjoint({r['base_id'] for r in b}),'BASE_LEAKAGE')
    require({r['mode'] for r in b}-{r['mode'] for r in a}==set(TEST_MODES[-2:]),'UNSEEN_COMBINATIONS_MISSING')
    return groups


def source(root):
    paths=atlas._roots(root)
    signature=fuzz()._source_signature(root)
    directory=Path(__file__).resolve().parent
    lineage={name:backbone.sha256(directory/name) for name in HISTORICAL}
    # Reuse the audited parent manifest to obtain the actual installed model ID.
    record,_=hard._verify_trained(paths,paths['parent_0087'])
    return paths,record,signature,lineage


def check_prior_seal(root,groups):
    old=Path(root)/'g1-semantic-fuzz-0110'/'pilot'/'seal.jsonl'
    require(old.is_file(),'RUN_0110_PREPARE_FIRST')
    manifest=json.loads(old.with_name('manifest.json').read_text(encoding='utf-8'))
    require(backbone.sha256(old)==manifest['seal_sha256'],'0110_SEAL_DRIFT')
    prior=set()
    with old.open(encoding='utf-8') as f:
        for line in f:prior.add(json.loads(line)['prompt_sha256'])
    require(all(r['prompt_sha256'] not in prior for rows in groups.values() for r in rows),'0110_PROMPT_REUSE')
    return manifest['seal_sha256']


class Reference(nn.Module):
    """Historical normalized tanh S + R1 bank/router, with zero R1 output.

    The former arithmetic-specific summary is replaced by frozen prompt hidden
    states and two G1 span statistics. No family/target identifiers enter R1.
    """
    def __init__(self,dim,rms,bank_size=32,rank=32):
        super().__init__();k=min(bank_size,dim);rank=min(rank,dim)
        self.base=nn.Parameter(torch.randn(dim)*0.02)
        self.strength_logit=nn.Parameter(torch.tensor(-2.944439)) # sigmoid .05
        self.register_buffer('embedding_rms',torch.tensor(float(rms)))
        self.bank=nn.Parameter(torch.linalg.qr(torch.randn(dim,k),mode='reduced').Q.T.contiguous())
        self.router_down=nn.Linear(dim,rank,bias=False)
        self.router_score=nn.Linear(2,rank,bias=False)
        self.router_coeff=nn.Linear(rank,k,bias=False)
        nn.init.zeros_(self.router_coeff.weight)
    def static_parameters(self):return (self.base,self.strength_logit)
    def dynamic_parameters(self):return (self.bank,*self.router_down.parameters(),*self.router_score.parameters(),*self.router_coeff.parameters())
    def normalized(self,raw):
        direction=torch.tanh(raw.float())
        return direction/(direction.square().mean()+1e-8).sqrt()*self.embedding_rms*self.strength_logit.sigmoid()
    def static(self):return self.normalized(self.base)
    def correction(self,summary,stats):
        code=torch.tanh(self.router_down(summary.float())+self.router_score(stats.float()))
        coeff=torch.tanh(self.router_coeff(code))
        residual=coeff@F.normalize(self.bank.float(),dim=1)
        return self.normalized(self.base+residual),coeff
    def phase(self,name):
        require(name in ('static','dynamic'),'UNKNOWN_TRAINING_PHASE')
        self.requires_grad_(False)
        params=self.static_parameters() if name=='static' else self.dynamic_parameters()
        for p in params:p.requires_grad_(True)
        return params


def with_reference(model,prepared,ref,device):
    """Historical inputs_embeds prepend. Slice prefix state before G1.

    Original character offsets, eligible mask and span indices stay unchanged.
    Crucially this path does not call frozen_context (which detaches gradients).
    """
    ids=prepared['ids'].to(device);att=prepared['attention'].to(device)
    with torch.no_grad():emb=model.get_input_embeddings()(ids)
    require(ref.ndim==1 and ref.numel()==emb.shape[-1],'REFERENCE_DIMENSION')
    inp=torch.cat((ref.to(device=emb.device,dtype=emb.dtype).reshape(1,1,-1),emb),dim=1)
    mask=torch.cat((torch.ones_like(att[:,:1]),att),dim=1)
    maxpos=getattr(model.config,'max_position_embeddings',None)
    require(maxpos is None or inp.shape[1]<=maxpos,'POSITION_LIMIT')
    out=model(inputs_embeds=inp,attention_mask=mask,output_hidden_states=True,use_cache=False)
    hidden=out.hidden_states[-1][0,1:]
    require(hidden.shape[0]==ids.shape[1],'SPAN_COORDINATE_DRIFT')
    return hidden.float()


def dynamic_reference(model,head,prepared,controller,device):
    # Historical S-first: detached observation at the static operating point.
    # No gold spans, family, answer, or target data are available here.
    with torch.no_grad():
        hidden=with_reference(model,prepared,controller.static(),device)
        eligible=prepared['eligible'].to(device)
        raw=head(hidden,eligible=eligible,active_stage='G1')
        valid=raw['span_valid']
        scores=raw['span_logits'][valid].float()
        require(scores.numel()>0,'EMPTY_SENSOR')
        margin=scores[:,1:].amax(-1)-scores[:,0]
        stats=torch.stack((margin.mean(),(margin>0).float().mean()))
        summary=hidden[eligible].mean(0)
    return controller.correction(summary,stats)


def training_loss(model,head,prepared,row,controller,phase,device):
    labels=curriculum.aligned_labels(prepared,row)
    if phase=='static':
        ref=controller.static();penalty=ref.sum()*0
    else:
        ref,coeff=dynamic_reference(model,head,prepared,controller,device)
        penalty=0.001*coeff.abs().mean()
    h=with_reference(model,prepared,ref,device)
    raw=head(h,eligible=prepared['eligible'].to(device),active_stage='G1')
    return curriculum.stage_loss(head,raw,labels,row,'G1')+penalty


def frozen_ok(model,head):
    return all(not p.requires_grad and p.grad is None for m in (model,head) for p in m.parameters())


def summarize(rows):
    c=Counter()
    for r in rows:c.update(r)
    tp=c['tp'];fp=c['fp'];fn=c['fn'];n=c['cases']
    return dict(c,precision=tp/max(tp+fp,1),recall=tp/max(tp+fn,1),
                f1=2*tp/max(2*tp+fp+fn,1),exact_rate=c['exact']/max(n,1))


def count(row,objects):
    gold=Counter((n['span'][0],n['span'][1],n['kind']) for n in row['graph']['nodes'] if 'span' in n)
    pred=Counter(tuple(x) for x in objects)
    tp=sum((gold&pred).values());fp=sum((pred-gold).values());fn=sum((gold-pred).values())
    return {'cases':1,'tp':tp,'fp':fp,'fn':fn,'exact':int(not fp and not fn),'core_exact':int(not fn)}


def decoded(composite,hidden,prepared,family):
    out=composite.predict(hidden,prepared,family=family,native_stage='G1')
    objects=[]
    for s in out['token_spans']:
        bounds=gh.token_span_char_bounds(s,prepared)
        require(bounds is not None,'UNMAPPED_COMPOSITE_SPAN')
        objects.append([int(bounds[0]),int(bounds[1]),s['kind']])
    return objects


def evaluate(model,composite,tokenizer,controller,rows,device):
    allcases=[]
    with torch.no_grad():
        for i,row in enumerate(rows,1):
            prepared=backbone.prepare_prompt(tokenizer,row['prompt'])
            s=controller.static();r,_=dynamic_reference(model,composite.head,prepared,controller,device)
            refs={'zero_reference':torch.zeros_like(s),'static_S':s,'dynamic_S_R1':r,'negative_reference':-r}
            record={'case_id':row['case_id'],'base_id':row['base_id'],'family':row['family'],'mode':row['mode'],'arms':{}}
            for arm in ARMS:
                hidden=(backbone.frozen_context(model,prepared,device) if arm=='original' else
                        with_reference(model,prepared,refs[arm],device))
                objects=decoded(composite,hidden,prepared,row['family'])
                record['arms'][arm]={'counts':count(row,objects),'predicted':objects}
            allcases.append(record)
            if i==1 or i%8==0 or i==len(rows):emit('EVAL',{'done':i,'of':len(rows)})
    summary={}
    for arm in ARMS:
        records=[x['arms'][arm]['counts'] for x in allcases]
        by_mode={m:summarize([x['arms'][arm]['counts'] for x in allcases if x['mode']==m]) for m in TEST_MODES}
        by_family={f:summarize([x['arms'][arm]['counts'] for x in allcases if x['family']==f]) for f in ('allocation','encounter')}
        paired={}
        for base in ('original','zero_reference','static_S'):
            a=[x['arms'][base]['counts'] for x in allcases]
            paired[base]={'exact_gain':sum(not x['exact'] and y['exact'] for x,y in zip(a,records)),
                          'exact_harm':sum(x['exact'] and not y['exact'] for x,y in zip(a,records)),
                          'core_gain':sum(not x['core_exact'] and y['core_exact'] for x,y in zip(a,records)),
                          'core_harm':sum(x['core_exact'] and not y['core_exact'] for x,y in zip(a,records))}
        summary[arm]={'overall':summarize(records),'by_mode':by_mode,'by_family':by_family,'paired':paired,
                      'unseen_combinations':summarize([x['arms'][arm]['counts'] for x in allcases if '+' in x['mode']])}
    return summary,allcases


def audit(root=ROOT):
    groups=cohorts();paths,record,sig,lineage=source(root);seal=check_prior_seal(root,groups)
    emit('AUDIT',{'ready':True,'model_id':record['model_id'],'frozen_g1':sig,'historical_source_sha256':lineage,
                  'prior_0110_seal_sha256':seal,'train_cases':48,'test_cases':64,'train_bases':8,'test_bases':8,
                  'unseen_combinations':list(TEST_MODES[-2:]),'arms':ARMS,'backbone_frozen':True,
                  'head_frozen':True,'g3_unlocked':False,'production_promotion':False})


def run(root=ROOT,output=None,device='cuda',steps=96,lr=0.002):
    require(steps>=1 and lr>0,'INVALID_TRAINING_CONFIG')
    groups=cohorts();paths,record,sig,lineage=source(root);seal=check_prior_seal(root,groups)
    dest=Path(output) if output else Path(root)/'g1-s-first-reference-0111'
    require(not dest.exists(),'OUTPUT_EXISTS_USE_NEW_OUTPUT')
    dest.mkdir(parents=True)
    write_new(dest/'cohort-seal.json',{'schema':SCHEMA,'groups':groups,'frozen_g1':sig,'historical_source_sha256':lineage,
                                     'prior_0110_seal_sha256':seal,'steps_per_phase':steps,'lr':lr})
    torch.manual_seed(111);rng=random.Random(111)
    model,tokenizer=backbone.model_components(record['model_id'],paths['adapter'],device)
    composite=native.load_composite(paths['composite'],device=device)
    model.eval().requires_grad_(False);head=composite.head.eval().requires_grad_(False)
    require(frozen_ok(model,head),'FROZEN_PARAMETERS_NOT_CLEAN')
    emb=model.get_input_embeddings().weight
    rms=float(emb.detach().float().square().mean().sqrt().item())
    controller=Reference(emb.shape[-1],rms).to(device)
    # Preflight only training labels, before any optimization. Never exclude
    # troublesome held-out cases. Token alignment failures are surfaced explicitly.
    prepared=[];unavailable=[]
    for row in groups['train']:
        p=backbone.prepare_prompt(tokenizer,row['prompt'])
        try:curriculum.aligned_labels(p,row)
        except ValueError as e:
            if str(e)!='GAMER_GRAPH_GOLD_TOKEN_ALIGNMENT_FAILURE':raise
            unavailable.append(row['case_id']);continue
        prepared.append((row,p))
    require(len(prepared)>=40,'TOO_MANY_UNALIGNABLE_TRAIN_CASES')
    emit('START',{'train_cases':48,'train_alignable':len(prepared),'train_unalignable':unavailable,
                  'test_cases':64,'steps_per_phase':steps,'device':device,'new_trainable_parameters':sum(p.numel() for p in controller.parameters())})
    history=[]
    for phase in ('static','dynamic'):
        params=controller.phase(phase);opt=torch.optim.AdamW(params,lr=lr,weight_decay=0.0)
        order=list(range(len(prepared)));rng.shuffle(order)
        for step in range(1,steps+1):
            if (step-1)%len(order)==0:rng.shuffle(order)
            row,p=prepared[order[(step-1)%len(order)]]
            opt.zero_grad(set_to_none=True)
            loss=training_loss(model,head,p,row,controller,phase,device)
            require(bool(torch.isfinite(loss)),'NONFINITE_LOSS')
            loss.backward()
            grads=[x.grad for x in params if x.grad is not None]
            require(grads and all(bool(torch.isfinite(g).all()) for g in grads),'NONFINITE_OR_MISSING_REFERENCE_GRADIENT')
            norm=torch.nn.utils.clip_grad_norm_(params,1.0)
            require(bool(torch.isfinite(norm)),'NONFINITE_GRADIENT_NORM')
            opt.step()
            require(frozen_ok(model,head),'FROZEN_GRADIENT_VIOLATION')
            item={'phase':phase,'step':step,'loss':float(loss.detach()),'gradient_norm':float(norm)}
            history.append(item)
            if step==1 or step%12==0 or step==steps:emit('LEARN',item)
        for x in params:x.grad=None
    controller.eval().requires_grad_(False)
    from safetensors.torch import save_file
    path=dest/'research-reference.safetensors'
    save_file({k:v.detach().cpu().contiguous() for k,v in controller.state_dict().items()},str(path))
    summary,allcases=evaluate(model,composite,tokenizer,controller,groups['test'],device)
    require(fuzz()._source_signature(root)==sig and frozen_ok(model,head),'FROZEN_SOURCE_CHANGED')
    report={'schema':SCHEMA,'arms':summary,'case_level':allcases,'learning_history':history,
            'training_alignment_failures':unavailable,'cohort_seal_sha256':backbone.sha256(dest/'cohort-seal.json'),
            'reference_sha256':backbone.sha256(path),'frozen_g1_sha256':sig,'historical_source_sha256':lineage,
            'test_used_for_training':False,'test_used_for_selection':False,'research_only':True,
            'production_promotion':False,'g3_unlocked':False,
            'limitations':['8 independent held-out bases, 64 correlated variants','same synthetic corpus families','G1 raw-head loss trains references; evaluation uses frozen full composite','G1-0110 alias/pronoun explanatory text is not annotated']}
    write_new(dest/'result.json',report)
    emit('RESULT',{a:{'overall':s['overall'],'unseen_combinations':s['unseen_combinations'],'paired':s['paired']['original']} for a,s in summary.items()})
    emit('SAVED',{'report':str(dest/'result.json'),'sha256':backbone.sha256(dest/'result.json'),'production_promotion':False})


def selftest():
    # Actual G1 span loss and head; tiny causal numerical backbone stand-in.
    class Tiny(nn.Module):
        def __init__(self):
            super().__init__();self.emb=nn.Embedding(20,16);self.mix=nn.Linear(16,16)
            self.config=SimpleNamespace(max_position_embeddings=128)
        def get_input_embeddings(self):return self.emb
        def forward(self,*,inputs_embeds=None,input_ids=None,attention_mask=None,output_hidden_states=True,use_cache=False):
            x=self.emb(input_ids) if inputs_embeds is None else inputs_embeds
            n=torch.arange(1,x.shape[1]+1,device=x.device).view(1,-1,1)
            return SimpleNamespace(hidden_states=(self.mix(x+x.cumsum(1)/n).tanh(),))
    torch.manual_seed(42);torch.set_num_threads(1)
    model=Tiny().eval().requires_grad_(False)
    head=gh.GamerGraphHead(hidden_size=16,width=16,layers=1,max_span_tokens=4).eval().requires_grad_(False)
    controller=Reference(16,0.2,bank_size=8,rank=8)
    p={'ids':torch.tensor([[1,2,3,4,5,6]]),'attention':torch.ones(1,6,dtype=torch.long),
       'eligible':torch.tensor([False,True,True,True,True,False]),
       'offsets':[(0,0),(0,5),(6,8),(9,13),(14,16),(0,0)],'prompt_start':0,'prompt':'Alpha 95 cost 70'}
    row={'graph':{'nodes':[{'id':'a','kind':'action','span':[0,5]}, {'id':'q','kind':'quantity','span':[6,8]},
                            {'id':'b','kind':'outcome','span':[9,13]},{'id':'r','kind':'quantity','span':[14,16]}]}}
    s=controller.static();r,coeff=dynamic_reference(model,head,p,controller,'cpu')
    require(torch.equal(s,r) and int(torch.count_nonzero(coeff))==0,'R1_NOT_INITIAL_NOOP')
    for phase in ('static','dynamic'):
        params=controller.phase(phase)
        loss=training_loss(model,head,p,row,controller,phase,'cpu');loss.backward()
        require(any(x.grad is not None and bool(x.grad.abs().sum()>0) for x in params),'NO_NONZERO_REFERENCE_GRADIENT_'+phase)
        require(frozen_ok(model,head),'FROZEN_GRADIENT_VIOLATION')
        for x in params:x.grad=None
    require(with_reference(model,p,s,'cpu').shape==(6,16),'REFERENCE_SPAN_COORDINATE_DRIFT')
    require(count(row,[[0,5,'action'],[6,8,'quantity'],[9,13,'outcome'],[14,16,'quantity']])['exact']==1,'COUNT_FAILURE')
    emit('SELFTEST',{'real_g1_head_loss':True,'static_gradient':True,'dynamic_gradient':True,
                     'r1_initial_noop':True,'original_span_coordinates_preserved':True,
                     'frozen_model_and_head_no_gradients':True,'actual_qwen_cuda_test':False})


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('command',choices=('audit','selftest','run'),nargs='?',default='audit')
    p.add_argument('--root',default=ROOT);p.add_argument('--output');p.add_argument('--device',default='cuda')
    p.add_argument('--steps',type=int,default=96);p.add_argument('--lr',type=float,default=0.002)
    a=p.parse_args(argv)
    if a.command=='audit':audit(a.root)
    elif a.command=='selftest':selftest()
    else:run(a.root,a.output,a.device,a.steps,a.lr)

if __name__=='__main__':main()
