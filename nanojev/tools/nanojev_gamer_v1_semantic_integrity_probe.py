"""Frozen G1-0115/G2-0116 causal verification. No fitting or promotion.
Separate prepare/predict/score processes; cached cases and author-marked contrasts.
"""
from __future__ import annotations
import argparse, collections, copy, hashlib, json, os, subprocess, sys, tempfile
from pathlib import Path
from types import SimpleNamespace
import torch
from safetensors.torch import load_file
from tools import nanojev_gamer_v1_g2_multilayer_reconstruction_0116 as lab

G=lab.g1; f=lab.f; m=lab.m; T=lab.T; P=lab.P; B=lab.B
read,write,sha,digest=lab.read,lab.write,lab.sha,lab.digest
MODULE='tools.nanojev_gamer_v1_semantic_integrity_probe'
LEGACY_PREPARE_SOURCE='85b52931bbe8adbd12ebf842cca1c1784f02d2469127714c6fe67b14223d1bba'
PREVIOUS_GUARD_SOURCE='637b67b94fdafc8e18ab4ea9d3e42526e1cf56838cd803497f9847fe95f1c7ed'
PREVIOUS_PROVENANCE_SOURCE='cf3722990817ab526ef3b507b857f3c7bc57d4c536421f84e22e97318917e96d'
NULL_CANDIDATE='G1_0079_NULL_CHARACTER_CANDIDATE'
PROBE_DECODER_FAILURES={NULL_CANDIDATE,'G1_0079_PINNED_QUANTITY_MISSING_FROM_LATTICE','G1_0079_QUANTITY_GUARD_FAILED','G1_0082_INVALID_SELECTED_KEY','G1_0082_TOO_MANY_NATIVE_SPANS','G1_0082_NATIVE_OBJECT_BOUNDARY_DRIFT','G1_0082_NATIVE_G1_G2_CONSUMPTION_PARITY'}
TAG='SEMANTIC_INTEGRITY'; ROUTES=('0114_full','0115_selected')
G2_POLICIES=('reduced_integrated','new_integrated','original_only')
CONDITIONS=('zero_all','shuffle_tokens','embedding_only','S_replaced')
FIXED=('normal',*CONDITIONS,'no_geometry','no_caps','no_lexical','no_kind','no_structure')

def need(x,msg):
    if not x: raise ValueError(TAG+'_'+msg)
def emit(ev,x): print(TAG+'_'+ev+' '+json.dumps(x,sort_keys=True,allow_nan=False),flush=True)
def immutable(p,x):
    if p.exists():need(read(p)==x,'IMMUTABLE_DRIFT_'+str(p))
    else:write(p,x)

def author_cases():
    # Deliberately small independently written binding fragments, NOT full decision problems.
    # Same surface object positions within each pair. Explicit annotations; no parser-derived gold.
    rows=[]
    def make(base,mode,segments,edges):
        prompt='';nodes=[];ids={}
        for v in segments:
            if isinstance(v,str):prompt+=v;continue
            key,kind,text=v;need(key not in ids,'DUPLICATE_AUTHOR_ID');ids[key]=key
            nodes.append({'id':key,'kind':kind,'span':[len(prompt),len(prompt)+len(text)]});prompt+=text
        graph={'nodes':nodes,'edges':[{'src':a,'dst':b,'type':t} for a,b,t in edges]}
        r={'case_id':base+'-'+mode,'base_id':base,'mode':mode,'family':'binding_fragment','cohort':'independent_author','prompt':prompt,'prompt_sha256':P.digest_text(prompt),'graph':graph}
        need(len(lab.gold(r))==len(edges),'AUTHOR_EDGE_LOSS');rows.append(r)
    for flip in (False,True):
        mode='reverse' if flip else 'direct'
        # Swap the action names in a cross-reference list; declarations remain fixed.
        a,b=('Amber','Cedar') if not flip else ('Cedar','Amber')
        make('reference-pair',mode,[('actor','actor','operator'),' can select ',('a','action','Amber'),' or ',('b','action','Cedar'),'. Reconsider ',('x','action',a),' before ',('y','action',b),'.'], [('actor','a','owns'),('actor','b','owns'),('x','b' if flip else 'a','refers_to'),('y','a' if flip else 'b','refers_to')])
        # Identical positions/types; ownership is conveyed by linking words, not nearest position.
        make('ownership-pair',mode,[('u','actor','planner'),' and ',('v','actor','trader'),' share a menu: ',('a','action','Amber'),' is '+('latter' if flip else 'former')+"'s choice; ",('b','action','Cedar'),' is '+('former' if flip else 'latter')+"'s choice."], [('v' if flip else 'u','a','owns'),('u' if flip else 'v','b','owns')])
        # Alternative explicit action-reference and numeric association order.
        make('quantity-pair',mode,[('u','actor','agent'),' offers ',('a','action','Amber'),' and ',('b','action','Cedar'),'. ',('q','quantity','17'),' is the ',('e','outcome','benefit'),' for ',('x','action',a),'; ',('r','quantity','29'),' is the ',('c','outcome','benefit'),' for ',('y','action',b),'.'], [('u','a','owns'),('u','b','owns'),('x','b' if flip else 'a','refers_to'),('y','a' if flip else 'b','refers_to'),('q','x','applies_to'),('r','y','applies_to'),('q','e','measures'),('r','c','measures')])
        # Same literal endpoint geometry; semantic ownership changes through relational words.
        make('ownership-cross',mode,[('a','action','Amber'),' and ',('b','action','Cedar'),' are available. The ',('u','actor','operator'),' chooses the '+('latter' if flip else 'former')+', while the ',('v','actor','partner'),' chooses the '+('former' if flip else 'latter')+'.'], [('u','b' if flip else 'a','owns'),('v','a' if flip else 'b','owns')])
    # Last pair has intentionally ambiguous pronoun annotation under the historical ontology;
    # it is an exploratory challenge, reported separately, never a certification gate.
    for r in rows:
        if r['base_id'] in ('ownership-pair','ownership-cross'):r['cohort']='independent_author_implicit_ownership'
    return rows

