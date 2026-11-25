"""0120: paired bare/adapted Qwen competence; zero training, research only.
Conditional binding, not operational extraction. Run from nanojev directory.
"""
from __future__ import annotations
import argparse, collections, hashlib, inspect, json, math, os, re, subprocess, sys, tempfile, time
from pathlib import Path
from types import SimpleNamespace
import torch

TAG='G12_0120_COMPETENCE'; MODULE='tools.nanojev_gamer_v1_backbone_competence_0120'
RELS=('refers_to','owns','applies_to','measures')
SPLITS=('calibration','exposed0118','exposed0119','fresh')
CONDS=('bare','bare_S','adapted','adapted_S')
COUNTS=dict(zip(SPLITS,(16,32,48,16)))
AUTHOR=Path(__file__).with_name('nanojev_gamer_v1_competence_author_0120.py')
LEGACY_SOURCE='71d3e90b568d1267fa07c9944de213ebcb920b6814e2e9c101d836ab4a3d3b39'


def need(ok,msg):
    if not ok: raise RuntimeError(TAG+'_'+msg)
def sha(p):
    h=hashlib.sha256()
    with Path(p).open('rb') as f:
        for b in iter(lambda:f.read(1048576),b''):h.update(b)
    return h.hexdigest()
def digest(x):return hashlib.sha256(json.dumps(x,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
def read(p):return json.loads(Path(p).read_text(encoding='utf-8'))
def imm(p,x):
    p=Path(p);p.parent.mkdir(parents=True,exist_ok=True)
    if p.exists():need(read(p)==x,'IMMUTABLE_DRIFT_'+str(p));return
    fd,tmp=tempfile.mkstemp(dir=p.parent,suffix='.tmp')
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as f:json.dump(x,f,sort_keys=True,indent=2,allow_nan=False)
        os.replace(tmp,p)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)
def emit(k,x):print(TAG+'_'+k+' '+json.dumps(x,sort_keys=True,allow_nan=False),flush=True)
def guard():
    def hook(event,args):
        if event=='open' and args and isinstance(args[0],(str,bytes,os.PathLike)):
            name=os.fsdecode(args[0]).replace('\\','/').rsplit('/',1)[-1].lower()
            if '.secret.' in name or name.startswith('answers.'):raise PermissionError(TAG+'_ANSWER_FORBIDDEN_'+name)
    sys.addaudithook(hook)

def words(r):
    return {q['id']:r['prompt'][q['span'][0]:q['span'][1]] for q in r['anchors']}
def statement(rel,l,r):
    return {'refers_to':f'{l} denotes {r}','owns':f'{r} is an available action of {l}','applies_to':f'{l} is the cost of {r}','measures':f'{l} is the recorded value of {r}'}[rel]
def questions(r,blank=False):
    w=words(r);last=max(q['span'][1] for q in r['anchors']);text=r['prompt'][:last] if blank else r['prompt']
    stm=[statement(r['relation'],w[f'l{i}'],w[f'r{j}']) for i in range(2) for j in range(2)]
    pairs=[text+'\nDoes the record establish this binding? '+s+'. Reply Yes or No.' for s in stm]
    maps=[stm[0]+'; '+stm[3],stm[1]+'; '+stm[2]]
    options=[text+'\nWhich complete pairing is specified?\nA: '+maps[k]+'.\nB: '+maps[1-k]+'.\nReply only A or B.' for k in (0,1)]
    # No options or labels in ordinary generated-answer lane; symmetric candidate names only.
    gen=text+f'\nWhich right-hand entry is paired with each of {w["l0"]} and {w["l1"]}? Use the record, not the order of the inventory alone. Give the two bindings on separate lines, as left name -> right name. Do not give alternatives.'
    return pairs,options,gen

def validate_rows(rr,gg):
    need(len(rr)==len(gg) and len({r['prompt_sha256'] for r in rr})==len(rr),'ROW_COUNT_OR_DUPLICATE')
    groups=collections.defaultdict(dict)
    for r,g in zip(rr,gg):
        need((r['case_id'],r['prompt_sha256'])==(g['case_id'],g['prompt_sha256']),'ALIGNMENT')
        need(hashlib.sha256(r['prompt'].encode()).hexdigest()==r['prompt_sha256'],'PROMPT_HASH')
        need(set(words(r))=={'l0','l1','r0','r1'} and len(set(words(r).values()))==4,'ANCHORS')
        need(g['positive'] in ([1,0,0,1],[0,1,1,0]),'BINDINGS');groups[r['base_id']][r['mode']]=(r,g)
    for z in groups.values():
        for mode in ('direct','direct_paraphrase'):
            if mode not in z:continue
            r,g=z[mode];p,h=z[mode.replace('direct','reverse')]
            need(r['anchors']==p['anchors'] and words(r)==words(p) and g['positive']==[1-v for v in h['positive']],'MATCHED_REVERSAL')
            need(questions(r,True)==questions(p,True),'CUE_CONTROL')
        if 'direct_paraphrase' in z:need(z['direct'][1]['positive']==z['direct_paraphrase'][1]['positive'],'PARAPHRASE')

def resume_identity(out,ident):
    # Keep the original protocol seal byte-for-byte only for the audited predecessor.
    existing=[read(out/s/'seal.json')['identity'] for s in SPLITS if (out/s/'seal.json').exists()]
    if not existing:return ident
    sealed=existing[0]
    need(all(v==sealed for v in existing),'MIXED_SEALED_IDENTITIES')
    need(sealed==ident or sealed==dict(ident,source=LEGACY_SOURCE),'RESUME_IDENTITY_DRIFT')
    return sealed


def prior(a):
    # Use the actual 0119 lineage verifier; no historical head training or reader loading.
    from tools import nanojev_gamer_v1_semantic_addressability_0119 as A
    root=Path(a.root).resolve();parent_dir=root/'g1-g2-semantic-addressability-0119'
    cfg=read(parent_dir/'selection.json')['identity'];q=SimpleNamespace(root=str(root),device=cfg['device'],register_epochs=cfg['register_epochs'],command='predict' if a.command=='predict' else 'audit')
    st=A.setup(q);A.selection(st);parent,old,ident=st
    locks={}
    for src in ('exposed0118','fresh'):
        d,rr=A.cohort(st,src);lock=read(d/'prediction-lock.json')
        need(lock['seal']==sha(d/'seal.json') and lock['selection']==sha(old/'selection.json') and lock['features']==sha(d/'feature-lock.json') and lock['shards']==[sha(d/'predictions'/f'{i:04d}.json') for i in range(len(rr))],'PARENT_LOCK_'+src)
        locks[src]=sha(d/'prediction-lock.json')
    paths,rec,sig,ck,_=A.C.T.lineage(str(root),{})
    need(str(rec['model_id']),'MODEL_ID');adapter=Path(paths['adapter']).resolve()
    # This exact historical adapter and S checkpoint, never a newly fit version.
    ident={'source':sha(__file__),'author':sha(AUTHOR),'parent_source':sha(A.__file__),'parent_selection':sha(old/'selection.json'),'parent_locks':locks,'model_id':str(rec['model_id']),'adapter':str(adapter),'adapter_sha256':A.C.B.adapter_fingerprint(adapter),'S_checkpoint':str(ck.resolve()),'S_sha256':sha(ck),'device':a.device,'max_new_tokens':a.max_new_tokens,'thinking_tokens':a.thinking_tokens,'local_files_only':True,'scope':'conditional_binding_no_training','frozen_signature':sig}
    # Canonicalize tuples etc for JSON equality.
    out=root/'g1-g2-backbone-competence-0120'
    return A,st,out,resume_identity(out,json.loads(json.dumps(ident)))

def prepare(a,ctx):
    A,st,out,ident=ctx
    from tools.nanojev_gamer_v1_competence_author_0120 import rows
    new=list(rows());seen=set();prior_hashes={r['prompt_sha256'] for k in ('fit','validation','fresh','exposed0118') for r in A.cohort(st,k)[1]};need(not any(r['prompt_sha256'] in prior_hashes for r,g in new),'NEW_PROMPT_REUSE')
    for split in SPLITS:
        if split.startswith('exposed'):
            _,rr,gg=A.cohort(st,'exposed0118' if split=='exposed0118' else 'fresh',True)
            rr=[dict(r,split=split) for r in rr]
        else:rr=[r for r,g in new if r['split']==split];gg=[g for r,g in new if r['split']==split]
        validate_rows(rr,gg);need(len(rr)==COUNTS[split] and not seen.intersection(r['prompt_sha256'] for r in rr),'COHORT_COLLISION');seen.update(r['prompt_sha256'] for r in rr)
        d=out/split;imm(d/'prompts.json',rr);imm(d/'answers.SECRET.json',gg);imm(d/'seal.json',{'identity':ident,'prompts':sha(d/'prompts.json'),'answers':sha(d/'answers.SECRET.json'),'cases':len(rr),'split':split});emit('SEALED',{'split':split,'cases':len(rr)})

def cohort(ctx,split,answers=False):
    _,_,out,ident=ctx;d=out/split;s=read(d/'seal.json');need(s['identity']==ident and s['prompts']==sha(d/'prompts.json'),'SEAL_'+split);rr=read(d/'prompts.json');need(len(rr)==s['cases'] and all(hashlib.sha256(r['prompt'].encode()).hexdigest()==r['prompt_sha256'] for r in rr),'PROMPT_DRIFT')
    if not answers:return d,rr
    need(s['answers']==sha(d/'answers.SECRET.json'),'ANSWER_DRIFT');gg=read(d/'answers.SECRET.json');validate_rows(rr,gg);return d,rr,gg

def chat(tok,text,thinking=False):
    messages=[{'role':'user','content':text}]
    rendered=tok.apply_chat_template(messages,tokenize=False,add_generation_prompt=True,enable_thinking=thinking)
    ids=tok(rendered,add_special_tokens=False,return_tensors='pt')['input_ids'];need(ids.ndim==2 and ids.shape[0]==1,'CHAT_IDS');return ids

def label_ids(tok,labels,wide):
    out=[]
    for label in labels:
        ids=set()
        for prefix in (('', ' ', '\n') if wide else ('',)):
            v=tok.encode(prefix+label,add_special_tokens=False)
            if len(v)==1 and tok.decode(v,skip_special_tokens=False).strip()==label:ids.add(int(v[0]))
        need(bool(ids),'LABEL_MISSING_'+label);out.append(sorted(ids))
    need(not set(out[0]).intersection(out[1]),'LABEL_OVERLAP');return out

def logits_kw(model):
    base=model.get_base_model() if hasattr(model,'get_base_model') else model
    p=inspect.signature(base.forward).parameters
    return {'logits_to_keep':1} if 'logits_to_keep' in p else {'num_logits_to_keep':1} if 'num_logits_to_keep' in p else {}
def forward_first(model,ids,device,S=None,cache=False):
    ids=ids.to(device);kw={'use_cache':cache,**logits_kw(model)}
    if S is None:
        mask=torch.ones_like(ids);return model(input_ids=ids,attention_mask=mask,**kw),mask
    e=model.get_input_embeddings()(ids);x=torch.cat((S.to(e).reshape(1,1,-1),e),1);mask=torch.ones(x.shape[:2],dtype=torch.long,device=x.device);return model(inputs_embeds=x,attention_mask=mask,**kw),mask

def likelihood(model,tok,text,labels,device,S):
    q,_=forward_first(model,chat(tok,text),device,S);z=q.logits[0,-1].float();lp=z.log_softmax(-1);ans={}
    for key,wide in (('legacy',False),('wide',True)):
        ids=label_ids(tok,labels,wide);a,b=[torch.logsumexp(lp[v],0).item() for v in ids];margin=float(z[ids[0][0]]-z[ids[1][0]]) if not wide else a-b;ans[key]={'margin':margin,'answer_mass':math.exp(a)+math.exp(b),'ids':ids}
    return ans

def generate(model,tok,text,device,S,limit,thinking=False):
    # Standard deterministic autoregressive greedy continuation; S is prepended
    # once. Use the model's KV cache, not hidden labels or output constraints.
    q,mask=forward_first(model,chat(tok,text,thinking),device,S,True);result=[];eos=set()
    for v in (getattr(tok,'eos_token_id',None),getattr(model.generation_config,'eos_token_id',None)):
        if v is not None:eos.update(v if isinstance(v,(list,tuple)) else [v])
    im=tok.convert_tokens_to_ids('<|im_end|>');
    if im is not None and tok.decode([im],skip_special_tokens=False)=='<|im_end|>':eos.add(im)
    ended=False
    for i in range(limit):
        token=int(q.logits[0,-1].argmax());
        if token in eos:ended=True;break
        result.append(token)
        if i+1==limit:break
        need(q.past_key_values is not None,'GENERATION_CACHE_MISSING');mask=torch.cat((mask,torch.ones_like(mask[:,:1])),1)
        q=model(input_ids=torch.tensor([[token]],device=device,dtype=torch.long),attention_mask=mask,past_key_values=q.past_key_values,use_cache=True,**logits_kw(model))
    return {'text':tok.decode(result,skip_special_tokens=False),'token_count':len(result),'stopped':ended,'limit':limit,'thinking':thinking}

def parse_mapping(r,text,thinking=False):
    # Conservative, label-independent parsing. Never choose a mapping based on
    # which matches the answer. Unparsed/ambiguous output is separately counted.
    if thinking:
        if '</think>' not in text:return {'status':'unfinished_or_unmarked_thinking','pairs':[]}
        text=text.rsplit('</think>',1)[1]
    elif '</think>' in text:text=text.rsplit('</think>',1)[1]
    w=words(r);pairs=set();conflict=False;matched_lines=0;extra=[]
    for line in text.splitlines():
        line=re.sub(r'^\s*(?:[-*•]|\d+[.)])\s+','',line.strip()).replace('**','').replace('`','').strip()
        if not line or line.startswith('```'):continue
        found=False
        for i in range(2):
            for j in range(2):
                l=re.escape(w[f'l{i}']);v=re.escape(w[f'r{j}']);pat=rf'{l}\s*(?:->|→|:|=|—|–|is paired with|pairs with|matches)\s*{v}[.!]?'
                if re.fullmatch(pat,line,re.I):pairs.add(2*i+j);matched_lines+=1;found=True
        if not found:extra.append(line)
    if any(sum(k//2==i for k in pairs)>1 for i in range(2)) or any(sum(k%2==j for k in pairs)>1 for j in range(2)):conflict=True
    status='ambiguous' if conflict else ('complete_with_extra_text' if extra else 'complete') if len(pairs)==2 else 'partial' if pairs else 'unparsed'
    return {'status':status,'pairs':sorted(pairs),'matched_lines':matched_lines,'unparsed_lines':extra}

def frozen(model):need(all(not p.requires_grad and p.grad is None for p in model.parameters()),'FROZEN_VIOLATION')
def restore_frozen(model):
    # PEFT can re-enable adapter requires_grad flags on exiting disable_adapter().
    # Never erase an unexpected gradient: that would conceal a real violation.
    need(all(p.grad is None for p in model.parameters()),'UNEXPECTED_EXISTING_GRADIENT')
    trainable=[n for n,p in model.named_parameters() if p.requires_grad]
    model.eval().requires_grad_(False);frozen(model)
    return trainable


def evaluate(model,tok,r,device,S,a,thinking=False):
    frozen(model)
    body={}
    with torch.inference_mode():
        for blank in (False,True):
            pair,choice,gen=questions(r,blank);key='no_cue' if blank else 'normal'
            p=[likelihood(model,tok,x,('Yes','No'),device,S) for x in pair];c=[likelihood(model,tok,x,('A','B'),device,S) for x in choice]
            body[key]={'candidate':p,'choice':c}
            if not blank:
                g=generate(model,tok,gen,device,S,a.max_new_tokens);body[key]['generation']={**g,'parsed':parse_mapping(r,g['text'])}
                if thinking:
                    g=generate(model,tok,gen,device,S,a.thinking_tokens,True);body[key]['thinking_generation']={**g,'parsed':parse_mapping(r,g['text'],True)}
    frozen(model);return body

def load_bare(ctx,a):
    from transformers import AutoTokenizer,AutoModelForCausalLM
    from safetensors.torch import load_file
    A,_,_,ident=ctx
    need(not str(a.device).startswith('cuda') or torch.cuda.is_available(),'CUDA_UNAVAILABLE')
    dtype=torch.float16 if str(a.device).startswith('cuda') else torch.float32
    tok=AutoTokenizer.from_pretrained(ident['model_id'],trust_remote_code=False,use_fast=True,local_files_only=True)
    model=AutoModelForCausalLM.from_pretrained(ident['model_id'],trust_remote_code=False,dtype=dtype,local_files_only=True).to(a.device).eval().requires_grad_(False)
    need(not hasattr(model,'peft_config'),'NOT_BARE');frozen(model)
    weights=load_file(ident['S_checkpoint'],device='cpu');dim=model.get_input_embeddings().weight.shape[-1]
    ref=A.C.T.prior.Reference(dim,float(weights['embedding_rms']),bank_size=weights['bank'].shape[0],rank=weights['router_down.weight'].shape[0]).to(a.device)
    ref.load_state_dict(weights,strict=True);ref.eval().requires_grad_(False);S=ref.static().detach().float();need(S.norm()>0,'S_ZERO')
    # Same tokenizer, base object and precision for the entire paired intervention.
    receipt={'model_id':ident['model_id'],'commit':getattr(model.config,'_commit_hash',None),'model_type':model.config.model_type,'dtype':str(dtype),'hidden_size':dim,'tokenizer_sha256':hashlib.sha256(tok.backend_tokenizer.to_str().encode()).hexdigest(),'chat_template_sha256':digest(tok.chat_template),'adapter_sha256':ident['adapter_sha256'],'S_sha256':ident['S_sha256'],'S_vector_sha256':hashlib.sha256(S.cpu().numpy().tobytes()).hexdigest(),'torch':torch.__version__,'transformers':__import__('transformers').__version__,'peft':__import__('peft').__version__}
    return model,tok,S,receipt

def verify_shard(p,seal,r,cond,receipt,protocol_source=None):
    z=read(p);need(z['seal']==seal and z['prompt']==r['prompt_sha256'] and z['condition']==cond and z['model_receipt']==receipt and z['digest']==digest(z['body']),'SHARD_DRIFT_'+str(p))
    need(z.get('execution_source')==sha(__file__) or ('execution_source' not in z and protocol_source==LEGACY_SOURCE),'SHARD_EXECUTION_DRIFT_'+str(p))
    return z['body']

def capture_condition(a,ctx,split,cond,model,tok,S,receipt):
    d,rr=cohort(ctx,split);seal=sha(d/'seal.json');folder=d/'predictions'/cond;folder.mkdir(parents=True,exist_ok=True)
    for i,r in enumerate(rr):
        p=folder/f'{i:04d}.json'
        if p.exists():verify_shard(p,seal,r,cond,receipt,ctx[3].get('source'));continue
        started=time.monotonic();body=evaluate(model,tok,r,a.device,S if cond.endswith('_S') else None,a,thinking=(a.thinking_tokens>0 and split=='calibration' and not cond.endswith('_S')))
        imm(p,{'seal':seal,'prompt':r['prompt_sha256'],'condition':cond,'model_receipt':receipt,'body':body,'digest':digest(body),'execution_source':sha(__file__)})
        v=body['normal']['choice'];g=body['normal']['generation'];emit('PROGRESS',{'split':split,'condition':cond,'case':r['case_id'],'done':i+1,'of':len(rr),'seconds':round(time.monotonic()-started,2),'canonical_margin':round((v[0]['legacy']['margin']-v[1]['legacy']['margin'])/2,6),'generation_parse':g['parsed']['status'],'generation_tokens':g['token_count']})
    imm(folder/'lock.json',{'seal':seal,'model_receipt':receipt,'shards':[sha(folder/f'{i:04d}.json') for i in range(len(rr))]})

def predict(a,ctx,split):
    _,_,out,ident=ctx;d,rr=cohort(ctx,split)
    imm(out/'freeze-repair-execution.json',{'protocol_source':ident['source'],'execution_source':sha(__file__),'compatible_predecessor':LEGACY_SOURCE,'repair':'restore requires_grad flags after adapter-disable context; no scoring changes'})
    if (d/'prediction-lock.json').exists():
        lock=read(d/'prediction-lock.json');rh=sha(out/'model-receipt.json');need(lock=={'seal':sha(d/'seal.json'),'model_receipt':rh,'adapter_diagnostic':sha(d/'adapter-diagnostic.json'),'conditions':{c:sha(d/'predictions'/c/'lock.json') for c in CONDS}},'RESUME_LOCK');
        for c in CONDS:
            folder=d/'predictions'/c;need(read(folder/'lock.json')=={'seal':lock['seal'],'model_receipt':rh,'shards':[sha(folder/f'{i:04d}.json') for i in range(len(rr))]},'RESUME_CONDITION_LOCK')
            for i,r in enumerate(rr):verify_shard(folder/f'{i:04d}.json',lock['seal'],r,c,rh,ident.get('source'))
        emit('REUSED',{'split':split,'cases':len(rr)});return
    from peft import PeftModel
    model,tok,S,receipt=load_bare(ctx,a);imm(out/'model-receipt.json',receipt);rh=sha(out/'model-receipt.json');emit('MODEL',receipt)
    # Capture actual bare predictions before PEFT ever touches the model object.
    for cond in CONDS[:2]:capture_condition(a,ctx,split,cond,model,tok,S,rh)
    text=questions(rr[0])[1][0];ids=chat(tok,text)
    with torch.inference_mode():z=forward_first(model,ids,a.device)[0].logits[0,-1].float().cpu()
    model=PeftModel.from_pretrained(model,ident['adapter'],is_trainable=False).eval().requires_grad_(False);frozen(model)
    need(bool(model.peft_config) and not any(getattr(m,'merged',False) for m in model.modules()),'ADAPTER_NOT_ACTIVE_UNMERGED')
    # Reversible adapter-disable is only a cross-check, not the bare arm.
    with torch.inference_mode():
        za=forward_first(model,ids,a.device)[0].logits[0,-1].float().cpu()
        with model.disable_adapter():zd=forward_first(model,ids,a.device)[0].logits[0,-1].float().cpu()
    trainable=restore_frozen(model)
    with torch.inference_mode():zr=forward_first(model,ids,a.device)[0].logits[0,-1].float().cpu()
    need(torch.isfinite(zr).all() and torch.allclose(zr,za,rtol=1e-3,atol=1e-3),'ADAPTER_RESTORE_LOGIT_MISMATCH')
    frozen(model)
    emit('FREEZE_RESTORED',{'split':split,'reenabled_parameters':len(trainable),'examples':trainable[:4],'restored_logits_max_abs':float((zr-za).abs().max()),'all_frozen':True})
    need(torch.isfinite(z).all() and torch.isfinite(za).all() and torch.isfinite(zd).all(),'PARITY_NONFINITE')
    parity={'bare_vs_disabled_max_abs':float((z-zd).abs().max()),'bare_vs_disabled_rms':float((z-zd).square().mean().sqrt()),'bare_vs_adapted_rms':float((z-za).square().mean().sqrt()),'bare_vs_disabled_argmax_equal':int(z.argmax())==int(zd.argmax()),'active_adapters':list(model.active_adapters),'adapter_config':{k:str(v.peft_type) for k,v in model.peft_config.items()}}
    imm(d/'adapter-diagnostic.json',parity);emit('ADAPTER_CHECK',{'split':split,**parity})
    for cond in CONDS[2:]:capture_condition(a,ctx,split,cond,model,tok,S,rh)
    frozen(model);imm(d/'prediction-lock.json',{'seal':sha(d/'seal.json'),'model_receipt':rh,'adapter_diagnostic':sha(d/'adapter-diagnostic.json'),'conditions':{cond:sha(d/'predictions'/cond/'lock.json') for cond in CONDS}});emit('LOCKED',{'split':split,'cases':len(rr)})


def rank_scores(b,method,key='normal'):
    a=b[key];kind,variant=method.split('_',1)
    if kind=='candidate':return [p[variant]['margin'] for p in a['candidate']]
    v=[p[variant]['margin'] for p in a['choice']];m=(v[0]-v[1])/2
    return [m,-m,-m,m]

def ranks(rr,gg,zz):
    wins=ties=total=exact=0;by=collections.defaultdict(dict);detail=[];margins=[]
    for r,g,z in zip(rr,gg,zz):
        y=g['positive'];need(len(z)==4 and all(math.isfinite(v) for v in z),'SCORE_SHAPE')
        ds=[z[i]-z[j] for i in range(4) for j in range(4) if y[i] and not y[j] and (i//2==j//2 or i%2==j%2)]
        ok=all(v>1e-7 for v in ds);wins+=sum(v>1e-7 for v in ds);ties+=sum(abs(v)<=1e-7 for v in ds);total+=len(ds);exact+=ok;margins.extend(ds);by[r['base_id']][r['mode']]=ok;detail.append(ok)
    return {'cases':len(rr),'bases':len(by),'pairwise_accuracy':(wins+.5*ties)/max(1,total),'comparisons':total,'ties':ties,'both_bindings_correct':exact,'mean_margin':sum(margins)/max(1,len(margins)),**paired(by),'exact_flags':detail}

def paired(by):
    rev=[];para=[]
    for modes in by.values():
        for m in ('direct','direct_paraphrase'):
            if m in modes and m.replace('direct','reverse') in modes:rev.append(modes[m] and modes[m.replace('direct','reverse')])
        for m in ('direct','reverse'):
            if m in modes and m+'_paraphrase' in modes:para.append(modes[m] and modes[m+'_paraphrase'])
    return {'reversal_pairs':len(rev),'reversal_pairs_correct':sum(rev),'paraphrase_pairs':len(para),'paraphrase_pairs_correct':sum(para)}

def generation_metrics(rr,gg,bb,key='generation'):
    exact=[];by=collections.defaultdict(dict);status=collections.Counter();tp=fp=fn=stopped=0;parsed_ok=0;complete=0;exact_with_extra=0
    for r,g,b in zip(rr,gg,bb):
        x=b['normal'][key];p=x['parsed'];s=set(p['pairs']);y={i for i,v in enumerate(g['positive']) if v};ok=p['status']=='complete' and s==y
        exact.append(ok);by[r['base_id']][r['mode']]=ok;status[p['status']]+=1;stopped+=x['stopped'];complete+=p['status']=='complete';parsed_ok+=ok;exact_with_extra+=p['status'] in ('complete','complete_with_extra_text') and s==y;tp+=len(s&y);fp+=len(s-y);fn+=len(y-s)
    return {'cases':len(rr),'bases':len(by),'both_bindings_correct':sum(exact),'exact_rate':sum(exact)/max(1,len(rr)),'complete_parses':complete,'exact_including_extra_text':exact_with_extra,'exact_given_complete':parsed_ok/complete if complete else None,'parse_status':dict(status),'binding_precision':tp/max(1,tp+fp),'binding_recall':tp/max(1,tp+fn),'tp':tp,'fp':fp,'fn':fn,'stopped':stopped,'token_limit_reached':len(rr)-stopped,**paired(by),'exact_flags':exact}

def order_metrics(gg,bb,variant,key='normal'):
    dis=both=one=ties=correct0=correct1=0;mass=[];canonical=[]
    for g,b in zip(gg,bb):
        v=[x[variant]['margin'] for x in b[key]['choice']];a,c=v[0],-v[1];sign=1 if g['positive'][0] else -1
        ties+=abs(a)<=1e-7 or abs(c)<=1e-7;dis+=a*c<0;correct0+=sign*a>1e-7;correct1+=sign*c>1e-7;both+=min(sign*a,sign*c)>1e-7;one+=max(sign*a,sign*c)>1e-7;mass.extend(x[variant]['answer_mass'] for x in b[key]['choice']);canonical.append((a+c)/2)
    return {'cases':len(gg),'strict_order_disagreement':dis,'any_order_tie':ties,'both_orders_correct':both,'order0_correct':correct0,'order1_correct':correct1,'at_least_one_order_correct':one,'mean_answer_token_mass':sum(mass)/max(1,len(mass)),'canonical_margin_mean':sum(canonical)/max(1,len(canonical))}

def candidate_decisions(gg,bb,variant,key):
    pos=neg=ties=exact=0;mass=[]
    for g,b in zip(gg,bb):
        margins=[v[variant]['margin'] for v in b[key]['candidate']];mass.extend(v[variant]['answer_mass'] for v in b[key]['candidate']);ok=[]
        for y,v in zip(g['positive'],margins):
            good=(v>1e-7 if y else v< -1e-7);ok.append(good);pos+=bool(y and good);neg+=bool(not y and good);ties+=abs(v)<=1e-7
        exact+=all(ok)
    return {'cases':len(gg),'positive_correct':pos,'negative_correct':neg,'positives':2*len(gg),'negatives':2*len(gg),'ties':ties,'all_four_correct':exact,'mean_answer_token_mass':sum(mass)/max(1,len(mass))}

def metrics(rr,gg,bb):
    q={}
    for key in ('normal','no_cue'):
        for meth in ('choice_legacy','choice_wide','candidate_legacy','candidate_wide'):
            q[meth+('' if key=='normal' else '_no_cue')]=ranks(rr,gg,[rank_scores(b,meth,key) for b in bb])
        for variant in ('legacy','wide'):
            suffix='' if key=='normal' else '_no_cue';q['order_'+variant+suffix]=order_metrics(gg,bb,variant,key);q['candidate_decisions_'+variant+suffix]=candidate_decisions(gg,bb,variant,key)
    q['generation']=generation_metrics(rr,gg,bb)
    if bb and 'thinking_generation' in bb[0]['normal']:q['thinking_generation']=generation_metrics(rr,gg,bb,'thinking_generation')
    return q

def compact(x):return {k:v for k,v in x.items() if k!='exact_flags'}

def compare(q):
    result={}
    for a,b in (('bare','adapted'),('bare_S','adapted_S'),('bare','bare_S'),('adapted','adapted_S')):
        for meth in ('choice_legacy','choice_wide','candidate_legacy','candidate_wide','generation','thinking_generation'):
            if meth not in q[a] or meth not in q[b]:continue
            x=q[a][meth]['exact_flags'];y=q[b][meth]['exact_flags'];result[f'{a}_to_{b}:{meth}']={'first_only_correct':sum(i and not j for i,j in zip(x,y)),'second_only_correct':sum(j and not i for i,j in zip(x,y)),'both_correct':sum(i and j for i,j in zip(x,y)),'both_wrong':sum(not i and not j for i,j in zip(x,y))}
    return result

def score(ctx,split):
    A,st,out,_=ctx;d,rr=cohort(ctx,split);seal=sha(d/'seal.json');receipt=sha(out/'model-receipt.json');lock=read(d/'prediction-lock.json')
    need(lock=={'seal':seal,'model_receipt':receipt,'adapter_diagnostic':sha(d/'adapter-diagnostic.json'),'conditions':{cond:sha(d/'predictions'/cond/'lock.json') for cond in CONDS}},'PREDICTION_LOCK')
    bodies={}
    for cond in CONDS:
        folder=d/'predictions'/cond;need(read(folder/'lock.json')=={'seal':seal,'model_receipt':receipt,'shards':[sha(folder/f'{i:04d}.json') for i in range(len(rr))]},'CONDITION_LOCK');bodies[cond]=[verify_shard(folder/f'{i:04d}.json',seal,r,cond,receipt,ctx[3].get('source')) for i,r in enumerate(rr)]
    _,_,gg=cohort(ctx,split,True);outcomes={}
    for rel in (*RELS,'all'):
        ix=[i for i,r in enumerate(rr) if rel=='all' or r['relation']==rel];r=[rr[i] for i in ix];g=[gg[i] for i in ix];q={cond:metrics(r,g,[bodies[cond][i] for i in ix]) for cond in CONDS};outcomes[rel]={'metrics':{cond:{k:compact(v) for k,v in m.items()} for cond,m in q.items()},'paired_interventions':compare(q)}
        for cond,m in q.items():
            emit('COHORT',{'split':split,'relation':rel,'condition':cond,**{k:compact(m[k]) for k in ('choice_legacy','choice_wide','candidate_wide','generation','order_legacy','order_wide','choice_wide_no_cue')},**({'thinking_generation':compact(m['thinking_generation'])} if 'thinking_generation' in m else {})})
    replay=None
    if split.startswith('exposed') and A is not None:
        old,oldrr=A.cohort(st,'exposed0118' if split=='exposed0118' else 'fresh');need([r['prompt_sha256'] for r in rr]==[r['prompt_sha256'] for r in oldrr],'REPLAY_ALIGNMENT');deltas=[];signs=0
        for i,b in enumerate(bodies['adapted']):
            p=read(old/'predictions'/f'{i:04d}.json')['body']['scores']['native_choice'];q=rank_scores(b,'choice_legacy');deltas.extend(abs(x-y) for x,y in zip(p,q));signs+=((p[0]>0)==(q[0]>0))
        replay={'cases':len(rr),'same_margin_sign':signs,'max_abs_delta':max(deltas),'mean_abs_delta':sum(deltas)/len(deltas)};emit('0119_REPLAY',{'split':split,**replay})
    examples=[]
    for i,(r,g) in enumerate(zip(rr,gg)):
        examples.append({'case_id':r['case_id'],'prompt':r['prompt'],'positive':g['positive'],'anchors':words(r),'outputs':{c:{'generation':bodies[c][i]['normal']['generation'],'choice_order_margins':[x['legacy']['margin'] for x in bodies[c][i]['normal']['choice']],**({'thinking_generation':bodies[c][i]['normal']['thinking_generation']} if 'thinking_generation' in bodies[c][i]['normal'] else {})} for c in CONDS}})
    by_structure={}
    for structure in sorted({r['structure'] for r in rr}):
        ix=[i for i,r in enumerate(rr) if r['structure']==structure];q={c:metrics([rr[i] for i in ix],[gg[i] for i in ix],[bodies[c][i] for i in ix]) for c in CONDS};by_structure[str(structure)]={c:{k:compact(v) for k,v in m.items()} for c,m in q.items()}
    report={'split':split,'by_structure':by_structure,'prediction_lock':sha(d/'prediction-lock.json'),'adapter_diagnostic':read(d/'adapter-diagnostic.json'),'previous_0119_replay':replay,'outcomes':outcomes,'examples':examples,'certification':False,'training':False};imm(d/'result.json',report);emit('SCORED',{'split':split,'path':str(d/'result.json')});return report

def finish(ctx):
    _,_,out,ident=ctx;reports={s:read(out/s/'result.json') for s in SPLITS};need(all(r['prediction_lock']==sha(out/s/'prediction-lock.json') for s,r in reports.items()),'RESULT_LOCK');lines=['# 0120 Backbone competence and adapter interference','', 'Research only. Conditional two-by-two binding; no training, no graph certification.','', '| Cohort | Condition | Choice legacy | Choice wide | Candidate wide | Generated exact | Reversals (generated) | Order disagreement (legacy) |','|---|---|---:|---:|---:|---:|---:|---:|']
    for split,r in reports.items():
        for c,m in r['outcomes']['all']['metrics'].items():
            g=m['generation'];o=m['order_legacy'];lines.append(f"| {split} | {c} | {m['choice_legacy']['pairwise_accuracy']:.3f} | {m['choice_wide']['pairwise_accuracy']:.3f} | {m['candidate_wide']['pairwise_accuracy']:.3f} | {g['both_bindings_correct']}/{g['cases']} | {g['reversal_pairs_correct']}/{g['reversal_pairs']} | {o['strict_order_disagreement']}/{o['cases']} |")
    limitations=['Bare checkpoint is loaded before adapter attachment from the locally resolved model ID used by the historical loader; 0119 did not record the original base checkpoint revision. Same runtime base/tokenizer/dtype across paired arms.','A/B and Yes/No are next-token scores, not sequence likelihoods; wide variants combine recognized single-token spellings only. Reported answer-token mass exposes low coverage.','Generated answers are greedy, format-requested but unconstrained; parse failures and incomplete thinking are retained separately, not treated as proof of semantic incapacity. Thinking lane is calibration only and has a fixed token cap.','No-cue controls remove binding instruction and change distribution; two-by-two anchor/type metadata and one-to-one task framing are supplied.','Fresh cohorts are small authored constructions, not broad semantic certification. Exposed cohorts are retrospective.','Application-level answer-open guard is not an OS sandbox. No production or checkpoint mutation.']
    lines+=['','## Limitations',*['- '+x for x in limitations]];p=out/'RESEARCH_RECORD.md';content='\n'.join(lines)+'\n';
    if p.exists():need(p.read_text(encoding='utf-8')==content,'RESEARCH_RECORD_DRIFT')
    else:p.write_text(content,encoding='utf-8')
    imm(out/'result.json',{'identity':ident,'status':'DIAGNOSTIC_REVIEW_REQUIRED','training':False,'certification':False,'production_modified':False,'g3_unlocked':False,'cohorts':{s:{'report_sha256':sha(out/s/'result.json'),'all':r['outcomes']['all'],'adapter_diagnostic':r['adapter_diagnostic'],'previous_0119_replay':r['previous_0119_replay']} for s,r in reports.items()},'limitations':limitations});emit('RESULT',{'path':str(out/'result.json'),'status':'DIAGNOSTIC_REVIEW_REQUIRED','training':False})

def selftest():
    from tools.nanojev_gamer_v1_competence_author_0120 import rows
    allrows=list(rows());need(len(allrows)==32,'AUTHOR');rr=[r for r,g in allrows];gg=[g for r,g in allrows];validate_rows(rr,gg)
    # Fixture verifies the interfaces and actual cache loop, not Qwen accuracy.
    class Tok:
        eos_token_id=0
        def encode(self,s,**kw):return {'A':[1],'B':[2],'Yes':[3],'No':[4],' A':[5],' B':[6],' Yes':[7],' No':[8]}.get(s,[20,21])
        def decode(self,ids,**kw):return ''.join({0:'<eos>',1:'A',2:'B',3:'Yes',4:'No',5:' A',6:' B',7:' Yes',8:' No',9:'Topaz -> Pine\n',10:'Agate -> Elm',11:'<|im_end|>'}.get(i,'?') for i in ids)
        def convert_tokens_to_ids(self,s):return 11
        def apply_chat_template(self,messages,**kw):return messages[0]['content']
        def __call__(self,s,**kw):return {'input_ids':torch.tensor([[12,13,14]],dtype=torch.long)}
    class Model(torch.nn.Module):
        def __init__(self):super().__init__();self.emb=torch.nn.Embedding(32,8);self.generation_config=SimpleNamespace(eos_token_id=0)
        def get_input_embeddings(self):return self.emb
        def forward(self,attention_mask,use_cache,input_ids=None,inputs_embeds=None,past_key_values=None,logits_to_keep=1):
            x=self.emb(input_ids) if inputs_embeds is None else inputs_embeds;n=x.shape[1] if past_key_values is None else past_key_values+x.shape[1]
            need(attention_mask.shape[1]==n,'FIXTURE_CACHE_POSITION');step=0 if past_key_values is None else (past_key_values-(4 if self.with_s else 3)+1);z=torch.zeros(1,1,32);z[0,0,9 if step==0 else 10 if step==1 else 0]=10.;z[0,0,1]=1;z[0,0,5]=2;return SimpleNamespace(logits=z,past_key_values=n if use_cache else None)
        with_s=False
    tok=Tok();model=Model().eval().requires_grad_(False);r,g=allrows[0];r2,g2=allrows[1];need(words(r)['l0']=='Topaz','FIXTURE_ANCHOR')
    p=likelihood(model,tok,questions(r)[1][0],('A','B'),'cpu',None);need(p['wide']['ids']==[[1,5],[2,6]] and p['wide']['margin']>p['legacy']['margin'] and p['wide']['answer_mass']<1.,'LABEL_VARIANTS')
    gen=generate(model,tok,questions(r)[2],'cpu',None,8);need(gen['stopped'] and parse_mapping(r,gen['text'])['pairs']==[0,3],'GREEDY_BARE');model.with_s=True;gen=generate(model,tok,questions(r)[2],'cpu',torch.ones(8),8);need(gen['stopped'] and parse_mapping(r,gen['text'])['pairs']==[0,3],'GREEDY_S');model.with_s=False
    need(parse_mapping(r,'Topaz -> Pine\nTopaz -> Elm')['status']=='ambiguous','CONFLICT');need(parse_mapping(r,'Topaz -> Pine')['status']=='partial','PARTIAL');need(parse_mapping(r,'thinking...')['status']=='unparsed','UNPARSED');need(parse_mapping(r,'<think>foo</think>\nTopaz -> Pine\nAgate -> Elm',True)['status']=='complete','THINKING')
    need(ranks([r,r2],[g,g2],[[1,-1,-1,1],[-1,1,1,-1]])['reversal_pairs_correct']==1,'RANK_REVERSAL');need(ranks([r,r2],[g,g2],[[1,-1,-1,1]]*2)['pairwise_accuracy']==.5,'POSITION_SHORTCUT')
    # Simulate PEFT's context-exit behavior, without needing an installed adapter.
    import contextlib
    @contextlib.contextmanager
    def toggle_adapter():
        try:yield
        finally:model.emb.weight.requires_grad_(True)
    with toggle_adapter():frozen(model)
    need(model.emb.weight.requires_grad,'FIXTURE_REENABLE')
    need(restore_frozen(model)==['emb.weight'],'FIXTURE_RESTORE');frozen(model)
    model.emb.weight.grad=torch.ones_like(model.emb.weight)
    try:restore_frozen(model)
    except RuntimeError as e:need('UNEXPECTED_EXISTING_GRADIENT' in str(e),'WRONG_GRAD_FAILURE')
    else:raise AssertionError('GRADIENT_WAS_CONCEALED')
    need(model.emb.weight.grad is not None,'GRADIENT_ERASED');model.emb.weight.grad=None;frozen(model)
    with tempfile.TemporaryDirectory() as t:
        p=Path(t);current={'source':sha(__file__),'device':'cpu','fixture':True};legacy=dict(current,source=LEGACY_SOURCE)
        need(resume_identity(p,current)==current,'NEW_IDENTITY')
        imm(p/'calibration'/'seal.json',{'identity':legacy});need(resume_identity(p,current)==legacy,'LEGACY_SEAL_REUSE')
        try:resume_identity(p,dict(current,device='cuda'))
        except RuntimeError as e:need('RESUME_IDENTITY_DRIFT' in str(e),'WRONG_IDENTITY_FAILURE')
        else:raise AssertionError('IDENTITY_DRIFT_ACCEPTED')
        old=p/'old.json';b={'fixture':True};imm(old,{'seal':'seal','prompt':r['prompt_sha256'],'condition':'bare','model_receipt':'receipt','body':b,'digest':digest(b)})
        need(verify_shard(old,'seal',r,'bare','receipt',LEGACY_SOURCE)==b,'LEGACY_SHARD_REUSE')
        try:verify_shard(old,'seal',r,'bare','receipt',current['source'])
        except RuntimeError as e:need('SHARD_EXECUTION_DRIFT' in str(e),'WRONG_SHARD_FAILURE')
        else:raise AssertionError('UNBOUND_LEGACY_SHARD_ACCEPTED')
    a=SimpleNamespace(device='cpu',max_new_tokens=8,thinking_tokens=0)
    import contextlib,io
    with tempfile.TemporaryDirectory() as tmp,contextlib.redirect_stdout(io.StringIO()):
        out=Path(tmp);ctx=(None,None,out,{'fixture':True});d=out/'calibration';data=allrows[:16];pub=[q for q,g in data];ans=[g for q,g in data];imm(d/'prompts.json',pub);imm(d/'answers.SECRET.json',ans);imm(d/'seal.json',{'identity':ctx[3],'prompts':sha(d/'prompts.json'),'answers':sha(d/'answers.SECRET.json'),'cases':len(pub),'split':'calibration'});imm(out/'model-receipt.json',{'fixture':True});rh=sha(out/'model-receipt.json');imm(d/'adapter-diagnostic.json',{'fixture':True});
        for cond in CONDS:
            model.with_s=cond.endswith('_S');capture_condition(a,ctx,'calibration',cond,model,tok,torch.ones(8),rh)
        imm(d/'prediction-lock.json',{'seal':sha(d/'seal.json'),'model_receipt':rh,'adapter_diagnostic':sha(d/'adapter-diagnostic.json'),'conditions':{c:sha(d/'predictions'/c/'lock.json') for c in CONDS}});score(ctx,'calibration');first=sha(d/'result.json');score(ctx,'calibration');need(first==sha(d/'result.json'),'RESUME_SCORE')
        capture_condition(a,ctx,'calibration','bare',model,tok,torch.ones(8),rh);need(first==sha(d/'result.json'),'RESUME_CAPTURE')
        # Lock mutation must fail before opening any answers.
        p=d/'predictions'/'bare'/'0000.json';original=p.read_text();p.write_text(original+' ')
        try:score(ctx,'calibration')
        except RuntimeError as e:need('CONDITION_LOCK' in str(e),'WRONG_TAMPER_FAILURE')
        else:raise AssertionError('TAMPER_NOT_DETECTED')
    code=f'from {MODULE} import guard; guard();\ntry: open("answers.SECRET.json","rb")\nexcept PermissionError: print("blocked")\nelse: raise SystemExit(1)'
    t=subprocess.run([sys.executable,'-c',code],capture_output=True,text=True,check=True);need(t.stdout.strip()=='blocked','ANSWER_GUARD')
    emit('SELFTEST',{'authored_cases':len(allrows),'matched_pairs':16,'legacy_and_wide_likelihood_fixture':True,'greedy_kv_cache_bare_and_S_fixture':True,'strict_mapping_parser':True,'reversal_scoring':True,'full_four_condition_cache_score_fixture':True,'resume_and_tamper_detection':True,'answer_guard':True,'actual_qwen_checkpoint_tested':False,'actual_peft_attachment_tested':False,'cuda_tested':False,'adapter_context_reenable_fixture':True,'unexpected_gradient_refused':True,'legacy_seal_and_shard_reuse':True,'execution_provenance':True})

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('command',choices=('audit','selftest','prepare','predict','score','finish','run','calibrate'));p.add_argument('--root',default='../runtime/reports/gamer-v1');p.add_argument('--device',default='cuda');p.add_argument('--split',choices=SPLITS,default='calibration');p.add_argument('--max-new-tokens',type=int,default=64);p.add_argument('--thinking-tokens',type=int,default=256);a=p.parse_args();need(8<=a.max_new_tokens<=512 and 0<=a.thinking_tokens<=2048,'TOKEN_BUDGET');torch.set_num_threads(min(4,torch.get_num_threads()))
    if a.command=='selftest':selftest();return
    if a.command in ('run','calibrate'):
        args=['--root',str(Path(a.root).resolve()),'--device',a.device,'--max-new-tokens',str(a.max_new_tokens),'--thinking-tokens',str(a.thinking_tokens)]
        def phase(cmd,split=None):subprocess.run([sys.executable,'-u','-m',MODULE,cmd,*args,*(['--split',split] if split else [])],check=True)
        phase('prepare')
        for s in SPLITS if a.command=='run' else ('calibration',):phase('predict',s);phase('score',s)
        if a.command=='run':phase('finish')
        return
    if a.command=='predict':guard()
    ctx=prior(a)
    if a.command=='audit':emit('AUDIT',{'ready':True,'identity':ctx[3],'counts':COUNTS,'conditions':CONDS,'training':False,'model_load_tested':False,'execution_source':sha(__file__),'legacy_seal_reused':ctx[3]['source']==LEGACY_SOURCE});return
    if a.command=='prepare':prepare(a,ctx)
    elif a.command=='predict':predict(a,ctx,a.split)
    elif a.command=='score':score(ctx,a.split)
    else:finish(ctx)
if __name__=='__main__':main()
