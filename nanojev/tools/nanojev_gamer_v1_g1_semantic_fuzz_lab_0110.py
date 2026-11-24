"""G2-0110: frozen-G1 semantic fuzzing laboratory, NOT training/certification.

From C:\\Users\\...\\main_computer\\nanojev:
    python -m tools.nanojev_gamer_v1_g1_semantic_fuzz_lab_0110 audit
    python -m tools.nanojev_gamer_v1_g1_semantic_fuzz_lab_0110 selftest
    python -u -m tools.nanojev_gamer_v1_g1_semantic_fuzz_lab_0110 prepare
    python -u -m tools.nanojev_gamer_v1_g1_semantic_fuzz_lab_0110 run --device cuda
    python -u -m tools.nanojev_gamer_v1_g1_semantic_fuzz_lab_0110 run --profile expanded --device cuda
    python -m tools.nanojev_gamer_v1_g1_semantic_fuzz_lab_0110 analyze

The pilot seals 200 base-disjoint problems x 12 variants (2,400 prompts).
Expanded seals 1,000 base-disjoint problems x 30 variants (30,000 prompts).
Gold object spans/semantic graphs are created BEFORE any model inference. Each
transformation remaps all author spans, validates graph-execution invariants,
and is tracked by operator and base ID. Captures use the existing frozen G1
composite. Append-only verified journal permits restart without recomputation.
Results include typed/span-boundary/missing/extra errors and composition
interference, with minimal transformation-set counterexamples. No model writes.

Important limitation: these are synthetic templates, not natural-language
certification. An author-annotated semantic graph says what the generator
intended; appended alias/pronoun notes are deliberately unseen by graph nodes.
"""
from __future__ import annotations
import argparse
import copy
from collections import Counter, defaultdict
import hashlib
import json
import os
from pathlib import Path
import random
import re
import time

from tools import nanojev_gamer_v1_graph_corpus as corpus
from tools import nanojev_gamer_v1_graph_contract as contract
from tools import nanojev_gamer_v1_g2_lexical_geometry_intervention_0109 as prev
from tools import nanojev_gamer_v1_g2_error_atlas_0093 as atlas
from tools import nanojev_gamer_v1_g2_hard_negative_learning_0087 as hard
from tools import nanojev_gamer_v1_g1_native_composite_0082 as g1native
from tools import nanojev_gamer_v1_backbone_first as backbone
from tools import nanojev_gamer_v1_graph_curriculum as cur
from tools.nanojev_gamer_v1_graph_head import token_span_char_bounds

TAG='G2_0110_G1_FUZZ'
SCHEMA='nanojev.g1.semantic-fuzz-lab-0110.v1'
SEED=1102026
ROOT_DEFAULT=prev.old.ROOT_DEFAULT
OUT='g1-semantic-fuzz-0110'
SINGLES=('alias','pronoun','forward','rename','caps','numformat',
         'spacing','newline','sentence_split','prefix','suffix')
PAIRS=(('alias','forward'),('pronoun','forward'),('alias','newline'),
       ('pronoun','spacing'),('forward','newline'),('forward','prefix'),
       ('rename','forward'),('caps','spacing'),('caps','newline'),
       ('numformat','newline'),('numformat','suffix'),('rename','prefix'),
       ('spacing','suffix'),('sentence_split','prefix'),('sentence_split','numformat'),
       ('alias','suffix'),('pronoun','prefix'),('rename','newline'))
PROFILES={'pilot':(200,('control',)+SINGLES),
          'expanded':(1000,('control',)+SINGLES+tuple('+'.join(x) for x in PAIRS))}
assert len(PROFILES['pilot'][1])==12 and len(PROFILES['expanded'][1])==30


def require(ok,why):
    if not ok:raise ValueError(TAG+'_'+why)


def emit(action,datum):
    print(TAG+'_'+action+' '+json.dumps(datum,sort_keys=True,ensure_ascii=False,allow_nan=False),flush=True)


def htext(s):return hashlib.sha256(s.encode('utf-8')).hexdigest()