def setup(a):
    root=Path(a.root).resolve();source=root/'g2-multilayer-reconstruction-0116';out=root/('semantic-integrity-0115-0116'+('-cache-only' if a.cache_only else '')+(f'-n{a.cached}' if a.cached!=12 else ''))
    s=read(source/'selection.json');cfg=s['identity']['config']
    # Reconstruct the historical configuration, NOT the probe device override.
    p=SimpleNamespace(root=str(root),device=cfg['device'],bases=cfg['bases'],epochs=cfg['epochs'],lr=cfg['lr'])
    ctx=historical_provenance(root,lambda:lab.parent(p)) if a.command=='predict' else lab.parent(p)
    sel,seal,public=lab.check_seal(source,ctx[-1]);
    ident={'probe_source':sha(__file__),'g2_selection':sha(source/'selection.json'),'g2_seal':sha(source/'fresh'/'seal.json'),'g1_selection':sha(root/'g1-integrated-reconstruction-0115'/'development'/'selection.json'),'g1_execution':sha(G.__file__),'g2_execution':sha(lab.__file__),'parent_identity':digest(ctx[-1]),'device':a.device,'cached_limit':a.cached,'novel':not a.cache_only,'seed':1170116}
    return root,source,out,ctx,sel,seal,public,ident

def verify_g1_receipt(root):
    d=root/'g1-integrated-reconstruction-0115'/'certification';s=read(d/'seal.json');public=read(d/'prompts.json');need(sha(d/'prompts.json')==s['prompts_sha256'],'0115_PROMPT_DRIFT');need(sha(d.parent/'development'/'selection.json')==s['selection_sha256'],'0115_POLICY_DRIFT');n=0
    for path in sorted((d/'predictions').glob('*.json')):
        if not path.stem.isdigit():continue
        i=int(path.stem);r=read(path);p=public[i];need(r['seal_sha256']==sha(d/'seal.json') and r['execution_implementation_sha256']==sha(G.__file__) and r['case_id']==p['case_id'] and r['prompt_sha256']==p['prompt_sha256'] and r['body_sha256']==digest(r['body']),'0115_SHARD_DRIFT');n+=1
    return {'verified_completed_shards':n,'seal_sha256':sha(d/'seal.json'),'prompts_sha256':s['prompts_sha256'],'answers_committed_sha256':s['answers_sha256']}

def prepare(a,x):
    root,source,out,ctx,sel,seal,public,ident=x;out.mkdir(exist_ok=True);lock=out/'seal.json'
    if lock.exists():check(x,answers=True);emit('SEAL_REUSED',{'cases':read(lock)['cases']});return
    # Snapshot available caches, independent base first. Do not require 0115/0116 completion.
    completed=[];seen=set();d=source/'fresh';sh=sha(d/'seal.json')
    for i,p in enumerate(public):
        if p['base_id'] in seen:continue
        r=lab.shard(d,i,p,sh)
        if r is None:continue
        completed.append((i,p,r));seen.add(p['base_id'])
        if len(completed)>=a.cached:break
    need(completed,'NO_COMPLETED_0116_CACHE')
    # Prepare may read gold; predict is a distinct subprocess denied access to answer files.
    need(sha(d/'answers.SECRET.json')==seal['answers'],'0116_ANSWER_DRIFT');answers=read(d/'answers.SECRET.json')
    rows=[];gold=[]
    for i,p,r in completed:
        g=answers[i];need(g['case_id']==p['case_id'] and g['prompt_sha256']==p['prompt_sha256'],'GOLD_ALIGNMENT')
        rows.append(dict(p,cache_index=i,cache_sha256=r['cache_sha256'],source_body_sha256=r['body_sha256']));gold.append(g)
    if not a.cache_only:
        known=lab.A._known_prompts(ctx[0][3],str(root));known.update(r['prompt_sha256'] for r in public)
        for pth in (root/'g1-integrated-reconstruction-0115'/'certification'/'prompts.json',):
            if pth.exists():known.update(r['prompt_sha256'] for r in read(pth))
        for pth in (root/'g1-semantic-fuzz-0110'/'pilot'/'seal.jsonl',root/'g1-semantic-fuzz-0110'/'expansion'/'seal.jsonl'):
            if pth.is_file():
                with pth.open(encoding='utf-8') as h:known.update(json.loads(line)['prompt_sha256'] for line in h if line.strip())
        for pth in (root/'g1-s-first-reference-0111'/'cohort-seal.json',root/'g1-backbone-transmission-0112'/'probe'/'cohort-seal.json',root/'g1-backbone-transmission-0112'/'run'/'cohort-seal.json',ctx[0][0].parent/'cohort-seal.json'):
            known.update(r['prompt_sha256'] for group in read(pth)['groups'].values() for r in group)
        for r in author_cases():
            need(r['prompt_sha256'] not in known,'AUTHOR_PROMPT_COLLISION');known.add(r['prompt_sha256']);g=r.pop('graph');rows.append(dict(r,cache_index=None));gold.append({'case_id':r['case_id'],'prompt_sha256':r['prompt_sha256'],'graph':g})
    need(len({r['prompt_sha256'] for r in rows})==len(rows),'DUPLICATE_PROMPTS')
    immutable(out/'prompts.json',rows);immutable(out/'answers.SECRET.json',gold)
    receipt=verify_g1_receipt(root);need(sha(root/'g1-integrated-reconstruction-0115'/'certification'/'answers.SECRET.json')==receipt['answers_committed_sha256'],'0115_ANSWER_DRIFT');immutable(lock,{'identity':ident,'prompts':sha(out/'prompts.json'),'answers':sha(out/'answers.SECRET.json'),'cases':len(rows),'cached_cases':len(completed),'g1_receipt':receipt});emit('SEALED',{'cases':len(rows),'cached':len(completed),'novel':len(rows)-len(completed),'g1_receipt':receipt})

def seal_identity_matches(sealed, current):
    # Only the exact known predecessor implementations may have sealed this population. No other
    # identity field may differ; old prediction artifacts are NOT accepted.
    return sealed==current or any(sealed==dict(current,probe_source=h) for h in (LEGACY_PREPARE_SOURCE,PREVIOUS_GUARD_SOURCE,PREVIOUS_PROVENANCE_SOURCE))

