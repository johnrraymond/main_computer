#!/usr/bin/env python3
"""Frozen-Qwen semantic-interface repair: targeted grammar versus grounded slots.

Controlled arms, all using final:mean and the same gold training tuples:
  prior_augmented: 216 original + 216 prior grammar examples
  count_control: prior_augmented + 216 duplicate original examples
  targeted_grammar: prior_augmented + 216 NEW training-only syntax examples
  targeted_lexical: targeted_grammar + text-only asset and polarity evidence
  targeted_typed: targeted_lexical + text-only role spans, unknown names,
                  nested Boolean scope, and explicit previous-supplier references.

The structural oracle uses evaluation labels only; inference methods receive
text and fitted models, never challenge IDs, gold tuples or grammar IDs. The
holdout uses independently generated semantic pairs and novel templates; old
inspected final cases are not reused for method selection. All inputs remain
synthetic, explicitly clause-delimited and from limited authored grammars.
No contract seal, model training, vocabulary change, or production mutation.

From repository root:
  & $py -m tools.nanojev_sealed_overlap_semantic_interface_repair_bakeoff \
    --device cuda --output runtime/reports/sealed-overlap-semantic-interface-repair-0001.json
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from hashlib import sha256
import itertools
import json
from pathlib import Path
import random
import re
from string import Formatter
from typing import Callable

import numpy as np

from tools import nanojev_sealed_overlap_information_smoke as smoke
from tools import nanojev_sealed_overlap_composition_bakeoff as composition
from tools import nanojev_sealed_overlap_polarity_layers_frob as layers
from tools import nanojev_sealed_overlap_relation_binding_bakeoff as binding
from tools import nanojev_sealed_overlap_language_stress_bakeoff as stress
from tools import nanojev_sealed_overlap_semantic_interface_bakeoff as previous

SCHEMA="nanojev.sealed-overlap.semantic-interface-repair-bakeoff.v1"
STATUS="COMPLETE_SEMANTIC_INTERFACE_REPAIR_DIAGNOSTIC_NO_SEAL_NO_PROMOTION"
HERE=Path(__file__).resolve().parent
FIXTURE=HERE/"fixtures"/"nanojev_overlap_semantic_interface_repair_v1.json"
PRIOR_REPORT=Path("runtime/reports/sealed-overlap-semantic-interface-0001.json")
ARMS=("prior_augmented","count_control","targeted_grammar","targeted_lexical","targeted_typed")
IN_SCHEMA=tuple(binding.case_variants(("Mira","Arlen","sensor","the permit is approved","access is suspended",1),
                                      ("Tarin","Bela","ledger","the audit is complete","payment is withheld",-1),
                                      binding.load_fixture(binding.FIXTURE)))
CHALLENGES=IN_SCHEMA+("oov_actor","oov_recipient","dual_oov","nested_and","nested_boolean","anaphoric_actor","multihop_actor")
OOS=tuple(k for k in CHALLENGES if k not in IN_SCHEMA)
WORD=r"[A-Z][A-Za-z]{2,}"


def load_fixture(path:Path,base:dict,old:dict,stress_fixture:dict)->dict:
    f=json.loads(path.read_text(encoding="utf-8"))
    if f.get("schema")!="nanojev.sealed-overlap.semantic-interface-repair-fixture.v1" or f.get("document_delimiter")!=" || ":
        raise ValueError("INTERFACE_REPAIR_FIXTURE_SCHEMA_INVALID")
    prior=set(sum((base['templates'][x] for x in ('train','selection','final')),[]))
    prior.update(stress_fixture['selection_templates']+stress_fixture['final_templates'])
    prior.update(old['augmentation_templates']+old['fresh_templates'])
    new=set()
    for k,n in (("targeted_training_templates",3),("new_holdout_templates",4)):
        ts=f.get(k)
        if not isinstance(ts,list) or len(ts)!=n:raise ValueError("INTERFACE_REPAIR_TEMPLATE_COUNT_INVALID")
        for template in ts:
            fields=[p for _,p,_,_ in Formatter().parse(template) if p is not None]
            required=set(binding.FIELDS[:-1])|({"modal"} if "{modal}" in template else {"duty"})
            if (set(fields)!=required or len(fields)!=6 or "." in template or " || " in template
                or template in prior or template in new):
                raise ValueError("INTERFACE_REPAIR_TEMPLATE_INVALID")
            new.add(template)
    if f.get('positive')!=old['positive'] or f.get('negative')!=old['negative']:
        raise ValueError("INTERFACE_REPAIR_POLARITY_INVALID")
    refs=f.get('reference_markers')
    if refs!=[old['reference_marker'],"that supplier","the immediately previous supplier"]:
        raise ValueError("INTERFACE_REPAIR_REFERENCES_INVALID")
    if any(not isinstance(f.get(k),list) or len(f[k])<4 or len(set(f[k]))!=len(f[k]) for k in ('new_actors','new_recipients')):
        raise ValueError("INTERFACE_REPAIR_OOV_INVALID")
    known=set(base['actors']+base['recipients'])
    if known.intersection(f['new_actors']+f['new_recipients']):raise ValueError("INTERFACE_REPAIR_OOV_LEAK")
    return f


def validate_prior(path:Path,base_path:Path,stress_path:Path,interface_path:Path)->dict:
    r=json.loads(path.read_text(encoding='utf-8'))
    if (r.get('schema')!=previous.SCHEMA or r.get('status')!=previous.STATUS
        or r.get('acceptance',{}).get('no_production_mutation') is not True
        or r.get('acceptance',{}).get('no_final_based_model_selection') is not True
        or r.get('design',{}).get('view')!='final:mean'
        or r.get('sources',{}).get('base_fixture_sha256')!=sha256(base_path.read_bytes()).hexdigest()
        or r.get('sources',{}).get('stress_fixture_sha256')!=sha256(stress_path.read_bytes()).hexdigest()
        or r.get('sources',{}).get('interface_fixture_sha256')!=sha256(interface_path.read_bytes()).hexdigest()
        or r.get('design',{}).get('no_new_distinct_gold_training_tuples') is not True
        or r.get('design',{}).get('augmentation_examples') != 2*r.get('design',{}).get('original_training_examples',0)
        or not {'grammar_augmented','duplicate_control','original','augmented_typed_surface'}.issubset(r.get('fresh',{}))
        or not all(k in r.get('fresh',{}).get('grammar_augmented',{}).get('in_schema',{}).get('by_field',{}) for k in ('asset','polarity'))
        or not {'0','1','2'}.issubset(r.get('fresh',{}).get('grammar_augmented',{}).get('by_grammar_template',{}))):
        raise ValueError("INTERFACE_REPAIR_PRIOR_REPORT_INVALID")
    return r


def render(atom:tuple,fixture:dict,mode:str,index:int)->str:
    if len(atom)!=6 or atom[-1] not in (-1,1):raise ValueError('INTERFACE_REPAIR_ATOM_INVALID')
    phr=fixture['positive' if atom[-1]==1 else 'negative']
    return fixture[mode][index%len(fixture[mode])].format(**dict(zip(binding.FIELDS,atom)),**phr)


def targeted_training(train:list[dict],f:dict)->list[dict]:
    extra=[{'text':render(r['atom'],f,'targeted_training_templates',i),'atom':r['atom']} for i,r in enumerate(train)]
    if len(set(r['text'] for r in extra))!=len(extra) or {r['text'] for r in extra}&{r['text'] for r in train}:
        raise ValueError('INTERFACE_REPAIR_TRAIN_TEXT_DUPLICATE')
    return extra


def make_fresh(data:dict,base:dict,old_fixture:dict,stress_fixture:dict,fixture:dict,*,seed:int,groups:int)->list[dict]:
    if groups<8 or groups%8:raise ValueError('INTERFACE_REPAIR_GROUPS_INVALID')
    old=previous.fresh_data(data,base,stress_fixture,old_fixture,seed=seed,groups=groups)
    used={tuple(sorted(r['atoms'])) for r in old if r['kind']=='base'}
    used.update(tuple(sorted(r['atoms'])) for phase in ('selection','final') for r in data[phase] if r['kind']=='base')
    training={r['atom'] for r in data['train']}
    unseen=sorted(set(itertools.product(*binding.values(base)))-training)
    rng=random.Random(seed+79109)
    rows=[]
    for g in range(groups):
        pool=sorted(training) if g%2==0 else unseen
        for _ in range(20000):
            a,b=rng.sample(pool,2)
            pair=tuple(sorted((a,b)))
            if pair not in used and all(a[j]!=b[j] for j in range(5)):
                used.add(pair);break
        else:raise ValueError('INTERFACE_REPAIR_NEW_PAIRS_EXHAUSTED')
        variants=binding.case_variants(a,b,base)
        na=fixture['new_actors'][g%len(fixture['new_actors'])]
        nr=fixture['new_recipients'][g%len(fixture['new_recipients'])]
        other=[c for c in base['conditions'] if c!=a[3]]
        nested=stress_fixture['nested_condition_connector'].format(first=a[3],second=other[0])
        boolean='('+a[3]+' and ('+other[0]+' or not '+other[1]+'))'
        ref_a=b[:0]+(a[0],)+b[1:]
        ref_b=a[:0]+(b[0],)+a[1:]
        variants.update({
            'oov_actor':[(na,)+a[1:],b],
            'oov_recipient':[a,b[:1]+(nr,)+b[2:]],
            'dual_oov':[(na,nr)+a[2:],b],
            'nested_and':[a[:3]+(nested,)+a[4:],b],
            'nested_boolean':[a[:3]+(boolean,)+a[4:],b],
            'anaphoric_actor':[a,ref_a],
            'multihop_actor':[a,b,ref_b],
        })
        if set(variants)!=set(CHALLENGES):raise ValueError('INTERFACE_REPAIR_KINDS_INVALID')
        for kind,atoms in variants.items():
            clauses=[render(atom,fixture,'new_holdout_templates',g+i) for i,atom in enumerate(atoms)]
            if kind in ('anaphoric_actor','multihop_actor'):
                index=len(atoms)-1
                old_actor=atoms[index][0]
                if clauses[index].count(old_actor)!=1:raise ValueError('INTERFACE_REPAIR_REFERENCE_NOT_UNIQUE')
                marker=fixture['reference_markers'][1 if kind=='anaphoric_actor' else 2]
                clauses[index]=clauses[index].replace(old_actor,marker,1)
            rows.append({'id':f'fresh2:{g:03d}:{kind}','kind':kind,'group_id':g,
                         'text':binding.document(clauses),'atoms':atoms,
                         'template_ids':[(g+i)%4 for i in range(len(atoms))],
                         'scope':'in_schema' if kind in IN_SCHEMA else 'extension'})
    if len({r['text'] for r in rows})!=len(rows):raise ValueError('INTERFACE_REPAIR_NEW_HOLDOUT_TEXT_COLLISION')
    if {r['text'] for r in rows}&{r['text'] for r in old}:raise ValueError('INTERFACE_REPAIR_HOLDOUT_TEXT_OVERLAP')
    return rows


class BooleanParser:
    """Tiny exact syntax grammar. Not open-domain scope understanding."""
    def __init__(self,text:str,atoms:list[str]):
        self.s=text.strip();self.i=0;self.atoms=sorted(atoms,key=len,reverse=True)
    def skip(self):
        while self.i<len(self.s) and self.s[self.i]==' ':self.i+=1
    def token(self,token:str):
        self.skip()
        if self.s.startswith(token,self.i):
            self.i+=len(token);return True
        return False
    def parse(self):
        node=self.expr();self.skip()
        if self.i!=len(self.s):raise ValueError('INTERFACE_REPAIR_BOOLEAN_TRAILING_TEXT')
        return node
    def expr(self):
        nodes=[self.conj()]
        while self.token('or '):nodes.append(self.conj())
        if len(set(nodes))!=len(nodes):raise ValueError('INTERFACE_REPAIR_BOOLEAN_DUPLICATE_OR')
        return nodes[0] if len(nodes)==1 else ('or',)+tuple(sorted(nodes))
    def conj(self):
        nodes=[self.neg()]
        while self.token('and '):nodes.append(self.neg())
        if len(set(nodes))!=len(nodes):raise ValueError('INTERFACE_REPAIR_BOOLEAN_DUPLICATE_AND')
        return nodes[0] if len(nodes)==1 else ('and',)+tuple(sorted(nodes))
    def neg(self):
        if self.token('not '):return ('not',self.neg())
        if self.token('('):
            node=self.expr()
            if not self.token(')'):raise ValueError('INTERFACE_REPAIR_BOOLEAN_UNCLOSED')
            return node
        self.skip()
        for name in self.atoms:
            if self.s.startswith(name,self.i):
                end=self.i+len(name)
                if end==len(self.s) or self.s[end] in ' )':
                    self.i=end;return ('atom',name)
        raise ValueError('INTERFACE_REPAIR_BOOLEAN_UNRECOGNIZED_ATOM')


def condition_node(condition:str,base:dict)->tuple:
    if condition in base['conditions']:return ('atom',condition)
    try:return previous.condition_ast(condition,base['conditions'])
    except ValueError:return BooleanParser(condition,base['conditions']).parse()


def graph(atoms:list[tuple],base:dict)->tuple:
    return tuple(sorted(set((a[0],a[1],a[2],condition_node(a[3],base),a[4],a[5]) for a in atoms)))


def contradiction(atoms:list[tuple],base:dict)->bool:
    signs=defaultdict(set)
    for atom in graph(atoms,base):signs[atom[:-1]].add(atom[-1])
    return any(s=={-1,1} for s in signs.values())


def _whole_word_matches(text:str,names:list[str])->list[str]:
    return [v for v in names if re.search(r'(?<![A-Za-z])'+re.escape(v)+r'(?![A-Za-z])',text)]


def extract_polarity(text:str)->int|None:
    """Unambiguous authored duty markers, longest negative first.

    A condition containing 'not' is not an obligation denial. Any conflicting
    or missing duty marker abstains rather than supplying a guessed label.
    """
    neg=(r'\bis not required to\b',r'\bno duty exists\b',r'\bhas no obligation to\b',
         r'\bdoes not impose a duty of delivery\b',r'\bdoes not obligate\b',r'\bdoes not impose\b')
    pos=(r'\bis required to\b',r'\ba duty exists\b',r'\bis obligated to\b',
         r'\bimposes a duty of delivery\b',r'\bobligates\b')
    clean=text.lower()
    denies=any(re.search(p,clean) for p in neg)
    # Mask negative spans before looking for embedded positives.
    for p in neg:clean=re.sub(p,' ',clean)
    affirms=any(re.search(p,clean) for p in pos)
    return None if denies==affirms else (-1 if denies else 1)


def extract_asset(text:str,base:dict)->str|None:
    candidates=_whole_word_matches(text,base['assets'])
    return candidates[0] if len(candidates)==1 else None


def role_mentions(text:str,role:str)->set[str]:
    """Observed-text syntactic anchors, independent of label lists and template ID.

    A single unambiguous proper-name span is mandatory. Multi-name text with
    no role anchor is unsupported rather than inferred from entity membership.
    """
    if role=='actor':
        patterns=[rf'\b(?:actor|supplier) is ({WORD})\b',
                  rf'\b({WORD}) is (?:named as |identified as )?(?:the )?supplier\b',
                  rf'\bfrom ({WORD})\b',rf'\bby ({WORD})\b',
                  rf'\b({WORD}) (?:is (?:not )?required to|is obligated to|has no obligation to)\b']
    elif role=='recipient':
        patterns=[rf'\b(?:recipient|beneficiary|receiving party) is ({WORD})\b',
                  rf'\b({WORD}) is (?:identified as |named as )?(?:the )?(?:beneficiary|recipient|receiving party)\b',
                  rf'\bto ({WORD})\b',rf'\b[Ff]or ({WORD})\b']
    else:raise ValueError('INTERFACE_REPAIR_UNKNOWN_ROLE')
    return {m.group(1) for p in patterns for m in re.finditer(p,text)}


def observed_roles(text:str,refs:list[str],antecedent:str|None,base:dict|None=None)->tuple[dict,str|None]:
    observations={};reasons=[]
    matches=[r for r in refs if re.search(r'(?<![A-Za-z])'+re.escape(r)+r'(?![A-Za-z])',text)]
    if matches:
        if len(matches)!=1 or antecedent is None:
            reasons.append('UNRESOLVED_OR_AMBIGUOUS_REFERENCE')
        else:observations['actor']=antecedent
    for role in ('actor','recipient'):
        m=role_mentions(text,role)
        if role=='actor' and matches:
            # Anaphoric actor is expressed as a reference, never overwritten
            # by unrelated names appearing elsewhere in the clause.
            if m:reasons.append('REFERENCE_ACTOR_COLLISION')
            continue
        if len(m)>1:reasons.append(role.upper()+'_MULTIPLE_CANDIDATES')
        elif len(m)==1:observations[role]=next(iter(m))
    if base is not None:
        stop={'The','If','For','With','Outcome','After','From','Under','Once','Following','Provided','Upon','Subject','A','An','And'}
        known=set(base['actors']+base['recipients'])
        mentions=set(re.findall(r'\b'+WORD+r'\b',text))-stop-known
        bound=set(observations.values())
        if any(m not in bound for m in mentions):
            reasons.append('NOVEL_NAME_WITHOUT_ROLE_ANCHOR')
    return observations,','.join(reasons) if reasons else None


def observed_condition(text:str,base:dict)->tuple[str|None,str|None]:
    """Recognize only an explicit canonical scope string or parenthesized AST.

    Parenthesized conditions must themselves parse. Unknown scope syntax is
    rejected rather than assumed to mean one of the base conditions.
    """
    matches=[]
    prefix='the conjunction of ';suffix=' has been satisfied'
    for a in base['conditions']:
        for b in base['conditions']:
            if a==b:continue
            x=prefix+a+' and '+b+suffix
            if x in text:matches.append(x)
    # Balanced parentheses immediately after an authored if/upon/once/after
    # are supported; arbitrary other parentheses are not treated as scope.
    for m in re.finditer(r'\b(?:if|upon|once|after)\s*\(',text,re.I):
        start=text.find('(',m.start());depth=0
        for i in range(start,len(text)):
            if text[i]=='(':depth+=1
            elif text[i]==')':
                depth-=1
                if depth==0:
                    clause=text[start:i+1]
                    try:condition_node(clause,base)
                    except ValueError:return None,'INVALID_BOOLEAN_SCOPE'
                    matches.append(clause);break
        else:return None,'UNCLOSED_BOOLEAN_SCOPE'
    if len(set(matches))>1:return None,'MULTIPLE_SCOPES'
    return (matches[0],None) if matches else (None,None)


def normalize_for_model(text:str,model_base:dict,roles:dict,condition:str|None,refs:list[str]|None=None)->str:
    s=text
    if refs and roles.get("actor"):
        for marker in refs:
            s=s.replace(marker,roles["actor"])
    # Bring OOV roles into the original learned closed-class vocabulary.
    # This substitution is based solely on observed text spans, not gold.
    for role,substitute in (('actor',model_base['actors'][0]),('recipient',model_base['recipients'][0])):
        value=roles.get(role)
        if value is not None and value not in model_base[role+'s']:
            s=re.sub(r'(?<![A-Za-z])'+re.escape(value)+r'(?![A-Za-z])',substitute,s)
    if condition is not None and condition not in model_base['conditions']:
        # Preserve the exact parsed scope in overlay; only the classifier
        # sees the first known atom, which was identified in observed text.
        first=next((x for x in model_base['conditions'] if x in condition),None)
        if first is None:raise ValueError('INTERFACE_REPAIR_SCOPE_ATOM_MISSING')
        s=s.replace(condition,first,1)
    return s


def score(rows:list[dict],predictions:list[list[tuple]],base:dict)->dict:
    inc=[i for i,r in enumerate(rows) if r['scope']=='in_schema']
    group=defaultdict(list);templ=defaultdict(list)
    def record(i):
        truth=rows[i]['atoms'];pred=predictions[i]
        return int(graph(truth,base)==graph(pred,base))
    for i,row in enumerate(rows):
        group[row['kind']].append(i)
        for j,t in enumerate(row['atoms']):templ[row['template_ids'][j]].append((t,predictions[i][j]))
    def clause_stats(indices):
        pairs=[(a,p) for i in indices for a,p in zip(rows[i]['atoms'],predictions[i])]
        return {'atomic_six_field_exact':float(np.mean([a==p for a,p in pairs])),
                'by_field':{k:float(np.mean([a[j]==p[j] for a,p in pairs])) for j,k in enumerate(binding.FIELDS)}}
    inc_stats=clause_stats(inc)
    result={'in_schema':{'documents':len(inc),'graph_exact':float(np.mean([record(i) for i in inc])),**inc_stats},
            'by_challenge':{},'by_template':{},'oracle_graph_exact':1.0}
    for kind,indices in group.items():
        result['by_challenge'][kind]={'documents':len(indices),'graph_exact':float(np.mean([record(i) for i in indices])),
             'contradiction_accuracy':float(np.mean([contradiction(rows[i]['atoms'],base)==contradiction(predictions[i],base) for i in indices])),
             **clause_stats(indices)}
    for k,pairs in templ.items():
        result['by_template'][str(k)]={'clauses':len(pairs),'six_field_exact':float(np.mean([a==p for a,p in pairs])),
             'asset':float(np.mean([a[2]==p[2] for a,p in pairs])),
             'polarity':float(np.mean([a[5]==p[5] for a,p in pairs]))}
    return result


def evaluate(rows:list[dict],models:dict,encoder:Callable,base:dict,fixture:dict)->tuple[dict,dict]:
    unique,index=binding.catalog(rows)
    raw=composition.views_from_encoder(encoder,unique)['final:mean']
    cache={}
    prediction_cache={}
    def embeds(strings):
        missing=list(dict.fromkeys(s for s in strings if s not in cache))
        if missing:
            x=composition.views_from_encoder(encoder,missing)['final:mean']
            cache.update(zip(missing,x))
        return np.vstack([cache[s] for s in strings])
    heads={}
    for arm in ARMS[:3]:
        pred=previous.predict_model(models[arm],raw,base)
        heads[arm]=binding.group_predictions(pred,index)
    obs_counts=defaultdict(int)
    # First annotate all non-referential clauses, then encode deduplicated
    # normalized strings. Referential clauses are resolved in document order.
    base_clause={}
    for row in rows:
        for clause in binding.split_document(row['text']):
            if any(ref in clause for ref in fixture['reference_markers']):continue
            roles,issue=observed_roles(clause,fixture['reference_markers'],None,base)
            cond,cond_issue=observed_condition(clause,base)
            base_clause[clause]=(roles,issue,cond,cond_issue)
    to_embed=[normalize_for_model(s,base,r,c,fixture['reference_markers']) if not issue and not ci else s
              for s,(r,issue,c,ci) in base_clause.items()]
    if to_embed:
        batch=list(dict.fromkeys(to_embed))
        batch_predictions=previous.predict_model(models['targeted_grammar'],embeds(batch),base)
        prediction_cache.update(zip(batch,batch_predictions))
    def predict_one(text):
        if text not in prediction_cache:
            prediction_cache[text]=previous.predict_model(models['targeted_grammar'],embeds([text]),base)[0]
        return prediction_cache[text]
    sequences={'targeted_lexical':[], 'targeted_typed':[]}
    for row in rows:
        last=None;lex=[];typed=[]
        for clause in binding.split_document(row['text']):
            roles,issue=observed_roles(clause,fixture['reference_markers'],last,base)
            cond,cond_issue=observed_condition(clause,base)
            if issue:obs_counts['role_abstain:'+issue]+=1
            if cond_issue:obs_counts['scope_abstain:'+cond_issue]+=1
            normalized=normalize_for_model(clause,base,roles,cond,fixture['reference_markers']) if not (issue or cond_issue) else clause
            model_pred=predict_one(normalized)
            lit_asset=extract_asset(clause,base)
            lit_polarity=extract_polarity(clause)
            if lit_asset is None:obs_counts['asset_abstain']+=1
            if lit_polarity is None:obs_counts['polarity_abstain']+=1
            local=list(model_pred)
            if lit_asset is not None:local[2]=lit_asset;obs_counts['asset_overlay']+=1
            if lit_polarity is not None:local[5]=lit_polarity;obs_counts['polarity_overlay']+=1
            lex.append(tuple(local))
            if not issue:
                for k in ('actor','recipient'):
                    if k in roles:
                        local[binding.FIELDS.index(k)]=roles[k]
                        obs_counts[k+'_overlay']+=1
            if not cond_issue and cond is not None:
                local[3]=cond;obs_counts['scope_overlay']+=1
            typed.append(tuple(local))
            last=local[0] # previous PREDICTED (possibly text-grounded), never gold
        sequences['targeted_lexical'].append(lex)
        sequences['targeted_typed'].append(typed)
    for k,v in sequences.items():heads[k]=v
    return {arm:score(rows,heads[arm],base) for arm in ARMS},dict(obs_counts)


def run(args:argparse.Namespace,*,encoder:Callable|None=None)->dict:
    if args.output.exists():raise FileExistsError('OUTPUT_EXISTS_REFUSE_OVERWRITE')
    if args.batch_size<1 or args.max_length!=192 or args.ridge<=0:raise ValueError('INTERFACE_REPAIR_CONFIG_INVALID')
    base=binding.load_fixture(args.base_fixture)
    st=stress.load_fixture(args.stress_fixture,base)
    old=previous.load_fixture(args.interface_fixture,base,st)
    fixture=load_fixture(args.fixture,base,old,st)
    report=validate_prior(args.prior_report,args.base_fixture,args.stress_fixture,args.interface_fixture)
    if report['design']['original_training_examples']!=args.train_atoms:raise ValueError('INTERFACE_REPAIR_TRAIN_COUNT_MISMATCH')
    data=stress.derive_data(base,st,seed=args.seed,train_atoms=args.train_atoms,selection_groups=args.selection_groups,final_groups=args.final_groups)
    fresh=make_fresh(data,base,old,st,fixture,seed=args.seed,groups=args.groups)
    if encoder is None:
        encoder=layers.FrozenLayerEncoder(smoke.MODEL_ID,args.device,
                        allow_download=args.allow_model_download,batch_size=args.batch_size,max_length=args.max_length)
    positions=getattr(encoder,'positions',None)
    if positions is not None and (positions.get('half')!=14 or positions.get('final')!=28):
        raise ValueError('INTERFACE_REPAIR_LAYERS_CHANGED')
    train=data['train'];generic=previous.augmented_training(train,old)[len(train):]
    targeted=targeted_training(train,fixture)
    text_batches=[[x['text'] for x in rows] for rows in (train,generic,targeted)]
    x=[composition.views_from_encoder(encoder,texts)['final:mean'] for texts in text_batches]
    yy=[r['atom'] for r in train]
    models={
      'prior_augmented':previous.fit_model(np.vstack(x[:2]),yy*2,base,args.ridge),
      'count_control':previous.fit_model(np.vstack((x[0],x[1],x[0])),yy*3,base,args.ridge),
      'targeted_grammar':previous.fit_model(np.vstack(x),yy*3,base,args.ridge),
    }
    result,abstentions=evaluate(fresh,models,encoder,base,fixture)
    if getattr(encoder,'stats',None) and encoder.stats.get('texts_truncated',0):
        raise ValueError('INTERFACE_REPAIR_TRUNCATED')
    oracle={kind:1.0 for kind in CHALLENGES}
    delta={metric:result['targeted_grammar']['in_schema'][metric]-result['prior_augmented']['in_schema'][metric]
           for metric in ('graph_exact','atomic_six_field_exact')}
    p={
      'schema':SCHEMA,'status':STATUS,
      'acceptance':{'status':'DIAGNOSTIC_ONLY_NOT_SEALABLE','never_updates_model_weights':True,
                    'vocabulary_unchanged':True,'no_contract_seal':True,'no_production_mutation':True,
                    'no_final_based_model_selection':True,'ollama_called':False},
      'sources':{'prior_report_sha256':sha256(args.prior_report.read_bytes()).hexdigest(),
        'base_fixture_sha256':sha256(args.base_fixture.read_bytes()).hexdigest(),
        'stress_fixture_sha256':sha256(args.stress_fixture.read_bytes()).hexdigest(),
        'interface_fixture_sha256':sha256(args.interface_fixture.read_bytes()).hexdigest(),
        'repair_fixture_sha256':sha256(args.fixture.read_bytes()).hexdigest()},
      'design':{'model':smoke.MODEL_ID,'model_frozen':True,'extraction':'final:mean',
        'train_unique_gold_tuples':len(train),'original_examples':len(train),
        'generic_augmentation_examples':len(generic),'targeted_augmentation_examples':len(targeted),
        'count_control_examples':len(train)*3,'targeted_examples':len(train)*3,
        'prior_augmented_examples':len(train)*2,'same_gold_field_supervision':True,
        'training_templates_excluded_from_holdout':True,'previous_final_and_fresh_pairs_excluded':True,
        'new_holdout_groups':args.groups,'new_holdout_documents':len(fresh),
        'in_schema_docs':sum(r['scope']=='in_schema' for r in fresh),
        'typed_surface_is_text_grounding_not_model_generalization':True,
        'role_reference_antecedent':'immediately previous predicted actor',
        'boolean_scope':'limited AND/OR/NOT parentheses and legacy explicit conjunction',
        'fixture_delimiter':' || ','token_stats':getattr(encoder,'stats',None)},
      'new_holdout':result,'observed_text_intervention_counts':abstentions,
      'targeted_grammar_minus_prior_augmented':delta,'typed_oracle':oracle,
      'limitations':'All clauses remain single-predicate synthetic text with explicit segmentation. Grounded text extraction depends on authored lexical and syntactic anchors; no unrestricted language/entity/coreference understanding.'
    }
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with args.output.open('x',encoding='utf-8') as fh:
        json.dump(p,fh,indent=2,ensure_ascii=False,allow_nan=False);fh.write('\n')
    return p


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--device',default='cuda')
    p.add_argument('--allow-model-download',action='store_true')
    p.add_argument('--output',required=True,type=Path)
    p.add_argument('--prior-report',default=PRIOR_REPORT,type=Path)
    p.add_argument('--fixture',default=FIXTURE,type=Path)
    p.add_argument('--base-fixture',default=binding.FIXTURE,type=Path)
    p.add_argument('--stress-fixture',default=stress.FIXTURE,type=Path)
    p.add_argument('--interface-fixture',default=previous.FIXTURE,type=Path)
    p.add_argument('--train-atoms',default=216,type=int)
    p.add_argument('--selection-groups',default=16,type=int)
    p.add_argument('--final-groups',default=24,type=int)
    p.add_argument('--groups',default=24,type=int)
    p.add_argument('--seed',default=261117,type=int)
    p.add_argument('--ridge',default=.1,type=float)
    p.add_argument('--batch-size',default=4,type=int)
    p.add_argument('--max-length',default=192,type=int)
    return p


def main(argv=None):
    args=parser().parse_args(argv)
    report=run(args)
    summary={name:{'graph_exact':round(r['in_schema']['graph_exact'],6),
                   'atomic_exact':round(r['in_schema']['atomic_six_field_exact'],6),
                   'asset':round(r['in_schema']['by_field']['asset'],6),
                   'polarity':round(r['in_schema']['by_field']['polarity'],6),
                   'weakest_template_2':round(r['by_template']['2']['six_field_exact'],6),
                   'dual_oov_graph':round(r['by_challenge']['dual_oov']['graph_exact'],6),
                   'nested_boolean_graph':round(r['by_challenge']['nested_boolean']['graph_exact'],6),
                   'multihop_graph':round(r['by_challenge']['multihop_actor']['graph_exact'],6)}
             for name,r in report['new_holdout'].items()}
    print(smoke.canonical({'status':report['status'],'acceptance':report['acceptance']['status'],
        'new_holdout':summary,'report':str(args.output)}))
    return 0

if __name__=='__main__':raise SystemExit(main())