def hfile(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as fd:
        for b in iter(lambda:fd.read(1024*1024),b''):h.update(b)
    return h.hexdigest()


def serial(value):return json.dumps(value,sort_keys=True,ensure_ascii=False,allow_nan=False,separators=(',',':'))


def _write_immutable(path,data):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    if path.exists():
        require(path.read_bytes()==data,'IMMUTABLE_FILE_DRIFT_'+path.name)
        return False
    with path.open('xb') as fd:
        fd.write(data);fd.flush();os.fsync(fd.fileno())
    return True


def _nodes(row):return [n for n in row['graph']['nodes'] if 'span' in n]


def _validate(row,reference=None):
    text=row['prompt']; require(isinstance(text,str) and text and len(text)<=3000,'PROMPT_LENGTH_INVALID')
    ids=set();spans=[]
    for n in _nodes(row):
        require(n['id'] not in ids,'DUPLICATE_GOLD_ID');ids.add(n['id'])
        a,b=n['span'];require(isinstance(a,int) and isinstance(b,int) and 0<=a<b<=len(text),
                              'AUTHOR_SPAN_OUT_OF_RANGE_'+n['id'])
        require(text[a:b] and text[a:b]==text[a:b].strip(),'AUTHOR_SPAN_INCLUDES_WHITESPACE')
        spans.append((a,b,n['kind']))
    require(len(spans)==len(set(spans)),'DUPLICATE_GOLD_OBJECT_SPAN')
    order=sorted(spans)
    require(all(a>=z for (_,z,_),(a,_,_) in zip(order,order[1:])),
            'OVERLAPPING_GOLD_OBJECT_SPANS')
    expected=[{'kind':n['kind'],'start':n['span'][0],'end':n['span'][1]} for n in _nodes(row)]
    require(row['object_spans']==expected,'OBJECT_SPAN_MIRROR_DRIFT')
    contract.validate_graph(row['graph'],complete=True)
    require(contract.execute_graph(row['graph'])['decisions']==row['expected_decisions'],
            'GOLD_GRAPH_OBJECTIVE_CHANGED')
    if reference is not None:
        require(row['graph']['edges']==reference['graph']['edges'] and
                {n['id']:(n['kind'],n.get('value'),n.get('operator')) for n in row['graph']['nodes']}==
                {n['id']:(n['kind'],n.get('value'),n.get('operator')) for n in reference['graph']['nodes']} and
                row['graph']['objective']==reference['graph']['objective'],
                'SEMANTIC_GRAPH_MUTATED')
        require(len(_nodes(row))==len(_nodes(reference)),'GOLD_OBJECTS_CHANGED')
        require(row['expected_decisions']==reference['expected_decisions'],
                'EXPECTED_RESULT_CHANGED')
    return True


def _splice(row,a,b,replacement, node_id=None):
    """One span-safe literal edit, optionally rewriting exactly one gold node."""
    row=copy.deepcopy(row);prompt=row['prompt'];require(0<=a<=b<=len(prompt),'EDIT_OUT_OF_RANGE')
    require(isinstance(replacement,str),'BAD_EDIT_TEXT')
    for n in _nodes(row):
        x,y=n['span']
        if node_id is not None and n['id']==node_id:
            require(x==a and y==b,'WRONG_NODE_EDIT_RANGE')
            n['span']=[a,a+len(replacement)]
        elif x>=b:
            delta=len(replacement)-(b-a);n['span']=[x+delta,y+delta]
        else:
            require(y<=a,'EDIT_INTERSECTS_ANNOTATION_'+n['id'])
    if node_id is not None:require(any(n['id']==node_id for n in _nodes(row)),'EDIT_NODE_NOT_FOUND')
    row['prompt']=prompt[:a]+replacement+prompt[b:]
    prev._sync_spans(row)
    return row


def _edit_node(row,node_id,new):
    node=next(n for n in _nodes(row) if n['id']==node_id)
    a,b=node['span'];return _splice(row,a,b,new,node_id)


def _ref_original(row):
    n,_=prev._ref_node(row)
    links=next(e for e in row['graph']['edges'] if e['src']==n and e['type']=='refers_to')
    dest=next(a for a in _nodes(row) if a['id']==links['dst'])
    return n,row['prompt'][dest['span'][0]:dest['span'][1]]


def _operator(row,operator,base_index):
    """Apply a single verified edit; semantics and gold node IDs remain fixed."""
    if operator in ('alias','pronoun'):
        # Always choose the SAME author-marked mention, before and after other edits.
        src=next(n for n in _nodes(row) if n['id']==row['_ref_source_id'])
        destination=next(n for n in _nodes(row) if n['id']==row['_ref_target_id'])
        old=row['prompt'][destination['span'][0]:destination['span'][1]]
        if operator=='alias':
            alias='AliasF'+str(base_index).zfill(5)
            row=_edit_node(row,src['id'],alias)
            row=_splice(row,len(row['prompt']),len(row['prompt']),
                      f' Alias note: {alias} is another name for {old}.')
        else:
            row=_edit_node(row,src['id'],'it')
            row=_splice(row,len(row['prompt']),len(row['prompt']),
                      f" Pronoun note: the selected use of 'it' denotes {old}.")
    elif operator=='forward':
        row=prev._forward_quantity(row)
    elif operator in ('rename','caps'):
        src=next(n for n in _nodes(row) if n['id']==row['_ref_target_id'])
        original=row['prompt'][src['span'][0]:src['span'][1]]
        new=('RenamedOption'+str(base_index).zfill(5) if operator=='rename' else original.upper())
        require(new!=original,'NAME_CHANGE_NO_OP')
        # Change every annotated occurrence of the same semantic name, right to left.
        candidates=[n['id'] for n in _nodes(row) if n['kind']=='action' and
                    row['prompt'][n['span'][0]:n['span'][1]]==original]
        require(len(candidates)>=2,'NAME_NOT_REPEATED')
        candidates.sort(key=lambda nid:next(n['span'][0] for n in _nodes(row) if n['id']==nid),reverse=True)
        for nid in candidates:row=_edit_node(row,nid,new)
    elif operator=='numformat':
        quantities=[n for n in _nodes(row) if n['kind']=='quantity']
        require(bool(quantities),'QUANTITY_REQUIRED')
        n=quantities[0];a,b=n['span'];num=row['prompt'][a:b]
        require(bool(re.fullmatch(r'-?\d+',num)),'QUANTITY_FORMAT_UNKNOWN')
        replacement='-0'+num[1:] if num.startswith('-') else '0'+num
        row=_edit_node(row,n['id'],replacement)
    elif operator in ('spacing','newline','sentence_split'):
        needle='; '
        pos=row['prompt'].find(needle)
        require(pos>=0,'EXPECTED_LIST_DELIMITER_MISSING')
        repl={'spacing':';   ','newline':';\n','sentence_split':'.  '}[operator]
        row=_splice(row,pos,pos+len(needle),repl)
    elif operator=='prefix':
        row=_splice(row,0,0,'Read this decision description carefully. ')
    elif operator=='suffix':
        row=_splice(row,len(row['prompt']),len(row['prompt']),
                    ' End of the decision description; do not introduce new alternatives.')
    else:raise ValueError(TAG+'_UNKNOWN_OPERATOR_'+operator)
    return row


def _base(i, seed=SEED):
    family=('allocation' if i%2==0 else 'encounter')
    rng=random.Random(seed+i*7009)
    row=(corpus.allocation(rng,'challenge',2_110_000+i) if family=='allocation'
         else corpus.encounter(rng,'challenge',2_110_000+i))
    links=[e for e in row['graph']['edges'] if e['type']=='refers_to']
    nodes={n['id']:n for n in _nodes(row)}
    candidates=[e for e in links if nodes[e['src']]['kind']=='action' and
                nodes[e['dst']]['kind']=='action' and
                row['prompt'][nodes[e['src']]['span'][0]:nodes[e['src']]['span'][1]]==
                row['prompt'][nodes[e['dst']]['span'][0]:nodes[e['dst']]['span'][1]]]
    require(bool(candidates),'NO_CANONICAL_REFERENCE')
    edge=candidates[0]
    row['_ref_source_id']=edge['src'];row['_ref_target_id']=edge['dst']
    row['base_id']=f'g1-0110-{seed}-{i:05d}'
    row['family']=family
    _validate(row)
    return row


def _split(i):
    # Separate BOTH families by base id; no variant of a base crosses splits.
    bucket=(i//2)%10
    return 'discovery' if bucket<6 else ('validation' if bucket<8 else 'sealed_holdout')


def _variant(base, mode):
    row=copy.deepcopy(base)
    ops=() if mode=='control' else tuple(mode.split('+'))
    require(bool(ops) or mode=='control','EMPTY_OPERATOR')
    require(len(set(ops))==len(ops) and all(k in SINGLES for k in ops),
            'INVALID_OPERATOR_COMPOSITION')
    for operator in ops:row=_operator(row,operator,int(base['base_id'].rsplit('-',1)[-1]))
    _validate(row,base)
    row['mode']=mode;row['operators']=list(ops)
    row['case_id']=row['base_id']+'-'+mode
    row['split']=base['split']
    row['annotation_provenance']='AUTHOR_GENERATED_SEMANTIC_GRAPH_AND_SPANS'
    row['prompt_sha256']=htext(row['prompt'])
    del row['_ref_source_id'],row['_ref_target_id']
    return row


def _resolve(profile,bases):
    require(profile in PROFILES,'INVALID_PROFILE')
    declared,modes=PROFILES[profile]
    count=declared if bases is None else bases
    require(type(count) is int and count>=4 and count%2==0 and count<=declared,
            'INVALID_BASE_COUNT')
    return count,modes


def iter_generated(profile='pilot',bases=None):
    """Stream rows; never retain an entire 30k-case corpus in memory."""
    count,modes=_resolve(profile,bases)
    for i in range(count):
        b=_base(i);b['split']=_split(i)
        for mode in modes:
            yield _variant(b,mode)


def generate(profile='pilot',bases=None):
    """Small-cohort testing helper; expanded runs use iter_generated()."""
    count,modes=_resolve(profile,bases)
    require(count<=200,'GENERATE_LIST_FOR_LARGE_CORPUS_REFUSED_USE_STREAM')
    rows=list(iter_generated(profile,bases))
    require(len(rows)==count*len(modes),'CASE_COUNT_DRIFT')
    require(len({r['prompt_sha256'] for r in rows})==len(rows),'DUPLICATE_FUZZED_PROMPT')
    splits=defaultdict(set)
    for r in rows:splits[r['split']].add(r['base_id'])
    names=list(splits)
    require(all(splits[a].isdisjoint(splits[b]) for a in names for b in names if a<b),
            'BASE_LEAK_BETWEEN_PARTITIONS')
    return rows


class IndexedSeal:
    """Random access to an immutable JSONL file, retaining only byte offsets."""
    def __init__(self,path,count):
        self.path=Path(path);self.offsets=[]
        with self.path.open('rb') as reader:
            while True:
                pos=reader.tell();line=reader.readline()
                if not line:break
                require(line.endswith(b'\n'),'TORN_SEALED_ROW')
                self.offsets.append(pos)
        require(len(self.offsets)==count,'SEALED_COUNT_INVALID')
        self._reader=None
    def __len__(self):return len(self.offsets)
    def __getitem__(self,index):
        if index<0:index+=len(self)
        require(0<=index<len(self),'SEALED_INDEX_OUT_OF_RANGE')
        if self._reader is None:self._reader=self.path.open('rb')
        self._reader.seek(self.offsets[index])
        return json.loads(self._reader.readline())
    def __iter__(self):
        with self.path.open('r',encoding='utf-8') as fd:
            for line in fd:yield json.loads(line)
    def close(self):
        if self._reader is not None:self._reader.close();self._reader=None


def _output(root,output,profile):
    return ((Path(output) if output else Path(root)/OUT)/profile).resolve()


def _source_signature(root):
    paths=atlas._roots(root)
    composite=Path(paths['composite'])
    files={'g1_composite_manifest':composite/'composite-interface.json',
           'g1_head':composite/'gamer_graph_head.safetensors'}
    require(all(p.is_file() for p in files.values()),'FROZEN_G1_FILES_MISSING')
    result={key:hfile(value) for key,value in sorted(files.items())}
    result['source_adapter_sha256']=backbone.adapter_fingerprint(paths['adapter'])
    result['g1_runtime_code_sha256']=hfile(g1native.__file__)
    return result


def _prepare(root,output,profile,bases=None):
    count,modes=_resolve(profile,bases)
    dest=_output(root,output,profile)
    seal=dest/'seal.jsonl'
    source=_source_signature(root)
    if seal.exists():
        require((dest/'manifest.json').is_file(),'SEALED_MANIFEST_MISSING')
        manifest=json.loads((dest/'manifest.json').read_text(encoding='utf-8'))
        require(manifest.get('schema')==SCHEMA and manifest.get('profile')==profile and
                manifest.get('cases')==count*len(modes) and
                manifest.get('bases')==count and manifest.get('modes')==list(modes) and
                manifest.get('g1_source_sha256')==source and
                manifest.get('seed')==SEED and manifest.get('training') is False,
                'EXISTING_SEAL_OR_CHECKPOINT_DRIFT')
        require(hfile(seal)==manifest['seal_sha256'],'SEALED_CASES_CHANGED')
        return IndexedSeal(seal,manifest['cases']),manifest,dest,False
    dest.mkdir(parents=True,exist_ok=True)
    require(not (dest/'manifest.json').exists(),'MANIFEST_EXISTS_WITHOUT_SEAL')
    tmp=dest/'seal.jsonl.tmp'
    require(not tmp.exists(),'STALE_INCOMPLETE_SEAL_REFUSE')
    seen=set();bases_seen=set();counts=Counter()
    h=hashlib.sha256()
    try:
        with tmp.open('xb') as fd:
            for row in iter_generated(profile,bases):
                require(row['prompt_sha256'] not in seen,'DUPLICATE_FUZZED_PROMPT')
                seen.add(row['prompt_sha256']);bases_seen.add(row['base_id'])
                counts[row['split']]+=1
                line=(serial(row)+'\n').encode('utf-8')
                h.update(line);fd.write(line)
            fd.flush();os.fsync(fd.fileno())
        require(len(seen)==count*len(modes) and len(bases_seen)==count,
                'GENERATED_COUNT_DRIFT')
        manifest={'schema':SCHEMA,'profile':profile,'seed':SEED,'cases':len(seen),
                  'bases':count,'modes':list(modes),'split_counts':dict(counts),
                  'seal_sha256':h.hexdigest(),'g1_source_sha256':source,
                  'annotation_provenance':'GENERATED_BEFORE_MODEL_INFERENCE',
                  'frozen_g1':True,'gold_never_passed_to_predict':True,
                  'training':False,'research_only':True,
                  'generated_not_independent_natural_language_certification':True}
        # First commit the finished seal, then its hash-bearing manifest.
        os.replace(tmp,seal)
        _write_immutable(dest/'manifest.json',
                         (json.dumps(manifest,sort_keys=True,indent=2)+'\n').encode())
    finally:
        if tmp.exists():tmp.unlink()
    require(hfile(seal)==manifest['seal_sha256'],'SEAL_SHA_MISMATCH')
    return IndexedSeal(seal,manifest['cases']),manifest,dest,True


def audit(root=ROOT_DEFAULT,output=None,profile='pilot',bases=None):
    rows,manifest,dest,_=_prepare(root,output,profile,bases)
    refcount=sum(1 for r in rows if r['mode']=='control')
    emit('AUDIT',{'ready':True,'profile':profile,'cases':len(rows),
          'base_ids':refcount,'variants_per_base':len(PROFILES[profile][1]),
          'split_cases':dict(Counter(x['split'] for x in rows)),
          'families':dict(Counter(x['family'] for x in rows)),
          'sealed_prompt_sha256':manifest['seal_sha256'],
          'frozen_g1_sha256':manifest['g1_source_sha256'],
          'output':str(dest),'new_training':False,'g3_unlocked':False})


def _gold(row):
    return [{'id':n['id'],'kind':n['kind'],'start':n['span'][0],
             'end':n['span'][1],'text':row['prompt'][n['span'][0]:n['span'][1]]}
             for n in _nodes(row)]


def _weight(g,p):
    a,b=g['start'],g['end'];c,d=p['start'],p['end']
    overlap=max(0,min(b,d)-max(a,c))
    if not overlap:return 0
    same_span=(a,b)==(c,d)
    same_kind=g['kind']==p['kind']
    cat=(4 if same_span and same_kind else 3 if same_span else
         2 if same_kind else 1)
    # Prefer exact > type > boundary > boundary+type; tie-break by IoU.
    return cat*100_000+int(10_000*overlap/(max(b,d)-min(a,c)))


def _hungarian(cost):
    """Minimum-weight assignment for a square matrix, O(n^3), pure Python."""
    n=len(cost)
    if not n:return []
    u=[0]*(n+1);v=[0]*(n+1);p=[0]*(n+1);way=[0]*(n+1)
    for i in range(1,n+1):
        p[0]=i;minv=[10**18]*(n+1);used=[False]*(n+1);j0=0
        while True:
            used[j0]=True;i0=p[j0];delta=10**18;j1=0
            for j in range(1,n+1):
                if used[j]:continue
                cur=cost[i0-1][j-1]-u[i0]-v[j]
                if cur<minv[j]:minv[j]=cur;way[j]=j0
                if minv[j]<delta:delta=minv[j];j1=j
            for j in range(n+1):
                if used[j]:u[p[j]]+=delta;v[j]-=delta
                else:minv[j]-=delta
            j0=j1
            if p[j0]==0:break
        while True:
            j1=way[j0];p[j0]=p[j1];j0=j1
            if j0==0:break
    assignment=[-1]*n
    for j in range(1,n+1):
        if p[j]:assignment[p[j]-1]=j-1
    return assignment


def classify(row,predicted):
    """One-to-one span matching, priority-preserving maximal-weight assignment."""
    gold=_gold(row);pred=[]
    for item in predicted:
        require(len(item)==3,'PREDICTED_SPAN_RECORD_INVALID')
        a,b,k=item
        require(type(a) is int and type(b) is int and 0<=a<b<=len(row['prompt']),
                'PREDICTED_SPAN_OUT_OF_RANGE')
        pred.append({'kind':str(k),'start':a,'end':b,'text':row['prompt'][a:b]})
    m,n=len(gold),len(pred);size=max(m,n)
    if size:
        weights=[[_weight(gold[i],pred[j]) if i<m and j<n else 0
                  for j in range(size)] for i in range(size)]
        matches=_hungarian([[-v for v in line] for line in weights])
    else:weights=[];matches=[]
    matched_pred=set();counts=Counter();errors=[]
    for i,g in enumerate(gold):
        j=matches[i] if i<size else -1
        if j<0 or j>=n or not weights[i][j]:
            name='missing';pair=None
        else:
            pair=pred[j];matched_pred.add(j)
            same_span=(g['start'],g['end'])==(pair['start'],pair['end'])
            same_kind=g['kind']==pair['kind']
            name=('exact' if same_span and same_kind else 'type' if same_span else
                  'boundary' if same_kind else 'boundary_and_type')
        counts[name]+=1
        counts['gold_kind|'+g['kind']+'|'+name]+=1
        if name!='exact':errors.append({'category':name,'gold':g,'predicted':pair})
    for j,prediction in enumerate(pred):
        if j not in matched_pred:
            counts['extra']+=1
            counts['pred_kind|'+prediction['kind']+'|extra']+=1
            errors.append({'category':'extra','gold':None,'predicted':prediction})
    counts['gold_objects']=m;counts['predicted_objects']=n
    counts['strict_correct']=counts['exact']
    counts['strict_wrong_gold']=m-counts['exact']
    counts['exact_graph']=int(len(errors)==0)
    require(counts['strict_wrong_gold']==counts['missing']+counts['type']+
            counts['boundary']+counts['boundary_and_type'],'GOLD_ERROR_ACCOUNTING_DRIFT')
    require(counts['exact']+counts['type']+counts['boundary']+
            counts['boundary_and_type']+counts['extra']==n,'PREDICTED_ERROR_ACCOUNTING_DRIFT')
    return {'counts':dict(counts),'errors':errors}


def _capture_g1(net,tok,composite,row,device):
    """Existing G1 inference path; author spans never enter prediction."""
    import torch
    with torch.inference_mode():
        # Do NOT call graph_curriculum.capture() here: it also computes gold
        # aligned_labels(row). Freeze G1, prepare text and encode it without
        # presenting author annotations to the prediction pathway.
        prepared=backbone.prepare_prompt(tok,row['prompt'],max_input_tokens=2048)
        hidden=backbone.frozen_context(net,prepared,device=device).detach()
        predicted=composite.predict(hidden.to(device),prepared,
                                    family=row['family'],native_stage='G1')
        result=[]
        for span in predicted['token_spans']:
            mapped=token_span_char_bounds(span,prepared)
            if mapped is None:return [],'UNMAPPED_G1_SPAN'
            result.append([int(mapped[0]),int(mapped[1]),str(span['kind'])])
        return result,None


def _load_journal(path,rows,manifest):
    """All committed entries must be consecutive, valid and tied to sealed text."""
    entries=[]
    if not path.exists():return entries
    with path.open('r',encoding='utf-8') as fh:
        for line in fh:
            require(line.endswith('\n'),'TORN_JOURNAL_RECORD_REFUSE_REPAIR')
            record=json.loads(line)
            index=len(entries)
            require(index<len(rows),'JOURNAL_TOO_LONG')
            expected=rows[index]
            require(record.get('schema')==SCHEMA and
                    record.get('seal_sha256')==manifest['seal_sha256'] and
                    record.get('g1_source_sha256')==manifest['g1_source_sha256'] and
                    record.get('case_id')==expected['case_id'] and
                    record.get('prompt_sha256')==expected['prompt_sha256'],
                    'JOURNAL_SOURCE_OR_CASE_DRIFT_AT_'+str(index))
            result=record.get('predicted')
            require(isinstance(result,list),'JOURNAL_PREDICTIONS_MISSING')
            # Validate all stored spans on reopening, not merely case IDs.
            classify(expected,result)
            entries.append(record)
    return entries


def _resume_journal(path,rows,manifest):
    """Recover only an uncommitted trailing partial line after interruption.

    A wrong hash, complete but wrong record, or modified earlier line ALWAYS
    fails. We only truncate the final bytes after the last valid newline.
    """
    path=Path(path)
    try:return _load_journal(path,rows,manifest)
    except ValueError as exc:
        require(str(exc)==TAG+'_TORN_JOURNAL_RECORD_REFUSE_REPAIR',
                'JOURNAL_INTEGRITY_ERROR_'+str(exc))
        with path.open('r+b') as fd:
            fd.seek(0,os.SEEK_END);length=fd.tell()
            tail_size=min(length,1_048_576)
            fd.seek(length-tail_size)
            tail=fd.read()
            cut=tail.rfind(b'\n')
            require(cut>=0 and cut<len(tail)-1,'JOURNAL_TAIL_NOT_BOUNDED')
            position=length-tail_size+cut+1
            # Check complete lines before changing anything.
            # The full journal verifier will reject all earlier tampering.
            fd.truncate(position);fd.flush();os.fsync(fd.fileno())
        emit('RECOVERED_INCOMPLETE_TAIL',{'journal':str(path),'discarded_bytes':length-position})
        return _load_journal(path,rows,manifest)


def run(root=ROOT_DEFAULT,output=None,profile='pilot',device='cuda',bases=None,max_cases=0):
    rows,manifest,dest,_=_prepare(root,output,profile,bases)
    journal=dest/'capture.jsonl'
    already=_resume_journal(journal,rows,manifest)
    if (dest/'result.json').exists():
        require(len(already)==len(rows),'RESULT_EXISTS_WITH_INCOMPLETE_JOURNAL')
        existing=json.loads((dest/'result.json').read_text(encoding='utf-8'))
        require(existing.get('schema')==SCHEMA and
                existing.get('source_seal_sha256')==manifest['seal_sha256'] and
                existing.get('capture_journal_sha256')==hfile(journal),
                'EXISTING_RESULT_OR_CAPTURE_JOURNAL_DRIFT')
        emit('ALREADY_COMPLETE',{'profile':profile,'cases':len(already),'report':str(dest/'result.json')})
        return
    if len(already)==len(rows):
        analyze(root,output,profile,bases)
        return
    require(type(max_cases) is int and max_cases>=0,'INVALID_MAX_CASES')
    require(device in ('cpu','cuda') or device.startswith('cuda:'),'INVALID_DEVICE')
    paths=atlas._roots(root)
    require(_source_signature(root)==manifest['g1_source_sha256'],'G1_CHECKPOINT_CHANGED_AFTER_SEAL')
    target=len(rows) if not max_cases else min(len(rows),len(already)+max_cases)
    record,_=hard._verify_trained(paths,paths['parent_0087'])
    net,tok=backbone.model_components(record['model_id'],paths['adapter'],device)
    require(all(not x.requires_grad for x in net.parameters()),'BACKBONE_NOT_FROZEN')
    composite=g1native.load_composite(paths['composite'],device=device)
    emit('START',{'profile':profile,'device':device,'cases':len(rows),
                  'already_captured':len(already),'target':target,
                  'source':manifest['g1_source_sha256'],
                  'gold_at_inference':False,'model_training':False,
                  'checkpoint_writes':False})
    cumulative=Counter();start=time.monotonic()
    with journal.open('a',encoding='utf-8',newline='\n') as handle:
        for index in range(len(already),target):
            row=rows[index]
            try:
                pred,error=_capture_g1(net,tok,composite,row,device)
            except ValueError as exc:
                # This one known, bounded char-span mapping failure is a failed
                # G1 capture, never an excuse to inject gold spans.
                if 'UNMAPPED_G1_SPAN' not in str(exc):raise
                pred,error=[],'UNMAPPED_G1_SPAN'
            metrics=classify(row,pred)
            rec={'schema':SCHEMA,'seal_sha256':manifest['seal_sha256'],
                 'g1_source_sha256':manifest['g1_source_sha256'],
                 'case_id':row['case_id'],'prompt_sha256':row['prompt_sha256'],
                 'predicted':pred,'capture_error':error}
            handle.write(serial(rec)+'\n');handle.flush()
            if (index+1)%25==0 or index+1==target:os.fsync(handle.fileno())
            cumulative.update(metrics['counts'])
            if index==len(already) or (index+1)%25==0 or index+1==target:
                emit('CAPTURE',{'done':index+1,'of':len(rows),'profile':profile,
                     'newly_exact':cumulative['exact_graph'],
                     'newly_missing':cumulative['missing'],
                     'newly_extra':cumulative['extra'],
                     'seconds':round(time.monotonic()-start,1)})
    if target==len(rows):
        analyze(root,output,profile,bases)
    else:
        emit('PARTIAL_RESUMABLE',{'captured':target,'of':len(rows),
                                  'journal':str(journal),'training':False})


def _ratio(a,b):return (a/b if b else 0.0)


def _metrics(rows):
    c=Counter()
    for a in rows:c.update(a['counts'] if 'counts' in a else a)
    kinds=defaultdict(dict)
    for key,value in c.items():
        if key.startswith('gold_kind|'):
            _,kind,category=key.split('|',2);kinds[kind][category]=value
    return {**dict(c),'by_gold_kind':dict(kinds),'cases':len(rows),'exact':c['exact_graph'],
            'strict_object_exact':c['exact'],
            'exact_rate':_ratio(c['exact_graph'],len(rows)),
            'strict_span_precision':_ratio(c['strict_correct'],c['predicted_objects']),
            'strict_span_recall':_ratio(c['strict_correct'],c['gold_objects'])}


def _minimal_examples(rows,results,limit):
    """Minimize transformation sets using frozen scored single variants.

    Streaming by base prevents large expanded corpora from retaining thousands
    of copy-on-write semantic graph dictionaries in RAM.
    """
    minimal=[];interaction=Counter();new_failure=Counter()
    variants={};current=None
    def process(variants):
        if not variants:return
        control,metric=variants['control']
        if not metric['counts']['exact_graph']:return
        for mode,(row,result) in variants.items():
            if mode=='control' or result['counts']['exact_graph']:continue
            ops=row['operators'];atomic=[variants[op][1]['counts']['exact_graph'] for op in ops]
            if len(ops)==1:label='single_operator';essential=ops
            elif len(ops)==2 and all(atomic):
                label='interaction_only';essential=ops
            else:
                label='single_already_fails'
                essential=[op for op,state in zip(ops,atomic) if not state]
            interaction[f'{label}|{mode}']+=1
            new_failure[mode]+=1
            if len(minimal)<limit:
                minimal.append({'base_id':row['base_id'],'family':row['family'],
                     'split':row['split'],'mode':mode,
                     'minimal_transform_set':essential,'classification':label,
                     'prompt':row['prompt'],'base_prompt':control['prompt'],
                     'gold_objects':_gold(row),'predicted':result['predicted'],
                     'errors':result['errors'][:18]})
    for row in rows:
        if current is not None and row['base_id']!=current:
            process(variants);variants={}
        current=row['base_id']
        variants[row['mode']]=(row,results[row['case_id']])
    process(variants)
    return minimal,dict(interaction),dict(new_failure)


def _text_examples(path,report,limit):
    lines=['G2-0110 — FROZEN G1 FUZZING, ERROR AND INTERFERENCE ATLAS',
           'Synthetic research evidence; no model training or certification.',
           f"Cases: {report['overall']['cases']}  Exact: {report['overall']['exact']} ",
           '']
    for x in report['minimal_counterexamples'][:limit]:
        lines.extend([f"CASE {x['base_id']}  [{x['family']}/{x['split']}] {x['mode']}",
          f"MINIMAL TRANSFORMS: {','.join(x['minimal_transform_set'])} ({x['classification']})",
          f"BASE: {x['base_prompt']}",f"FUZZ: {x['prompt']}"])
        for e in x['errors'][:10]:
            gs=e.get('gold');ps=e.get('predicted')
            lines.append(f"  {e['category']}: gold={None if not gs else (gs['kind'],gs['text'],gs['start'],gs['end'])!r} predicted={None if not ps else (ps['kind'],ps['text'],ps['start'],ps['end'])!r}")
        lines.append('')
    path.write_text('\n'.join(lines)+'\n',encoding='utf-8')


def analyze(root=ROOT_DEFAULT,output=None,profile='pilot',bases=None,example_limit=80):
    rows,manifest,dest,_=_prepare(root,output,profile,bases)
    journal=_load_journal(dest/'capture.jsonl',rows,manifest)
    require(len(journal)==len(rows),'INCOMPLETE_CAPTURE_NO_FINAL_METRICS')
    recordmap={x['case_id']:x for x in journal}
    results={}
    groups={'mode':defaultdict(list),'family':defaultdict(list),
            'split':defaultdict(list),'operator':defaultdict(list),
            'family_x_mode':defaultdict(list),'split_x_mode':defaultdict(list)}
    counts=Counter()
    for row in rows:
        rec=recordmap[row['case_id']]
        analyzed=classify(row,rec['predicted'])
        analyzed['predicted']=rec['predicted'];analyzed['capture_error']=rec.get('capture_error')
        results[row['case_id']]=analyzed
        datum=analyzed['counts'];counts.update(datum)
        for k,val in [('mode',row['mode']),('family',row['family']),('split',row['split']),
                      ('family_x_mode',row['family']+'|'+row['mode']),
                      ('split_x_mode',row['split']+'|'+row['mode'])]:groups[k][val].append(datum)
        for op in row['operators']:groups['operator'][op].append(datum)
    # Compounded errors are included in BOTH affected feature groups; comparisons
    # with 'mode' retain an exact partition of all cases.
    examples,interaction,new_failures=_minimal_examples(rows,results,example_limit)
    baselines={row['base_id']:results[row['case_id']]['counts']['exact_graph']
               for row in rows if row['mode']=='control'}
    prompt_failure=Counter();paired_comparisons=Counter()
    for row in rows:
        if row['mode']=='control':continue
        old=baselines[row['base_id']]
        new=results[row['case_id']]['counts']['exact_graph']
        prompt_failure[f'base_{"exact" if old else "wrong"}_variant_{"exact" if new else "wrong"}']+=1
        if old and not new:paired_comparisons[row['mode']]+=1
    summary={k:{name:_metrics(val) for name,val in sorted(group.items())}
             for k,group in groups.items()}
    overall=_metrics([results[r['case_id']]['counts'] for r in rows])
    report={'schema':SCHEMA,'profile':profile,'seed':SEED,'overall':overall,
            'grouped':summary,'transform_interference':interaction,
            'new_errors_after_clean_base':dict(new_failures),
            'paired_base_transition':dict(prompt_failure),
            'base_disjoint_partitions':True,
            'case_count':len(rows),'base_count':manifest['bases'],
            'source_manifest_sha256':hfile(dest/'manifest.json'),
            'source_seal_sha256':manifest['seal_sha256'],
            'source_g1_sha256':manifest['g1_source_sha256'],
            'capture_journal_sha256':hfile(dest/'capture.jsonl'),
            'minimal_counterexamples':examples,
            'case_level':[{'case_id':r['case_id'],'base_id':r['base_id'],
                            'family':r['family'],'split':r['split'],'mode':r['mode'],
                            'counts':results[r['case_id']]['counts'],
                            'capture_error':results[r['case_id']]['capture_error']}
                          for r in rows],
            'model_training':False,'checkpoint_writes':False,'promotion':False,
            'g3_unlocked':False,'gold_only_for_evaluation':True,
            'interpretation_limits':['All cases are synthetic generator templates',
                'Object exact requires gold kind and exact character span',
                'Minimal examples are minimized over generated transform sets, not token text',
                'Only previously correct base prompts support newly introduced failure claims',
                'One-to-one span pairing is a maximum-weight match, not a linguistic judgment']}
    dest.mkdir(parents=True,exist_ok=True)
    output_file=dest/'result.json'
    _write_immutable(output_file,(json.dumps(report,sort_keys=True,indent=2,ensure_ascii=False)+'\n').encode())
    _text_examples(dest/'counterexamples.txt',report,example_limit)
    emit('DIAGNOSIS',{'profile':profile,'overall':overall,
             'split':{k:{'exact':v['exact'],'cases':v['cases']} for k,v in summary['split'].items()},
             'mode':{k:{'exact':v['exact'],'cases':v['cases']} for k,v in summary['mode'].items()},
             'interference':interaction,'base_transitions':dict(prompt_failure)})
    emit('SAVED',{'report':str(output_file),'examples':str(dest/'counterexamples.txt'),
                  'sha256':hfile(output_file),'frozen':True,'model_training':False})


def selftest():
    rows=generate('pilot',6)
    require(len(rows)==72,'SELFTEST_CASE_COUNT')
    for row in rows:
        _validate(row)
        predicted=[[n['span'][0],n['span'][1],n['kind']] for n in _nodes(row)]
        exact=classify(row,predicted)
        require(exact['counts']['exact_graph']==1,'SELFTEST_GOLD_REPLAY_NOT_EXACT')
    sample=rows[0]
    gold=[[n['span'][0],n['span'][1],n['kind']] for n in _nodes(sample)]
    # Synthetic perturbation categories with mutually exclusive matching.
    altered=copy.deepcopy(gold)
    altered[0][2]='quantity' if altered[0][2]!='quantity' else 'actor'
    err=classify(sample,altered)
    require(err['counts']['type']==1 and err['counts']['exact_graph']==0,'TYPE_DIAGNOSIS_BROKEN')
    altered=copy.deepcopy(gold)
    i=next(i for i,(a,b,kind) in enumerate(altered) if b-a>=2)
    altered[i][1]-=1
    err=classify(sample,altered)
    require(err['counts']['boundary']==1,'BOUNDARY_DIAGNOSIS_BROKEN')
    altered=copy.deepcopy(gold);altered.pop(0)
    err=classify(sample,altered)
    require(err['counts']['missing']==1,'MISSING_DIAGNOSIS_BROKEN')
    altered=copy.deepcopy(gold);altered.append([len(sample['prompt'])-1,len(sample['prompt']),'actor'])
    err=classify(sample,altered)
    require(err['counts']['extra']==1,'EXTRA_DIAGNOSIS_BROKEN')
    # Synthetic interaction: singles are exact; their combined variant fails.
    inp=[];mock={}
    for row in rows:
        predicted=[[n['span'][0],n['span'][1],n['kind']] for n in _nodes(row)]
        if row['mode'] in ('alias','forward') and row['base_id']==rows[0]['base_id']:
            pass
        mock[row['case_id']]={'predicted':predicted,'errors':[],
                 'counts':classify(row,predicted)['counts']}
    # Tests an expanded pair on 2 synthetic bases without inferring on any model.
    expanded=generate('expanded',4)
    byid={r['case_id']:r for r in expanded}
    for row in expanded:
        p=[[n['span'][0],n['span'][1],n['kind']] for n in _nodes(row)]
        if row['mode']=='alias+forward' and row['base_id']==expanded[0]['base_id']:
            p=p[1:]
        d=classify(row,p);mock[row['case_id']]={'predicted':p,'errors':d['errors'],'counts':d['counts']}
    minimum,interactions,_=_minimal_examples(expanded,mock,20)
    require(interactions.get('interaction_only|alias+forward')==1,
            'INTERFERENCE_DIAGNOSTIC_BROKEN')
    # A changed annotated-object span must not pass the author checks.
    corrupted=copy.deepcopy(sample);_nodes(corrupted)[0]['span'][0]=-1
    try:_validate(corrupted)
    except ValueError:pass
    else:raise AssertionError('TAMPERED_SPAN_NOT_REJECTED')
    # Real append-only resume + final report path, with a synthetic predictor.
    import tempfile
    saved=globals()['_source_signature']
    try:
        globals()['_source_signature']=lambda _root:{'synthetic_only':'g1-fixture'}
        with tempfile.TemporaryDirectory() as folder:
            sealed,manifest,dest,_=_prepare(folder,folder,'pilot',6)
            require(len(sealed)==72,'TEMP_SEAL_SIZE_BAD')
            require(len(IndexedSeal(dest/'seal.jsonl',72))==72,'INDEXED_SEAL_DRIFT')
            with (dest/'capture.jsonl').open('w',encoding='utf-8') as fd:
                for i,r in enumerate(sealed):
                    pred=[[n['span'][0],n['span'][1],n['kind']] for n in _nodes(r)]
                    if r['mode']=='alias':pred=pred[:-1]
                    record={'schema':SCHEMA,'seal_sha256':manifest['seal_sha256'],
                         'g1_source_sha256':manifest['g1_source_sha256'],
                         'case_id':r['case_id'],'prompt_sha256':r['prompt_sha256'],
                         'predicted':pred,'capture_error':None}
                    fd.write(serial(record)+'\n')
            loaded=_load_journal(dest/'capture.jsonl',sealed,manifest)
            require(len(loaded)==72,'RESUMABLE_JOURNAL_VERIFICATION_FAILED')
            with (dest/'capture.jsonl').open('ab') as fd:
                fd.write(b'{"uncommitted":')
            recovered=_resume_journal(dest/'capture.jsonl',sealed,manifest)
            require(len(recovered)==72,'PARTIAL_TAIL_RECOVERY_FAILED')
            analyze(folder,folder,'pilot',6,example_limit=8)
            summary=json.loads((dest/'result.json').read_text(encoding='utf-8'))
            require(summary['overall']['cases']==72 and
                    summary['grouped']['mode']['alias']['exact']==0 and
                    summary['grouped']['mode']['control']['exact']==6 and
                    (dest/'counterexamples.txt').is_file(),
                    'SYNTHETIC_END_TO_END_DIAGNOSIS_FAILED')
            record=copy.deepcopy(loaded[0]);record['prompt_sha256']='tampered'
            with (dest/'capture.jsonl').open('w',encoding='utf-8') as fd:
                fd.write(serial(record)+'\n')
            try:_load_journal(dest/'capture.jsonl',sealed,manifest)
            except ValueError:pass
            else:raise AssertionError('JOURNAL_TAMPER_NOT_REJECTED')
    finally:
        globals()['_source_signature']=saved
    emit('SELFTEST',{'pilot_cases':72,'expanded_cases':120,
          'matched_errors':['type','boundary','missing','extra'],
          'metamorphic_semantic_graph_preservation':True,
          'compositional_interference_detected':True,
          'tampered_author_spans_rejected':True,
          'resumable_journal_lineage_guard':True,
          'model_training':False,'checkpoint_writes':False})


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('command',nargs='?',choices=('audit','selftest','prepare','run','analyze'),default='audit')
    p.add_argument('--root',default=ROOT_DEFAULT)
    p.add_argument('--output')
    p.add_argument('--profile',choices=tuple(PROFILES),default='pilot')
    p.add_argument('--device',default='cuda')
    p.add_argument('--bases',type=int)
    p.add_argument('--max-cases',type=int,default=0)
    p.add_argument('--examples',type=int,default=80)
    a=p.parse_args(argv)
    if a.command=='selftest':selftest()
    elif a.command=='audit':audit(a.root,a.output,a.profile,a.bases)
    elif a.command=='prepare':
        rows,m,d,w=_prepare(a.root,a.output,a.profile,a.bases)
        emit('SEALED',{'cases':len(rows),'bases':m['bases'],'profile':a.profile,
                       'seal_sha256':m['seal_sha256'],'newly_written':w,'destination':str(d),
                       'model_training':False})
    elif a.command=='run':run(a.root,a.output,a.profile,a.device,a.bases,a.max_cases)
    else:analyze(a.root,a.output,a.profile,a.bases,a.examples)

if __name__=='__main__':main()