def check(x,answers=False):
    out=x[2];s=read(out/'seal.json');need(seal_identity_matches(s['identity'],x[-1]) and s['prompts']==sha(out/'prompts.json'),'PROBE_SEAL_DRIFT')
    if answers:need(s['answers']==sha(out/'answers.SECRET.json'),'PROBE_ANSWER_DRIFT')
    rows=read(out/'prompts.json');need(len(rows)==s['cases'],'CASE_COUNT')
    for r in rows:
        keys={'case_id','base_id','family','mode','cohort','prompt','prompt_sha256','cache_index'}
        if r['cache_index'] is not None:keys|={'cache_sha256','source_body_sha256'}
        need(set(r)==keys and r['prompt_sha256']==P.digest_text(r['prompt']),'PUBLIC_SCHEMA')
    return s,rows

def answer_guard():
    # Python audit guard for file opens, not OS-level sandbox. Includes all prior secret files.
    def hook(event,args):
        if event=='open' and args and isinstance(args[0],(str,bytes,os.PathLike)):
            s=os.fsdecode(args[0]).replace('\\','/').lower();name=s.rsplit('/',1)[-1]
            if '.secret.' in name or name.startswith('answers.'):
                raise PermissionError(TAG+'_ANSWER_OPEN_FORBIDDEN_'+name)
    sys.addaudithook(hook)

def historical_provenance(root, verify):
    """Keep the predictor's direct file-open guard active during historical verification.

    The actual 0095 source verifier checks SHA-256 of two 0093 answer files.
    Only those paths receive a digest-only child process, with no answer content
    returned. The existing verifier still compares each digest to its committed
    value. Every other request uses the original digest path and is guarded.
    The adapter is restored before loading readers or executing inference.
    This is an application-level guard, not an OS sandbox.
    """
    old=lab.moe.old
    original=old.digest
    directory=Path(root).resolve()/old.atlas.OUTPUT_NAME/'seal'
    allowed={directory/f'answers.{split}.SECRET.jsonl' for split in ('discovery','confirmation')}
    # Reject redirected directories and symlink/reparse aliases rather than hashing
    # an arbitrary target under an approved filename. No case-insensitive name match.
    helper=('import hashlib,sys\n'
            'from pathlib import Path\n'
            'p=Path(sys.argv[1])\n'
            'if p.resolve()!=p: raise SystemExit(3)\n'
            'h=hashlib.sha256()\n'
            'with p.open("rb") as f:\n'
            '    for chunk in iter(lambda:f.read(1048576),b""): h.update(chunk)\n'
            'print(h.hexdigest())\n')
    def digest_only(path):
        q=Path(os.path.abspath(os.fspath(path)))
        if q not in allowed:
            return original(path)
        need(q.resolve()==q and q.is_file(),'HISTORICAL_ANSWER_PATH_REDIRECTED')
        r=subprocess.run([sys.executable,'-I','-S','-c',helper,str(q)],check=True,capture_output=True,text=True)
        value=r.stdout.strip()
        need(len(value)==64 and all(c in '0123456789abcdef' for c in value),'HISTORICAL_HASH_PROTOCOL')
        return value
    old.digest=digest_only
    try:
        return verify()
    finally:
        old.digest=original

def transform(states,p,mode,seed):
    idx=torch.where(p['eligible'].cpu().flatten())[0];gen=torch.Generator().manual_seed(seed)
    # Preserve token eligibility, shape, and cross-layer correspondence. Derangement via cyclic shift.
    perm=idx.roll(int(torch.randint(1,len(idx),(1,),generator=gen))) if len(idx)>1 else idx
    result={}
    for k,v in states.items():
        is_embedding=k.endswith('_0')
        if mode=='zero_all' or mode=='embedding_only' and not is_embedding: result[k]=torch.zeros_like(v)
        elif mode=='S_replaced' and k.startswith('S_'):result[k]=states['original_'+k[2:]]
        elif mode=='shuffle_tokens':
            q=v.clone();q[idx]=v[perm];result[k]=q
        else:result[k]=v
    return result

def decoder_failure(exc):
    """Recognize the precise frozen decoder failure, never arbitrary TypeErrors.

    The 0079 selector builds a set of option.char_key values, which can include
    None. Its _quantity comprehension dereferences None before 0082 validates
    the selected keys. Keep the original decoder frozen, and report its failure.
    """
    if isinstance(exc,TypeError) and str(exc)=="'NoneType' object is not subscriptable":
        tb=exc.__traceback__
        target=P.native.quantity._quantity.__code__
        while tb is not None:
            fr=tb.tb_frame
            if fr.f_code is target and None in fr.f_locals.get('prediction',()):return NULL_CANDIDATE
            tb=tb.tb_next
    if isinstance(exc,ValueError) and str(exc) in PROBE_DECODER_FAILURES-{NULL_CANDIDATE}:return str(exc)
    return None

def capture_probe_reader(comp,reader,p,hs):
    try:return f.capture_one(comp,reader,({},p,None),hs)
    except (TypeError,ValueError) as exc:
        failure=decoder_failure(exc)
        if failure is None:raise
        # No rescue, no filtered retry of the composite, no gold. Its output is
        # unavailable. Preserve native evidence exactly as in the 0114 reader
        # capture contract. Native objects are NOT substituted as selected ones.
        with torch.inference_mode():
            h=reader.hidden(hs);raw=reader.head(h,eligible=p['eligible'].to(h.device),active_stage='G1')
            try:native=m.native_objects(raw,p);native_error=None
            except ValueError as err:
                if str(err)!='GAMER_GRAPH_TOO_MANY_PREDICTED_OBJECTS':raise
                native=[];native_error=str(err)
        return {'selected':[],'native':f.sorted_objects(f.objects(native)),
                'decode_error':failure,'native_decode_error':native_error}

def g1_extract(comp,readers,states,p,policy,device):
    ev={}
    for arm,net in readers.items():
        src,ls,_=m.ARMS[arm];hs=[states[f'{src}_{k}'].to(device,dtype=torch.float32) for k in ls]
        ev[arm]=capture_probe_reader(comp,net,p,hs)
    return lab.endpoint_views(p['prompt'],ev,policy,p), f.sorted_objects(G.make_table(ev,f.ARMS)),{k:v['decode_error'] for k,v in ev.items() if v['decode_error']}

