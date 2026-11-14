#!/usr/bin/env python3
"""TRAINING/EVALUATION ONLY: T08 language-template curriculum and gold oracle.

No inference module imports this file. Holdout templates and teacher answers
are never available as inference features or through checkpoint lookup.
"""
from __future__ import annotations
import random
import re
from decimal import Decimal
from tools import nanojev_gamer_v1_hawk_dove_t08_language as language
from tools import nanojev_hawk_dove_million as curriculum

MARKER = re.compile(r'\[\[([HD])\]\]')
spans_from_prompt = language.spans_from_prompt
TRAIN_TEMPLATES = (
    'You realize you are playing Hawk–Dove. Your opponent plays Hawk [[H]] out of a million times. Your opponent plays Dove [[D]] out of the same million times. Which pure strategy gives you the greater total payoff?',
    'During one million Hawk–Dove rounds, the opponent chose Dove [[D]] times and Hawk [[H]] times. Which choice earns you the most?',
    'Hawk–Dove encounter log: the rival made [[H]] Hawk selections and [[D]] Dove selections. Maximize your total payoff.',
    'The rival favored peaceful Dove [[D]] times, versus aggressive Hawk [[H]] times, across a million interactions. Choose your strategy.',
    'Of one million turns, [[H]] involved your opponent taking the Hawk action, while [[D]] involved the Dove action. Pick the more profitable response.',
    'The opponent Dove count is [[D]] and its Hawk count is [[H]]. After a million encounters, should you always play Hawk or Dove?',
    'In the Hawk–Dove contest the opponent attacked as Hawk on [[H]] occasions and played Dove on [[D]] occasions. Find the better pure action.',
    'In a million matches, [[D]] of the enemy decisions were Dove; [[H]] were Hawk. Which of your fixed actions earns more?',
    'Observe the enemy choosing the aggressive Hawk move [[H]] times and the passive Dove move [[D]] times. Select the best pure strategy.',
    'Your opponent used Dove during [[D]] encounters and Hawk during [[H]] encounters. Which yields the larger cumulative return to you?',
    'There were [[H]] rounds of opponent Hawk and [[D]] rounds of opponent Dove, out of a million. What is your payoff-maximizing choice?',
    'Your rival chose Hawk [[H]] times against your captain, and Dove [[D]] times. Pick Hawk or Dove for all encounters.',
    'The opponent registers [[D]] Dove plays in the ledger; the remainder, [[H]], are Hawk plays. Which pure response has a larger score?',
    'Aggressive Hawk occurred [[H]] times in the opposing play; nonaggressive Dove occurred [[D]] times. Pick a profitable fixed response.',
    'In this Hawk–Dove game, the enemy made [[D]] Dove plays but [[H]] Hawk plays. Select the higher-scoring strategy.',
)
HOLDOUT_TEMPLATES = (
    'Out of the opposing million choices, [[D]] are categorized as Dove selections, whereas [[H]] are categorized as Hawk selections. Which fixed action pays better?',
    'When the opponent opts for Hawk, there are [[H]] such rounds; when it opts for Dove, there are [[D]]. Identify your higher-return pure strategy.',
    'Dove actions observed in the rival log: [[D]]. Hawk actions observed: [[H]]. Which action gives the greater cumulative payout?',
    "In the contest your adversary's Hawk frequency corresponds to [[H]] encounters. The Dove frequency corresponds to [[D]] encounters. What is your best constant choice?",
    'From a million rival moves, [[H]] were Hawk and [[D]] were Dove. Pick your action to maximize accumulated reward.',
)

