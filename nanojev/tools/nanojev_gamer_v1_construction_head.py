#!/usr/bin/env python3
"""Isolated GAMER V1 frozen-Qwen numeric-span -> canonical 2x2 construction head.

First bounded acquisition domain: symmetric two-action games with a specified
per-defection penalty. No language parser is used at inference to assign payoff
roles; an independently generated role-labelled corpus trains a token-level
classifier on frozen Qwen/LoRA activations. An exact solver computes effects.

The generated corpus is NOT a claim of coverage of all 229 registered games.
No adapter/head/serving changes outside this isolated construction checkpoint.
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path
import hashlib
import json
import random
import re

from tools import nanojev_gamer_v1_strategic_execution as solver

SCHEMA = 'nanojev.gamer.v1.frozen-qwen-slot-constructor.v1'
ROLES = ('cc', 'dc', 'cd', 'dd', 'penalty')
NUMERIC = re.compile(r'(?<![\w.])[+-]?(?:\d+/\d+|\d+(?:\.\d+)?)(?!\w)')
MARKER = re.compile(r'\[\[(cc|dc|cd|dd|penalty)\]\]')
# Train and evaluation use disjoint *language templates*. Numeric values are
# varied independently. Query labels never come from the parser or SQLite.
TRAIN_TEMPLATES = (
    'Two players can cooperate or defect. If both cooperate, each earns [[cc]]. '
    'A lone defector collects [[dc]], and the cooperator receives [[cd]]. '
    'If both defect, each gets [[dd]]. Defection carries a penalty of [[penalty]] per player. Which strategy dominates?',
    'Each side chooses cooperation or defection. Joint cooperation pays [[cc]] each. '
    'Defecting against a cooperator yields [[dc]] to the defector and [[cd]] to the cooperator. '
    'Joint defection yields [[dd]] each. Every defector pays a fine of [[penalty]]. Find the dominant strategies.',
    'Two generals may honor or betray an alliance. If both honor it, they gain [[cc]] each. '
    'A traitor gains [[dc]] when the other honors the alliance, leaving that ally [[cd]]. '
    'Two traitors gain [[dd]] each. Betrayal is penalized by [[penalty]] for the betrayer. What should each do?',
    'Suppose both rivals support one another: each wins [[cc]]. A rival who breaks the agreement '
    'while the other supports it gains [[dc]], leaving the supporter [[cd]]. '
    'If both break it, both receive [[dd]]. Breaking it incurs a charge of [[penalty]]. What action dominates?',
    'In a cooperation game the shared-cooperation reward is [[cc]] for each player. '
    'Unilateral defection rewards the defector [[dc]] while the cooperative opponent earns [[cd]]. '
    'If both players defect they earn [[dd]] apiece. The policy charges defectors [[penalty]]. Identify the best responses.',
    'Two independent actors can cooperate or defect. Mutual defection pays [[dd]] to each. '
    'Mutual cooperation pays [[cc]] to each. A unilateral cooperator receives [[cd]]; '
    'the unilateral defector receives [[dc]]. A defector faces a penalty of [[penalty]]. What strategy dominates?',
    'When both parties defect, the reward is [[dd]] each; when both cooperate it is [[cc]] each. '
    'If one party defects alone it earns [[dc]], and the cooperating party earns [[cd]]. '
    'The per-defector punishment is [[penalty]]. Decide which action dominates for each player.',
    'Each competitor decides between cooperation and defection. One defector facing a cooperator '
    'receives [[dc]], while the cooperator receives [[cd]]. If both defect they earn [[dd]] each. '
    'If both cooperate they earn [[cc]] each. There is a fee of [[penalty]] for defection. Which action is dominant?',
    'Each defection incurs a sanction of [[penalty]]. Two players have the option to cooperate or defect. '
    'They receive [[dd]] apiece if both defect. A single cooperator facing defection gets [[cd]]; '
    'the opposing defector obtains [[dc]]. If both cooperate, each receives [[cc]]. Determine dominance.',
    'The fine imposed individually on defectors is [[penalty]]. Mutual cooperation earns [[cc]] each, '
    'while mutual defection earns [[dd]] each. A unilateral defector gets [[dc]], '
    'leaving the cooperating player [[cd]]. Both players choose independently. Find their strategy.',
)
TEST_TEMPLATES = (
    'Two competing players may cooperate or defect. Mutual cooperation pays each player [[cc]]. '
    'If one defects while the other cooperates, the defector gets [[dc]] and the cooperator gets [[cd]]. '
    'Mutual defection pays each [[dd]]. Now impose a penalty of [[penalty]] on any player who defects. '
    'What is each player\'s dominant strategy before and after the penalty?',
    'For two decision makers, working together returns [[cc]] to each. '
    'One who defects against a cooperating counterpart gets [[dc]], and the counterpart receives [[cd]]. '
    'Mutual defection returns [[dd]] each. A defection tax of [[penalty]] applies to each defector. '
    'Find whether cooperation or defection dominates.',
    'Every player who defects pays [[penalty]]. When both players defect, each receives [[dd]]. '
    'A player who keeps cooperating against a defector gets [[cd]], while the defector earns [[dc]]. '
    'When both cooperate, the two receive [[cc]] each. Identify each dominant action.',
    'One defector facing a cooperator earns [[dc]]; the cooperator gets [[cd]]. '
    'Each defector additionally pays a charge of [[penalty]]. Joint cooperation yields [[cc]] each, '
    'whereas joint defection yields [[dd]] apiece. Two players independently select an action. '
    'Does either action dominate?',
    'The consequence of joint defection is [[dd]] to each actor. A defector pays [[penalty]] in fees. '
    'If the other party cooperates, the defector receives [[dc]], while the cooperator receives [[cd]]. '
    'Joint cooperation rewards each with [[cc]]. Which action is optimal regardless of the rival?',
    'A treaty fee of [[penalty]] applies individually to the party betraying the agreement. '
    'Betrayal against an honorable partner yields [[dc]] to the betrayer. '
    'Joint betrayal awards each [[dd]]. Honorable cooperation by both awards each [[cc]], '
    'but an honorable partner betrayed by the other earns [[cd]]. Which policy dominates?',
)


def sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha_file(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open('rb') as src:
        for chunk in iter(lambda: src.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def canonical(value) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def render(template: str, values: dict):
    """Generator-produced span labels. This is NEVER used on inference prompts."""
    if set(values) != set(ROLES) or sorted(MARKER.findall(template)) != sorted(ROLES):
        raise ValueError('GAMER_CONSTRUCTION_GENERATOR_ROLE_COVERAGE')
    result, spans, cursor = [], [], 0
    for match in MARKER.finditer(template):
        prefix = template[cursor:match.start()]
        result.append(prefix)
        token = str(values[match.group(1)])
        start = sum(map(len, result))
        result.append(token)
        spans.append((start, start+len(token), match.group(1), token))
        cursor = match.end()
    result.append(template[cursor:])
    prompt = ''.join(result)
    detected = [(m.start(), m.end(), m.group()) for m in NUMERIC.finditer(prompt)]
    expected = [(s, e, token) for s, e, _role, token in spans]
    if sorted(detected) != sorted(expected):
        raise ValueError('GAMER_CONSTRUCTION_NONPAYOFF_NUMBER_IN_TEMPLATE')
    return {'prompt': prompt, 'spans': sorted(spans), 'values': {k:str(v) for k,v in values.items()}}


def _number(rng: random.Random, *, penalty=False):
    # Role classes cannot be inferred from the numeric format: exact integers,
    # decimals and rational strings occur in EVERY supervised payoff role.
    if penalty:
        return str(rng.choice(('0','1','2','3','4','5','6','7','9','11',
                               '0.5','1/2','3/2','2.0000000000000001','7/3')))
    return str(rng.choice(('-11','-7','-4','-3','-2','-1','0','0','1','2','3',
                           '4','6','7','9','11','-1/2','1/2','-3/2','3/2',
                           '0.5','2.5','-0.25','2.0000000000000001')))


def generate(*, seed=47, samples_per_template=48):
    if not 4 <= samples_per_template <= 2000:
        raise ValueError('GAMER_CONSTRUCTION_SAMPLES_OUT_OF_RANGE')
    rng = random.Random(seed)
    rows=[]
    for split, templates in (('train', TRAIN_TEMPLATES),('challenge',TEST_TEMPLATES)):
        for template_id, template in enumerate(templates):
            for i in range(samples_per_template):
                values = {role:_number(rng,penalty=(role=='penalty')) for role in ROLES}
                # Deliberately expose exact zero, negative, positive and large penalties.
                if i % 8 == 0: values['penalty']='0'
                if i % 8 == 1: values['penalty']='2'
                if i % 8 == 2: values['penalty']='3'
                if split=='challenge' and i in (0,1,2):
                    values.update(cc='4',dc='6',cd='0',dd='2',penalty=str(i+1))
                row=render(template,values)
                row.update(split=split,template_id=template_id,case_index=i)
                rows.append(row)
    if len({r['prompt'] for r in rows}) != len(rows):
        raise ValueError('GAMER_CONSTRUCTION_DUPLICATE_PROMPTS')
    return rows


def _adapter_identity(run: Path):
    from tools.nanojev_gamer_v1_handoff import file_tree_sha256
    run=Path(run).resolve()
    adapter=run/'lora'/'best_adapter'
    report=run/'lora'/'report.json'
    if not adapter.is_dir():
        raise FileNotFoundError('GAMER_CONSTRUCTION_FROZEN_LORA_MISSING:'+str(adapter))
    fingerprint=file_tree_sha256(adapter)
    # Existing GAMER adapter identity is the authoritative source. Respect it
    # if report shape is present; never train/promote/modify it.
    candidates=[run/'lora'/'final_report.json',run/'lora'/'report.json',run/'handoff.json']
    for candidate in candidates:
        if candidate.is_file():
            data=json.loads(candidate.read_text(encoding='utf-8'))
            recorded=data.get('best_adapter_sha256') or data.get('adapter_sha256')
            if recorded is not None and recorded!=fingerprint:
                raise ValueError('GAMER_CONSTRUCTION_ADAPTER_FINGERPRINT_MISMATCH')
    return adapter,fingerprint


def _capture(model_id, run, device, layer, rows, *, max_length):
    """Capture causal features from frozen adapter; labels never enter encoder."""
    import numpy as np
    from tools.nanojev_gamer_v1_handoff import model_components
    adapter,adapter_sha=_adapter_identity(run)
    torch,tok,model=model_components(model_id,device,adapter_dir=adapter)
    if not getattr(tok,'is_fast',False):
        raise ValueError('GAMER_CONSTRUCTION_REQUIRES_FAST_OFFSET_TOKENIZER')
    if any(p.requires_grad for p in model.parameters()):
        raise ValueError('GAMER_CONSTRUCTION_ENCODER_NOT_FROZEN')
    model.eval()
    results=[]
    with torch.inference_mode():
        for row_index,row in enumerate(rows):
            if row_index and row_index % 40 == 0:
                print(f'GAMER CONSTRUCTION frozen capture: {row_index}/{len(rows)} prompts',flush=True)
            prompt=row['prompt']
            spans=row.get('spans') or [(m.start(),m.end(),None,m.group()) for m in NUMERIC.finditer(prompt)]
            encoded=tok(prompt,add_special_tokens=True,return_offsets_mapping=True,truncation=False)
            offsets=encoded['offset_mapping']
            ids=encoded['input_ids']
            if len(ids)>max_length:
                raise ValueError('GAMER_CONSTRUCTION_PROMPT_EXCEEDS_MAX_LENGTH')
            input_ids=torch.tensor([ids],dtype=torch.long,device=device)
            mask=torch.ones_like(input_ids)
            out=model(input_ids=input_ids,attention_mask=mask,output_hidden_states=True,
                      use_cache=False,return_dict=True)
            if not -len(out.hidden_states)<=layer<len(out.hidden_states):
                raise ValueError('GAMER_CONSTRUCTION_LAYER_OUT_OF_RANGE')
            hidden=out.hidden_states[layer][0].float()
            positions=[]
            for start,end,role,token in spans:
                aligned=[i for i,(lo,hi) in enumerate(offsets) if hi>lo and lo<end and hi>start]
                if not aligned:
                    raise ValueError('GAMER_CONSTRUCTION_UNALIGNED_NUMERIC_SPAN')
                token_idx=aligned[-1]
                positions.append(token_idx)
                # Current-token, short following context, entire prompt.
                # None of these contains a gold role or computed outcome.
                feat=torch.cat((hidden[token_idx],hidden[min(token_idx+3,len(ids)-1)],
                                hidden[len(ids)-1])).cpu().numpy()
                if not np.isfinite(feat).all():
                    raise ValueError('GAMER_CONSTRUCTION_NONFINITE_FEATURE')
                results.append((feat,role,token))
    return results,adapter_sha


def _fit(features, labels, *, epochs, seed):
    import numpy as np
    from sklearn.preprocessing import StandardScaler
    from sklearn.linear_model import SGDClassifier
    if not 1<=epochs<=1000:
        raise ValueError('GAMER_CONSTRUCTION_EPOCHS_OUT_OF_RANGE')
    x=np.asarray(features,dtype=np.float32)
    scaler=StandardScaler()
    z=scaler.fit_transform(x).astype(np.float32)
    model=SGDClassifier(loss='log_loss',alpha=0.0001,random_state=seed,shuffle=False,
                        learning_rate='optimal',average=False)
    target=np.asarray([ROLES.index(label) for label in labels],dtype=np.int64)
    for i in range(epochs):
        if i==0: model.partial_fit(z,target,classes=np.arange(len(ROLES)))
        else: model.partial_fit(z,target)
    if sorted(model.classes_.tolist())!=list(range(len(ROLES))):
        raise ValueError('GAMER_CONSTRUCTION_INCOMPLETE_LABEL_CLASSES')
    return {'mean':scaler.mean_.astype(np.float32),
            'scale':scaler.scale_.astype(np.float32),
            'coef':model.coef_.astype(np.float32),
            'intercept':model.intercept_.astype(np.float32)}


def predict(weights, feature_matrix):
    """Checkpoint-only role scores; no SQLite/labels/templates/parser."""
    import numpy as np
    x=np.asarray(feature_matrix,dtype=np.float32)
    if x.ndim!=2 or x.shape[1]!=weights['coef'].shape[1]:
        raise ValueError('GAMER_CONSTRUCTION_FEATURE_DIMENSION_MISMATCH')
    if not np.isfinite(x).all() or any(not np.isfinite(weights[k]).all() for k in ('mean','scale','coef','intercept')) or (weights['scale']<=0).any():
        raise ValueError('GAMER_CONSTRUCTION_INVALID_INFERENCE_FEATURES_OR_WEIGHTS')
    z=(x-weights['mean'])/weights['scale']
    logits=z@weights['coef'].T+weights['intercept']
    if not np.isfinite(logits).all():
        raise ValueError('GAMER_CONSTRUCTION_NONFINITE_LOGITS')
    ids=np.argmax(logits,axis=1)
    logits=logits-logits.max(axis=1,keepdims=True)
    probs=np.exp(logits);probs/=probs.sum(axis=1,keepdims=True)
    return [dict(role=ROLES[int(cls)],confidence=float(probs[i,int(cls)]))
            for i,cls in enumerate(ids)]


def bind_spans(prompt, predictions, *, min_confidence):
    """Fail closed on missing/duplicated/unbound roles. Never infer a value."""
    spans=list(NUMERIC.finditer(prompt))
    if len(spans)!=5 or len(predictions)!=len(spans):
        return {'status':'ABSTAIN_NUMERIC_SLOT_COUNT','values':None}
    if any(p['confidence']<min_confidence for p in predictions):
        return {'status':'ABSTAIN_LOW_SLOT_CONFIDENCE','values':None}
    roles=[p['role'] for p in predictions]
    if set(roles)!=set(ROLES) or len(set(roles))!=len(ROLES):
        return {'status':'ABSTAIN_MISSING_OR_DUPLICATED_ROLE','values':None}
    values={p['role']:m.group() for m,p in zip(spans,predictions)}
    # A model can assign a NEGATIVE payoff span to the PENALTY role even when
    # all five roles are unique and its confidence is high. This is a wrong
    # construction, not a broken experiment: reject it before exact execution.
    # Never silently coerce the sign, swap roles, or supply the gold value.
    try:
        parsed={role:solver._fraction(value) for role,value in values.items()}
    except ValueError:
        return {'status':'ABSTAIN_INVALID_PAYOFF_NUMBER','values':None}
    if parsed['penalty']<0:
        return {'status':'ABSTAIN_NEGATIVE_PENALTY_ASSIGNMENT','values':None}
    return {'status':'CANDIDATE_SLOTS_BOUND','values':values}


def construct_candidate(values):
    """Canonical normal-form game; exact data values from PROMPT spans only."""
    if set(values)!=set(ROLES):
        raise ValueError('GAMER_CONSTRUCTION_INCOMPLETE_VALUES')
    for value in values.values():
        solver._fraction(value)
    if solver._fraction(values['penalty'])<0:
        raise ValueError('GAMER_CONSTRUCTION_NEGATIVE_PENALTY')
    return {
        'players':['player_1','player_2'],
        'actions':[['cooperate','defect'],['cooperate','defect']],
        'payoffs':[
            [[values['cc'],values['cc']],[values['cd'],values['dc']]],
            [[values['dc'],values['cd']],[values['dd'],values['dd']]],
        ],
        'penalty':{'action':'defect','amount':values['penalty']},
    }


def training_report(*, seed, samples_per_template, rows, prediction_results, corpus_sha):
    samples=[x for x in rows if x['split']=='challenge']
    total=len(samples);correct=0;exact_spec=0;gated_exact=0;gated_count=0
    train=[r for r in rows if r['split']=='train']
    position_roles=[Counter(r['spans'][i][2] for r in train).most_common(1)[0][0]
                    for i in range(len(ROLES))]
    position_only_correct=sum(int(all(r['spans'][i][2]==position_roles[i]
                                     for i in range(len(ROLES)))) for r in samples)
    value_votes={}
    for row in train:
        for _start,_end,role,value in row['spans']:
            value_votes.setdefault(value,Counter())[role]+=1
    value_majority={value:votes.most_common(1)[0][0] for value,votes in value_votes.items()}
    role_prior=Counter(role for row in train for _s,_e,role,_v in row['spans']).most_common(1)[0][0]
    value_only_correct=sum(int(all(value_majority.get(value,role_prior)==role
                                  for _s,_e,role,value in row['spans'])) for row in samples)
    per=[]
    raw_statuses=Counter()
    gated_statuses=Counter()
    for row,preds in zip(samples,prediction_results):
        truth={role:token for _s,_e,role,token in row['spans']}
        bound=bind_spans(row['prompt'],preds,min_confidence=0.0)
        valid=bound['values'] is not None
        raw_statuses[bound['status']]+=1
        if valid and bound['values']==truth: exact_spec+=1
        gated=bind_spans(row['prompt'],preds,min_confidence=0.8)
        gated_statuses[gated['status']]+=1
        if gated['values'] is not None:
            gated_count+=1
            if gated['values']==truth: gated_exact+=1
        if valid:
            output=solver.execute(canonical(construct_candidate(bound['values'])))
            correct+=int(output['status']=='BOUNDED_EXACT_TWO_PLAYER_DECISION_COMPUTED'
                         and bound['values']==truth)
        per.append({'exact_roles_and_values':bool(valid and bound['values']==truth),
                    'status':bound['status']})
    return {
        'training_examples':sum(x['split']=='train' for x in rows),
        'challenge_examples':total,
        'challenge_distinct_unseen_language_templates':len(TEST_TEMPLATES),
        'challenge_role_exact':exact_spec,
        'challenge_role_accuracy':exact_spec/total if total else 0,
        'challenge_exact_game_construction':correct,
        'challenge_exact_game_construction_rate':correct/total if total else 0,
        'challenge_confidence_gated_accepts':gated_count,
        'challenge_confidence_gated_exact':gated_exact,
        'challenge_confidence_gated_exact_rate_of_all':gated_exact/total if total else 0,
        'challenge_confidence_gated_error_count':gated_count-gated_exact,
        'challenge_raw_candidate_status_counts':dict(sorted(raw_statuses.items())),
        'challenge_confidence_gated_status_counts':dict(sorted(gated_statuses.items())),
        'challenge_negative_penalty_assignment_rejections':raw_statuses.get(
            'ABSTAIN_NEGATIVE_PENALTY_ASSIGNMENT',0),
        'confidence_gate':0.8,
        'position_only_baseline_exact_game_rate':position_only_correct/total if total else 0,
        'position_only_baseline_exact_games':position_only_correct,
        'numeric_value_only_baseline_exact_games':value_only_correct,
        'numeric_value_only_baseline_exact_game_rate':value_only_correct/total if total else 0,
        'position_only_majority_roles_by_numeric_position':position_roles,
        'training_template_count':len(TRAIN_TEMPLATES),
        'training_overlap_with_challenge_templates':0,
        'corpus_sha256':corpus_sha,
        'no_evaluation_example_used_to_fit_head':True,
        'is_full_229_game_certification':False,
    }


def train(*, run, output, model_id='Qwen/Qwen3-0.6B',device='cuda',layer=9,
          seed=47,samples_per_template=48,epochs=20,max_length=256):
    """Train new standalone slot head; original Qwen/LoRA never modified."""
    import numpy as np
    output=Path(output)
    if output.exists():
        raise FileExistsError('GAMER_CONSTRUCTION_OUTPUT_EXISTS:'+str(output))
    rows=generate(seed=seed,samples_per_template=samples_per_template)
    corpus_sha=sha_bytes(canonical([(r['split'],r['template_id'],r['prompt'],r['spans']) for r in rows]).encode())
    print('GAMER CONSTRUCTION: capturing frozen Qwen/LoRA representations; source weights remain frozen',flush=True)
    feats,adapter_sha=_capture(model_id,run,device,layer,rows,max_length=max_length)
    if _adapter_identity(run)[1] != adapter_sha:
        raise ValueError('GAMER_CONSTRUCTION_FROZEN_ADAPTER_MUTATED_DURING_CAPTURE')
    expected=sum(len(r['spans']) for r in rows)
    if len(feats)!=expected:
        raise ValueError('GAMER_CONSTRUCTION_FEATURE_COUNT_MISMATCH')
    print(f'GAMER CONSTRUCTION: captured {len(feats)} labeled payoff spans; fitting isolated head',flush=True)
    train_rows=sum(r['split']=='train' for r in rows)
    train_count=train_rows*len(ROLES)
    weights=_fit([f for f,_role,_val in feats[:train_count]],
                 [role for _f,role,_val in feats[:train_count]],epochs=epochs,seed=seed)
    pred=predict(weights,np.stack([f for f,_role,_value in feats[train_count:]]))
    predictions=[pred[i:i+len(ROLES)] for i in range(0,len(pred),len(ROLES))]
    metric=training_report(seed=seed,samples_per_template=samples_per_template,
                            rows=rows,prediction_results=predictions,corpus_sha=corpus_sha)
    meta={'schema':SCHEMA,'model_id':model_id,'run':str(Path(run).resolve()),
          'adapter_sha256':adapter_sha,'device_at_train':device,'layer':layer,
          'max_length':max_length,'seed':seed,'samples_per_template':samples_per_template,
          'epochs':epochs,'roles':list(ROLES),'corpus_sha256':corpus_sha,
          'representation':'frozen_Qwen_LoRA_causal_numeric_span_plus_3_plus_last',
          'activation_on_train_prompts_only':True,
          'base_model_weights_cryptographically_pinned':False,
          'no_lora_training':True,
          'no_c0_or_wide_training':True,'no_production_authority_change':True}
    output.mkdir(parents=True,exist_ok=False)
    checkpoint=output/'constructor_head.npz'
    np.savez_compressed(checkpoint,**weights,meta_json=canonical(meta))
    receipt=dict(meta,metrics=metric,status='COMPLETE_ISOLATED_CONSTRUCTION_HEAD_TRAIN',
                 checkpoint_sha256=sha_file(checkpoint),
                 checkpoint=str(checkpoint.resolve()),
                 promotion='BLOCKED_DIAGNOSTIC_ONLY',
                 limitations=('Bounded symmetric 2x2 penalty game construction, not all registered games; '
                              'confidence alone is not semantic validation. Unfamiliar rules may be misread. '
                              'Frozen Qwen/LoRA is used as a representation source, not trained. '
                              'Base model ID is recorded but underlying weights are not fully SHA256 pinned.'))
    (output/'final_report.json').write_text(json.dumps(receipt,sort_keys=True,indent=2)+'\n',encoding='utf-8')
    return receipt


def query(*,run,output,prompt,device='cuda',min_confidence=0.80):
    """Blind learned construction. Never reads generator truth or source oracle."""
    import numpy as np
    output=Path(output)
    checkpoint=output/'constructor_head.npz'
    receipt=json.loads((output/'final_report.json').read_text(encoding='utf-8'))
    if receipt.get('schema')!=SCHEMA or receipt.get('checkpoint_sha256')!=sha_file(checkpoint):
        raise ValueError('GAMER_CONSTRUCTION_CHECKPOINT_LINEAGE_MISMATCH')
    with np.load(checkpoint,allow_pickle=False) as bundle:
        meta=json.loads(str(bundle['meta_json']))
        weights={key:bundle[key].copy() for key in ('mean','scale','coef','intercept')}
    if meta.get('schema')!=SCHEMA or any(meta.get(k)!=receipt.get(k) for k in
                ('adapter_sha256','model_id','layer','corpus_sha256','run')):
        raise ValueError('GAMER_CONSTRUCTION_METADATA_MISMATCH')
    if Path(run).resolve()!=Path(meta['run']):
        raise ValueError('GAMER_CONSTRUCTION_FROZEN_ADAPTER_RUN_MISMATCH')
    if not 0<=min_confidence<=1:
        raise ValueError('GAMER_CONSTRUCTION_CONFIDENCE_OUT_OF_RANGE')
    if not isinstance(prompt,str) or not 1<=len(prompt)<=16384:
        raise ValueError('GAMER_CONSTRUCTION_BAD_PROMPT')
    spans=list(NUMERIC.finditer(prompt))
    if len(spans)!=5:
        return {'schema':SCHEMA,'status':'ABSTAIN_NUMERIC_SLOT_COUNT',
                'strategic_decision':None,'model_checkpoint_used_for_construction':False,
                'production_authority_changed':False,'detected_numeric_spans':len(spans)}
    feats,adapter_sha=_capture(meta['model_id'],run,device,meta['layer'],
                 [{'prompt':prompt}],max_length=meta['max_length'])
    if adapter_sha!=meta['adapter_sha256'] or _adapter_identity(run)[1]!=adapter_sha:
        raise ValueError('GAMER_CONSTRUCTION_FROZEN_ADAPTER_CHANGED')
    predictions=predict(weights,[f for f,_role,_val in feats])
    bindings=bind_spans(prompt,predictions,min_confidence=min_confidence)
    report={'schema':SCHEMA,'status':bindings['status'],
            'prediction_by_numeric_span':[
                {'numeric_span':m.group(),'position':[m.start(),m.end()],**pred}
                for m,pred in zip(spans,predictions)],
            'checkpoint_sha256':receipt['checkpoint_sha256'],
            'adapter_sha256':adapter_sha,
            'model_checkpoint_used_for_construction':True,
            'construction_is_neural':True,
            'production_authority_changed':False,
            'strategic_decision':None,
            'claim_of_certified_strategic_generalization':False,
            'limitations':'A candidate learned construction is not proof that altered or unseen game rules were understood.',
    }
    if bindings['values'] is None:
        return report
    spec=construct_candidate(bindings['values'])
    result=solver.execute(canonical(spec))
    report.update(status='CANDIDATE_LEARNED_GAME_CONSTRUCTED_EXACTLY_SOLVED',
                  constructed_game=spec,strategic_execution=result,
                  strategic_decision=result['strategic_decision'],
                  numerical_backend=result['exact_numerical_backend'],
                  original_english_parser_used=False,
                  game_source='trained_slot_head_plus_exact_prompt_numerals',
                  decision_certification='NOT_CERTIFIED_WITHOUT_INDEPENDENT_GAME_ORACLE')
    return report