def g2_extract(states,p,view,nets,sel,condition):
    feat=lab.features(states,view,p);obj=feat['objects'];spec=copy.deepcopy(sel);probs={}
    if condition in ('no_geometry','no_structure'):
        feat['loc'].zero_();feat['geom'].zero_()
    if condition in ('no_lexical','no_structure'):feat['lex'].zero_()
    # Zero the learned type-embedding output, not the type index or endpoint identities.
    handles=[]
    if condition in ('no_kind','no_structure'):
        handles=[net.kinds.register_forward_hook(lambda mod,inp,out:torch.zeros_like(out)) for net in nets.values()]
    try:
        with torch.inference_mode():
            for k,net in nets.items():probs[k]=net(feat).softmax(-1).cpu()
    finally:
        for h in handles:h.remove()
    if condition in ('no_caps','no_structure'):
        for k in spec['decoders']:spec['decoders'][k]['caps']=False
        for k in spec['policies']:spec['policies'][k]['caps']=False
    caps=spec['caps'];ind={k:lab.individual(obj,v,spec['decoders'][k],caps) for k,v in probs.items()}
    pred={k:lab.policy_predict(obj,probs,ind,spec['policies'][k],caps) for k in G2_POLICIES}
    union=set().union(*ind.values());votes=collections.Counter(e for es in ind.values() for e in es)
    return {'objects':view['objects'],'predictions':{k:lab.jsedges(v) for k,v in pred.items()},'union':lab.jsedges(union),'individual':{k:lab.jsedges(v) for k,v in ind.items()},'votes':[{'edge':lab.jsedges([e])[0],'votes':v} for e,v in sorted(votes.items())]}

def predict(a,x):
    root,source,out,ctx,sel,seal,public,ident=x;s,rows=check(x);d=source/'fresh';sh=sha(d/'seal.json');(out/'predictions').mkdir(exist_ok=True);(out/'novel-cache').mkdir(exist_ok=True)
    # No Qwen model in cache-only mode: use exact frozen composite lineage.
    if any(r['cache_index'] is None for r in rows):
        model,tok,comp,ctrl,S,sig,lin=T.load(str(root),{'probe':[{k:r[k] for k in ('case_id','base_id','family','mode','cohort','prompt','prompt_sha256')} for r in rows if r['cache_index'] is None]},a.device);need(sig==ctx[0][-1]['frozen_g1'],'BACKBONE_DRIFT')
    else:
        paths,rec,sig,ck,lin=T.lineage(str(root),{});need(sig==ctx[0][-1]['frozen_g1'],'BACKBONE_DRIFT');comp=T.prior.native.load_composite(paths['composite'],device=a.device);comp.head.eval().requires_grad_(False);model=tok=S=None
    folder=ctx[0][0];readers={}
    for arm in f.ARMS:
        _,ls,kind=m.ARMS[arm];net=m.Reader(comp.head,ls,kind).to(a.device);net.load_state_dict(load_file(str(folder/arm/'selected.safetensors')),strict=True);readers[arm]=net.eval().requires_grad_(False)
    nets=lab.readers_for(a,source,sel);need(all(set(sel['policies'][k]['arms'])<=set(nets) for k in G2_POLICIES),'POLICY_REQUIRES_UNSUPPORTED_LEGACY_EXPERT');lockhash=sha(out/'seal.json')
    for i,row in enumerate(rows):
        dest=out/'predictions'/f'{i:06d}.json'
        if dest.exists():
            previous=validate_record(dest,row,lockhash)
            if row['cache_index'] is None:need(previous['novel_cache_sha256']==sha(out/'novel-cache'/f'{i:06d}.safetensors'),'NOVEL_CACHE_DRIFT')
            continue
        idx=row['cache_index'];prior=None
        if idx is not None:
            rec=lab.shard(d,idx,public[idx],sh);need(rec is not None and rec['cache_sha256']==row['cache_sha256'] and rec['body_sha256']==row['source_body_sha256'],'SOURCE_CACHE_DRIFT');prior=rec['body'];p=m.unpack(prior['prepared']);states=load_file(str(d/'cache'/f'{idx:06d}.safetensors'))
        else:
            p=B.prepare_prompt(tok,row['prompt']);original=m.capture(model,p,None,a.device);reference=m.capture(model,p,S,a.device);states={f'original_{k}':v for k,v in original.items()};states.update({f'S_{k}':v for k,v in reference.items() if k!=0})
        need(p['prompt']==row['prompt'],'PREPARED_PROMPT_DRIFT');emit('CASE_START',{'done':i,'of':len(rows),'case':row['case_id']});normal,union,errors=g1_extract(comp,readers,states,p,ctx[1],a.device)
        if errors:emit('DECODER_FAILURE',{'case':row['case_id'],'condition':'normal','failures':errors})
        body={'g1':{'normal':{'views':normal,'union':union,'decode_errors':errors}},'fixed':{},'end_to_end':{},'replay':{}};memo={}
        def run_g2(mode,view):
            key=(mode,digest(view))
            if key not in memo:memo[key]=g2_extract(transform(states,p,mode,1170116+i),p,view,nets,sel,mode)
            return memo[key]
        if prior is None:
            path=out/'novel-cache'/f'{i:06d}.safetensors';m.save(path,states,{'case_id':row['case_id'],'prompt_sha256':row['prompt_sha256']});body['novel_cache_sha256']=sha(path);body['prepared']=m.pack(p)
        if prior is not None:
            body['replay']['g1_exact']=normal==prior['endpoint_views']
        # G1 ablations are evaluated independently, and then passed downstream unchanged.
        for mode in CONDITIONS:
            st=transform(states,p,mode,1170116+i);v,u,e=g1_extract(comp,readers,st,p,ctx[1],a.device);body['g1'][mode]={'views':v,'union':u,'decode_errors':e}
            if e:emit('DECODER_FAILURE',{'case':row['case_id'],'condition':mode,'failures':e})
        for route in ROUTES:
            body['fixed'][route]={};body['end_to_end'][route]={}
            for mode in FIXED:
                body['fixed'][route][mode]=run_g2(mode,normal[route])
            if prior is not None:
                body['replay'][route]={k:body['fixed'][route]['normal']['predictions'][k]==prior['views'][route]['predictions'][k] for k in G2_POLICIES}
            for mode in CONDITIONS:
                body['end_to_end'][route][mode]=run_g2(mode,body['g1'][mode]['views'][route])
        immutable(dest,{'case_id':row['case_id'],'prompt_sha256':row['prompt_sha256'],'seal':lockhash,'execution_source':ident['probe_source'],'body':body,'body_sha256':digest(body)});emit('PROGRESS',{'done':i+1,'of':len(rows),'case':row['case_id'],'replay':body['replay']})
    need(all(not q.requires_grad and q.grad is None for n in [comp.head,*readers.values(),*nets.values()] for q in n.parameters()),'TRAINING_DETECTED')
    need(setup(a)[-1]==ident,'PARENT_DRIFT');immutable(out/'prediction-lock.json',{'seal':lockhash,'shards':[sha(out/'predictions'/f'{i:06d}.json') for i in range(len(rows))],'execution_source':ident['probe_source'],'answer_guard':'python_open_audit_hook_v3_historical_checksum_child','training':False});emit('PREDICTIONS_LOCKED',{'cases':len(rows)})