assert len(TRAIN_TEMPLATES)==15 and len(HOLDOUT_TEMPLATES)==5
def _render(template,a,b,comma=False):
    if len(MARKER.findall(template))!=2 or set(MARKER.findall(template))!={'H','D'}:
        raise ValueError('HAWK_DOVE_LANGUAGE_TEMPLATE_INVALID')
    mapping={'H':format(a,',') if comma else str(a),'D':format(b,',') if comma else str(b)}
    chunks=[]; labeled=[];cur=0;length=0
    for m in MARKER.finditer(template):
        prefix=template[cur:m.start()];chunks.append(prefix);length+=len(prefix)
        val=mapping[m.group(1)];labeled.append((length,length+len(val),m.group(1),val))
        chunks.append(val);length+=len(val);cur=m.end()
    chunks.append(template[cur:])
    prompt=''.join(chunks)
    visible=spans_from_prompt(prompt)
    if [(s[0],s[1],s[3]) for s in visible] != [(s[0],s[1],s[3]) for s in labeled]:
        raise ValueError('HAWK_DOVE_LANGUAGE_GENERATOR_SPANS_MISMATCH')
    return {'prompt':prompt,'spans':labeled,'hawk_count':a,'dove_count':b,
            'first_is_hawk':int(labeled[0][2]=='H')}


def generate(*,seed=47,samples_per_template=48):
    if not 16<=samples_per_template<=1000:
        raise ValueError('HAWK_DOVE_LANGUAGE_SAMPLES_OUT_OF_RANGE')
    rng=random.Random(seed)
    rows=[];used=set()
    reserved={0,250000,500000,750000,1000000,599998,599999,600000,600001,600002}
    # Allocate a fresh count pair for every prompt; evaluation values never
    # appear as pairs in training and templates are disjoint by construction.
    for split,templates in (('train',TRAIN_TEMPLATES),('holdout',HOLDOUT_TEMPLATES)):
        for tid,template in enumerate(templates):
            for i in range(samples_per_template):
                if i<5:
                    a=([0,250000,500000,750000,1000000] if split=='train'
                       else [599998,599999,600000,600001,600002])[i]
                else:
                    a=rng.randrange(1_000_001)
                # Boundary cases recur by design across templates; all other
                # count pairs must be unique across train and holdout.
                if i>=5:
                    while a in used or a in reserved:a=rng.randrange(1_000_001)
                    used.add(a)
                row=_render(template,a,1_000_000-a,comma=(i%3==0))
                row.update(split=split,template_id=tid,case_index=i,
                           is_boundary_probe=i<5)
                rows.append(row)
    assert len(rows)==20*samples_per_template
    return rows



_decision_for_row = language._decision_for_row
def _score(model,x,rows,game_weights):
    from tools import nanojev_hawk_dove_million as curriculum
    from decimal import Decimal
    teacher={k:Decimal(v) for k,v in {'HAWK_HAWK_PAYOFF':'-1','HAWK_DOVE_PAYOFF':'3',
            'DOVE_HAWK_PAYOFF':'0','DOVE_DOVE_PAYOFF':'1.5'}.items()}
    # IMPORTANT: this reference oracle is evaluation only and isn't available
    # in query(). A separate independent test should validate score details.
    labels=[int(x['first_is_hawk']) for x in rows]
    guesses=model.predict(x)
    exact=0;decision=0;boundary=0;boundary_total=0
    by_template={}
    for idx,row in enumerate(rows):
        guessed=int(guesses[idx]);ok=guessed==labels[idx]
        if ok:exact+=1
        val=_decision_for_row(row['prompt'],guessed,game_weights)
        _,_,expected=curriculum.oracle(row['hawk_count'],row['dove_count'],teacher)
        right=val['decision']==expected
        decision+=int(right)
        if row['is_boundary_probe']:
            boundary_total+=1;boundary+=int(ok and right)
        name=str(row['template_id'])
        entry=by_template.setdefault(name,{'cases':0,'exact_roles':0,'correct_decisions':0})
        entry['cases']+=1;entry['exact_roles']+=int(ok);entry['correct_decisions']+=int(right)
    return {'cases':len(rows),'exact_roles':exact,'role_accuracy':exact/len(rows),
            'correct_decisions':decision,'decision_accuracy':decision/len(rows),
            'boundary_exact':boundary,'boundary_cases':boundary_total,
            'per_template':by_template}