def validate_record(path,row,lockhash):
    r=read(path);need(r['case_id']==row['case_id'] and r['prompt_sha256']==row['prompt_sha256'] and r['seal']==lockhash and r.get('execution_source')==sha(__file__) and r['body_sha256']==digest(r['body']),'PROBE_SHARD_DRIFT');return r['body']

def score(a,x):
    out=x[2];s,rows=check(x,answers=True);lock=read(out/'prediction-lock.json');lh=sha(out/'seal.json');need(lock.get('execution_source')==sha(__file__) and lock['seal']==lh and lock['shards']==[sha(out/'predictions'/f'{i:06d}.json') for i in range(len(rows))],'LOCK_DRIFT');answers=read(out/'answers.SECRET.json');bodies=[]
    for i,(r,g) in enumerate(zip(rows,answers)):
        need(r['case_id']==g['case_id'] and r['prompt_sha256']==g['prompt_sha256'],'ANSWER_ALIGNMENT');r['graph']=g['graph'];bodies.append(validate_record(out/'predictions'/f'{i:06d}.json',r,lh))
    need(len(answers)==len(rows),'ANSWER_COUNT')
    result={'schema':'semantic-integrity-0115-0116.v1','identity':x[-1],'sealed_identity':s['identity'],'seal':lh,'replay':[dict(case_id=r['case_id'],**b['replay']) for r,b in zip(rows,bodies) if b['replay']],'cohorts':{},'training':False,'production_promotion':False,'g3_unlocked':False}
    for cohort in sorted({r['cohort'] for r in rows}|{'cached_all','author_all'}):
        pairs=[(r,b) for r,b in zip(rows,bodies) if r['cohort']==cohort or cohort=='cached_all' and r['cache_index'] is not None or cohort=='author_all' and r['cache_index'] is None]
        if not pairs:continue
        rr,bb=zip(*pairs);report={'cases':len(rr),'bases':len({r['base_id'] for r in rr}),'g1':{},'g2':{},'contrast_cases':[]};result['cohorts'][cohort]=report
        for route in ROUTES:
            report['g1'][route]={};report['g2'][route]={}
            for mode in bb[0]['g1']:
                ps=[b['g1'][mode]['views'][route]['objects'] for b in bb];z=f.stats(rr,ps);z['inventory_gold']=sum(len(f.gold(r)&f.objects(b['g1'][mode]['union'])) for r,b in pairs);z['gold_objects']=sum(len(f.gold(r)) for r in rr);z['decode_errors']=sum(len(b['g1'][mode]['decode_errors']) for b in bb);report['g1'][route][mode]=z
            for phase in ('fixed','end_to_end'):
                report['g2'][route][phase]={}
                for mode in bb[0][phase][route]:
                    vv=[b[phase][route][mode] for b in bb];me={k:lab.measure(rr,[v['objects'] for v in vv],[lab.edge_set(v['predictions'][k]) for v in vv]) for k in G2_POLICIES};me['inventory_gold']=sum(len(lab.gold(r)&lab.edge_set(v['union'])) for r,v in zip(rr,vv));me['gold_edges']=sum(len(lab.gold(r)) for r in rr);vote=collections.defaultdict(collections.Counter)
                    for r,v in zip(rr,vv):
                        g=lab.gold(r)
                        for q in v['votes']:vote[q['votes']]['true' if next(iter(lab.edge_set([q['edge']]))) in g else 'false']+=1
                    me['agreement_new_readers']={str(k):{'tp':v['true'],'fp':v['false'],'precision':v['true']/max(1,v['true']+v['false'])} for k,v in sorted(vote.items())};report['g2'][route][phase][mode]=me
        for r,b in pairs:
            if r['cache_index'] is not None:continue
            v=b['fixed']['0115_selected']['normal'];pred=lab.edge_set(v['predictions']['new_integrated']);gold=lab.gold(r);report['contrast_cases'].append({'case_id':r['case_id'],'prompt':r['prompt'],'gold':lab.jsedges(gold),'predicted':lab.jsedges(pred),'missing':lab.jsedges(gold-pred),'extra':lab.jsedges(pred-gold),'exact':pred==gold})
    replay_ok=all(r['g1_exact'] and all(all(r[k].values()) for k in ROUTES) for r in result['replay']);result['cached_replay_exact']=replay_ok
    result['decoder_failure_events']=[{'case_id':r['case_id'],'cohort':r['cohort'],'condition':mode,'readers':z['decode_errors']} for r,b in zip(rows,bodies) for mode,z in b['g1'].items() if z['decode_errors']]
    result['normal_probe_decoder_failures']=[v for v in result['decoder_failure_events'] if v['condition']=='normal' and any(e in PROBE_DECODER_FAILURES for e in v['readers'].values())]
    result['decoder_failure_accounting']='A failed composite emits no selected objects; native evidence remains native only. Scores include the failures; do not interpret a decoder crash as pure loss of semantic observability.'
    result['author_pair_contrasts']=[]
    for base in sorted({r['base_id'] for r in rows if r['cache_index'] is None}):
        pair=[(r,b) for r,b in zip(rows,bodies) if r['base_id']==base];need(len(pair)==2,'PAIR_COUNT');(r1,b1),(r2,b2)=pair;g1,g2=lab.gold(r1),lab.gold(r2);report={'base':base,'cohort':r1['cohort'],'gold_changed_edges':len(g1^g2),'conditions':{}}
        for mode in FIXED:
            e1=lab.edge_set(b1['fixed']['0115_selected'][mode]['predictions']['new_integrated']);e2=lab.edge_set(b2['fixed']['0115_selected'][mode]['predictions']['new_integrated']);want={(1,e) for e in g1-g2}|{(2,e) for e in g2-g1};got={(1,e) for e in e1-e2}|{(2,e) for e in e2-e1};report['conditions'][mode]={'expected_directional_changes':len(want),'correct_directional_changes':len(want&got),'spurious_directional_changes':len(got-want),'both_graphs_exact':e1==g1 and e2==g2}
        result['author_pair_contrasts'].append(report)
    result['interpretation']='Frozen-model ablations are out-of-distribution sensitivity tests, not a quantitative decomposition of semantic knowledge. Author fragments are exploratory; implicit ownership may differ from historical annotation conventions. No automatic certification.'
    result['status']='BLOCKED_BASELINE_REPLAY_DRIFT' if not replay_ok else 'BLOCKED_NORMAL_DECODER_FAILURE' if result['normal_probe_decoder_failures'] else 'DIAGNOSTIC_REVIEW_REQUIRED'
    immutable(out/'result.json',result)
    for report in result['author_pair_contrasts']:emit('SEMANTIC_CONTRAST',{'base':report['base'],'cohort':report['cohort'],'normal':report['conditions']['normal'],'zero_all':report['conditions']['zero_all'],'shuffle_tokens':report['conditions']['shuffle_tokens']})
    for c,z in result['cohorts'].items():
        emit('COHORT',{'cohort':c,'cases':z['cases'],'bases':z['bases'],'g1_0115':{k:round(v['f1'],5) for k,v in z['g1']['0115_selected'].items()},'g2_fixed_0115':{k:{p:round(v[p]['f1'],5) for p in G2_POLICIES} for k,v in z['g2']['0115_selected']['fixed'].items()},'g2_end_to_end_0115':{k:round(v['new_integrated']['f1'],5) for k,v in z['g2']['0115_selected']['end_to_end'].items()}})
    emit('RESULT',{'path':str(out/'result.json'),'cached_replay_exact':replay_ok,'status':result['status'],'decoder_failure_events':len(result['decoder_failure_events']),'normal_probe_decoder_failures':len(result['normal_probe_decoder_failures']),'certification':False})

def oracle(a,x):
    """Optional post-lock gold-endpoint control; separate from all operational scores."""
    root,source,out,ctx,sel,seal,public,ident=x;s,rows=check(x,answers=True);lh=sha(out/'seal.json');lock=read(out/'prediction-lock.json');need(lock.get('execution_source')==sha(__file__) and lock['seal']==lh and lock['shards']==[sha(out/'predictions'/f'{i:06d}.json') for i in range(len(rows))],'LOCK_DRIFT');answers=read(out/'answers.SECRET.json');need(len(rows)==len(answers),'ANSWER_COUNT');nets=lab.readers_for(a,source,sel);d=source/'fresh';sh=sha(d/'seal.json');records=[];missing=[];data=collections.defaultdict(list)
    # Do not claim an oracle case was correct if exact author spans cannot align to tokens.
    max_span=int(lab.GH.DEFAULT_MAX_SPAN_TOKENS)
    for i,(row,g) in enumerate(zip(rows,answers)):
        need(row['case_id']==g['case_id'] and row['prompt_sha256']==g['prompt_sha256'],'ANSWER_ALIGNMENT');row=dict(row,graph=g['graph']);body=validate_record(out/'predictions'/f'{i:06d}.json',row,lh)
        if row['cache_index'] is None:
            path=out/'novel-cache'/f'{i:06d}.safetensors';need(sha(path)==body['novel_cache_sha256'],'NOVEL_CACHE_DRIFT');p=m.unpack(body['prepared'])
        else:
            idx=row['cache_index'];rec=lab.shard(d,idx,public[idx],sh);need(rec is not None and rec['cache_sha256']==row['cache_sha256'] and rec['body_sha256']==row['source_body_sha256'],'SOURCE_CACHE_DRIFT');path=d/'cache'/f'{idx:06d}.safetensors';p=m.unpack(rec['body']['prepared'])
        view,bad=lab.oracle_view(row,p,max_span)
        if view is None:missing.append({'case_id':row['case_id'],'objects':bad});continue
        states=load_file(str(path));z={}
        for mode in FIXED:z[mode]=g2_extract(transform(states,p,mode,1170116+i),p,view,nets,sel,mode)
        data[row['cohort']].append((row,z));records.append({'case_id':row['case_id'],'cohort':row['cohort'],'views':z});emit('ORACLE_PROGRESS',{'done':i+1,'of':len(rows)})
    report={'scope':'POST_LOCK_GOLD_ENDPOINTS_NOT_OPERATIONAL','prediction_lock_sha256':sha(out/'prediction-lock.json'),'cases':len(records),'unalignable':missing,'cohorts':{}}
    for c,pairs in data.items():
        rr,zz=zip(*pairs);report['cohorts'][c]={mode:{k:lab.measure(rr,[v[mode]['objects'] for v in zz],[lab.edge_set(v[mode]['predictions'][k]) for v in zz]) for k in G2_POLICIES} for mode in FIXED}
    immutable(out/'oracle-only.json',report);emit('ORACLE_RESULT',report)

def decoder_regression():
    # Actual 0079 _predict -> _quantity, actual 0082 predict, actual 0114
    # capture_one, actual neural head. Only the lattice/selector inputs are
    # controlled to deterministically reproduce the reported bad candidate.
    from unittest.mock import patch
    q=P.native.quantity
    p={'prompt':'Alpha 95','prompt_start':0,'offsets':[(0,5),(6,8)],'eligible':torch.ones(2,dtype=torch.bool)}
    head=lab.GH.GamerGraphHead(hidden_size=16,width=32,max_span_tokens=2).eval().requires_grad_(False)
    reader=m.Reader(head,[14],'frozen').eval().requires_grad_(False)
    comp=P.native.NativeCompositeG1(None,{}, {}, head,None,None,None,{})
    data=SimpleNamespace(added=SimpleNamespace(case=SimpleNamespace(options=[SimpleNamespace(char_key=None)])))
    hs=[torch.zeros(2,16)]
    with patch.object(P.native.NativeCompositeG1,'_lattice',return_value=data),patch.object(q.reliability,'_budget',return_value=data),patch.object(q,'_baseline',return_value=set()),patch.object(q,'_guarded_scores',return_value=torch.tensor([1.])),patch.object(q.structural,'_viterbi',return_value=[0]):
        try:f.capture_one(comp,reader,({},p,None),hs)
        except TypeError as exc:need(decoder_failure(exc)==NULL_CANDIDATE,'NULL_REPRO_WRONG_EXCEPTION')
        else:raise ValueError(TAG+'_NULL_REPRO_NOT_TRIGGERED')
        z=capture_probe_reader(comp,reader,p,hs)
        need(z['selected']==[] and z['decode_error']==NULL_CANDIDATE,'NULL_COMPOSITE_FAILURE_NOT_RECORDED')
        with torch.inference_mode():expected=f.sorted_objects(f.objects(m.native_objects(reader.head(reader.hidden(hs),eligible=p['eligible'],active_stage='G1'),p)))
        need(z['native']==expected,'NATIVE_EVIDENCE_CHANGED')
    # A legitimate empty decoder output is distinct from an exception.
    with patch.object(f,'capture_one',return_value={'selected':[],'native':[],'decode_error':None,'native_decode_error':None}):
        need(capture_probe_reader(comp,reader,p,hs)['decode_error'] is None,'EMPTY_IS_NOT_FAILURE')
    # An unrelated NoneType exception must still abort, rather than bias scores.
    with patch.object(f,'capture_one',side_effect=TypeError("'NoneType' object is not subscriptable")):
        try:capture_probe_reader(comp,reader,p,hs)
        except TypeError:pass
        else:raise ValueError(TAG+'_UNRELATED_TYPEERROR_SWALLOWED')
    # Empty endpoint inventories must be consumable by the actual G2 readers.
    nets={k:lab.Reader(16,k,width=32).eval().requires_grad_(False) for k in lab.ARMS}
    states={f'{src}_{k}':torch.zeros(2,16) for src in ('original','S') for k in m.LAYERS if src!='S' or k!=0}
    sel={'decoders':{k:{'threshold':.2,'margin':0.,'caps':True} for k in nets},'policies':{k:{'rule':'mean','arms':list(nets),'threshold':.2,'margin':0.,'caps':True} for k in G2_POLICIES},'caps':{}}
    view={'objects':[],'spans':[]};out=g2_extract(states,p,view,nets,sel,'zero_all')
    need(not out['union'] and all(not v for v in out['predictions'].values()),'EMPTY_G2_ROUTE')


def selftest():
    # Regression: permit only the known source-only change in an existing seal.
    ident={'probe_source':sha(__file__),'g2_selection':'fixed','seed':1170116}
    need(seal_identity_matches(dict(ident,probe_source=LEGACY_PREPARE_SOURCE),ident),'LEGACY_SEAL_REUSE')
    need(not seal_identity_matches(dict(ident,probe_source=LEGACY_PREPARE_SOURCE,seed=0),ident),'LEGACY_SEAL_DRIFT')
    need(seal_identity_matches(dict(ident,probe_source=PREVIOUS_GUARD_SOURCE),ident),'PREVIOUS_GUARD_SEAL_REUSE')
    need(not seal_identity_matches(dict(ident,probe_source='unrecognized'),ident),'UNKNOWN_SEAL_SOURCE')
    need(seal_identity_matches(dict(ident,probe_source=PREVIOUS_PROVENANCE_SOURCE),ident),'PREVIOUS_PROVENANCE_SEAL_REUSE')
    decoder_regression()
    rows=author_cases();need(len(rows)==8,'AUTHOR_COUNT');need(len({r['prompt_sha256'] for r in rows})==8,'AUTHOR_UNIQUENESS')
    for base in {r['base_id'] for r in rows}:
        a,b=[r for r in rows if r['base_id']==base];need(f.gold(a)==f.gold(b),'CONTRAST_GEOMETRY');need(lab.gold(a)!=lab.gold(b),'CONTRAST_GOLD_DELTA')
    x={'original_0':torch.arange(20).reshape(5,4).float(),'original_14':torch.arange(20).reshape(5,4).float()+100};p={'eligible':torch.tensor([False,True,True,True,False])};y=transform(x,p,'shuffle_tokens',1170116);need(torch.equal(y['original_14']-y['original_0'],torch.full((5,4),100.)) and not torch.equal(y['original_0'],x['original_0']),'SHUFFLE');need(torch.equal(y['original_0'][[0,4]],x['original_0'][[0,4]]),'INELIGIBLE_MUTATION');need(all(not v.any() for v in transform(x,p,'zero_all',1).values()),'ZERO');need(torch.equal(transform(x,p,'embedding_only',1)['original_0'],x['original_0']),'EMBEDDING')
    
    # Real 0116 reader forward/decoder code with synthetic hidden states; no fit.
    p={'prompt':'Alpha 95 Beta 70','prompt_start':0,'offsets':[(0,5),(6,8),(9,13),(14,16)],'eligible':torch.ones(4,dtype=torch.bool)}
    obj=[(0,5,'action'),(6,8,'quantity'),(9,13,'action'),(14,16,'quantity')];r={'graph':{'nodes':[{'id':str(i),'kind':k[2],'span':list(k[:2])} for i,k in enumerate(obj)],'edges':[{'src':'1','dst':'0','type':'applies_to'},{'src':'3','dst':'2','type':'applies_to'}]}};view,missing=lab.oracle_view(r,p,4);need(view is not None and not missing,'FIXTURE_MAPPING')
    states={f'{src}_{k}':torch.randn(4,16) for src in ('original','S') for k in m.LAYERS if src!='S' or k!=0};nets={k:lab.Reader(16,k,width=32).eval().requires_grad_(False) for k in lab.ARMS};dec={k:{'threshold':.2,'margin':0.,'caps':True} for k in nets};pol={k:{'rule':'mean','arms':list(nets),'threshold':.2,'margin':0.,'caps':True} for k in G2_POLICIES};sel={'decoders':dec,'policies':pol,'caps':lab.caps_from([r])}
    with torch.inference_mode():
        feat=lab.features(states,view,p);probs={k:n(feat).softmax(-1).cpu() for k,n in nets.items()}
    expected=lab.decode_case(obj,probs,sel);normal=g2_extract(states,p,view,nets,sel,'normal');need(all(lab.edge_set(normal['predictions'][k])==expected[k] for k in G2_POLICIES),'G2_BASELINE_DRIFT')
    for mode in FIXED:g2_extract(transform(states,p,mode,17),p,view,nets,sel,mode)
    need(g2_extract(states,p,view,nets,sel,'normal')==normal,'HOOK_LEAK');need(all(not q.requires_grad and q.grad is None for n in nets.values() for q in n.parameters()),'FROZEN')
    with tempfile.TemporaryDirectory() as tmp:
        paths=[Path(tmp)/name for name in ('answers.SECRET.json','holdout_answers.SECRET.jsonl','audit.answers.SECRET.jsonl')]
        for secret in paths:secret.write_text('{}',encoding='utf-8')
        # Exercise the REAL 0095 _source verifier, not a simulated digest call.
        # Synthetic artifacts carry correct historical digests and no actual answers.
        old=lab.moe.old; root=Path(tmp).resolve(); d=root/old.atlas.OUTPUT_NAME
        (d/'seal').mkdir(parents=True);(d/'discovery').mkdir()
        h=lambda v:hashlib.sha256(v).hexdigest()
        values={split:('fixture-'+split).encode() for split in ('discovery','confirmation')}
        historical=[d/'seal'/f'answers.{split}.SECRET.jsonl' for split in values]
        for path,value in zip(historical,values.values()):path.write_bytes(value)
        (d/'seal'/'prompts.jsonl').write_bytes(b'public')
        parent=root/'oracle-fabric-g2-mixed-family-fuzz-0092'/'B_from_0090'/'stage-4'/'checkpoint';parent.mkdir(parents=True);(parent/old.cur.HEAD_FILE).write_bytes(b'checkpoint')
        commit={'schema':old.atlas.SCHEMA,'state':'GOLD_COMMITTED_BEFORE_INFERENCE','prompt_sha256':h(b'public'),'answers_sha256':{k:h(v) for k,v in values.items()},'lineage':{'head_0092_B_sha256':h(b'checkpoint')}}
        (d/'seal'/'commit.json').write_text(json.dumps(commit),encoding='utf-8')
        lock={'g3_unlocked':False}
        for key,name in [('cases_sha256','cases.jsonl.gz'),('hypotheses_sha256','hypotheses.json'),('summary_sha256','summary.json'),('counterfactual_sha256','counterfactuals.jsonl')]:
            (d/'discovery'/name).write_bytes(name.encode());lock[key]=h(name.encode())
        (d/'discovery'/'lock.json').write_text(json.dumps(lock),encoding='utf-8')
        original_digest=old.digest
        # Reproduce the exact false positive even if secrets is already imported.
        import importlib.util
        stdlib=Path(importlib.util.find_spec('secrets').origin)
        answer_guard()
        need(bool(stdlib.read_bytes()),'STDLIB_SECRETS_BLOCKED')
        import secrets
        need(len(secrets.token_bytes(8))==8,'STDLIB_SECRETS_IMPORT')
        # The failure originated in a meta-device embedding initializer and lazy imports.
        with torch.device('meta'):
            e=torch.nn.Embedding(16,32)
        need(e.weight.is_meta,'META_EMBEDDING_INIT')
        try:old._source(root)
        except PermissionError as exc:need('answers.discovery.secret.jsonl' in str(exc),'WRONG_GUARD_FAILURE')
        else:raise ValueError(TAG+'_HISTORICAL_REPRO_FAILED')
        need(historical_provenance(root,lambda:old._source(root))[2]==commit,'HISTORICAL_VERIFIER_FAILED')
        need(old.digest is original_digest,'DIGEST_ADAPTER_NOT_RESTORED')
        # The same adapter must never allow current answers or direct historical reads.
        for secret in paths+historical:
            try:historical_provenance(root,lambda:secret.read_bytes())
            except PermissionError:pass
            else:raise ValueError(TAG+'_DIRECT_ANSWER_ACCESS')
        for secret in paths:
            try:historical_provenance(root,lambda:old.digest(secret))
            except PermissionError:pass
            else:raise ValueError(TAG+'_CURRENT_ANSWER_HASH_ALLOWED')
        # Alter the committed digest, without changing answer files: original check must fail.
        bad=copy.deepcopy(commit);bad['answers_sha256']['discovery']='0'*64
        (d/'seal'/'commit.json').write_text(json.dumps(bad),encoding='utf-8')
        try:historical_provenance(root,lambda:old._source(root))
        except ValueError as exc:need('0093_ANSWER_SEAL_DRIFT_discovery' in str(exc),'WRONG_DRIFT_FAILURE')
        else:raise ValueError(TAG+'_HISTORICAL_DRIFT_UNDETECTED')
        need(old.digest is original_digest,'DIGEST_ADAPTER_NOT_RESTORED_ON_FAILURE')
        for secret in paths+historical:
            try:secret.read_text(encoding='utf-8')
            except PermissionError:pass
            else:raise ValueError(TAG+'_ANSWER_GUARD_BYPASS')
    emit('SELFTEST',{'null_character_candidate_reproduced':True,'frozen_decoder_failure_recorded':True,'unrelated_typeerror_rejected':True,'empty_g2_route':True,'answer_open_guard':True,'stdlib_secrets_allowed':True,'meta_embedding_init':True,'historical_0095_source_guard_reproduced':True,'historical_0095_source_checksum_only':True,'historical_digest_drift_rejected':True,'direct_historical_and_current_answers_blocked':True,'exact_legacy_seal_reuse':True,'real_g2_reader_forward_and_decoder':True,'author_contrasts':4,'zero_shuffle_embedding':True,'runtime_checkpoints_tested':False,'cuda_tested':False})

def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('command',choices=('audit','selftest','prepare','predict','score','oracle','run'));ap.add_argument('--root',default=m.ROOT);ap.add_argument('--device',default='cuda');ap.add_argument('--cached',type=int,default=12);ap.add_argument('--cache-only',action='store_true');a=ap.parse_args();need(a.cached>0,'CACHE_COUNT');torch.set_num_threads(min(4,torch.get_num_threads()))
    if a.command=='selftest':selftest();return
    if a.command=='run':
        for phase in ('prepare','predict','score'):
            subprocess.run([sys.executable,'-u','-m',MODULE,phase,'--root',str(Path(a.root).resolve()),'--device',a.device,'--cached',str(a.cached)]+(['--cache-only'] if a.cache_only else []),check=True)
        return
    if a.command=='predict':answer_guard()
    x=setup(a)
    if a.command=='audit':emit('AUDIT',{'ready':True,'identity':x[-1],'g1_receipt':verify_g1_receipt(x[0]),'no_training':True});return
    {'prepare':prepare,'predict':predict,'score':score,'oracle':oracle}[a.command](a,x)
if __name__=='__main__':main()
