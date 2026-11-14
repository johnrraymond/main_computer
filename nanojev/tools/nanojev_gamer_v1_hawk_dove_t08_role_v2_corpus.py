"""Training-only augmentation of the existing 15/5 Hawk–Dove T08 corpus.

The original fifteen training templates and five holdout templates are reused
unchanged. Each original training template is paired with an independently
written reversal of the *whole role/count clause order*. No labels or template
strings enter the inference module or checkpoint features.
"""
from __future__ import annotations

# Same 15 semantic framings with reversed presentation order, not renamed roles.
REORDERED_TRAIN = (
    'You realize you are playing Hawk–Dove. Your opponent plays Dove [[D]] out of a million times. Your opponent plays Hawk [[H]] out of the same million times. Which pure strategy gives you the greater total payoff?',
    'During one million Hawk–Dove rounds, the opponent chose Hawk [[H]] times and Dove [[D]] times. Which choice earns you the most?',
    'Hawk–Dove encounter log: the rival made [[D]] Dove selections and [[H]] Hawk selections. Maximize your total payoff.',
    'The rival favored aggressive Hawk [[H]] times, versus peaceful Dove [[D]] times, across a million interactions. Choose your strategy.',
    'Of one million turns, [[D]] involved your opponent taking the Dove action, while [[H]] involved the Hawk action. Pick the more profitable response.',
    'The opponent Hawk count is [[H]] and its Dove count is [[D]]. After a million encounters, should you always play Hawk or Dove?',
    'In the Hawk–Dove contest the opponent played Dove on [[D]] occasions and attacked as Hawk on [[H]] occasions. Find the better pure action.',
    'In a million matches, [[H]] of the enemy decisions were Hawk; [[D]] were Dove. Which of your fixed actions earns more?',
    'Observe the enemy choosing the passive Dove move [[D]] times and the aggressive Hawk move [[H]] times. Select the best pure strategy.',
    'Your opponent used Hawk during [[H]] encounters and Dove during [[D]] encounters. Which yields the larger cumulative return to you?',
    'There were [[D]] rounds of opponent Dove and [[H]] rounds of opponent Hawk, out of a million. What is your payoff-maximizing choice?',
    'Your rival chose Dove [[D]] times against your captain, and Hawk [[H]] times. Pick Hawk or Dove for all encounters.',
    'The opponent registers [[H]] Hawk plays in the ledger; the remainder, [[D]], are Dove plays. Which pure response has a larger score?',
    'Nonaggressive Dove occurred [[D]] times in the opposing play; aggressive Hawk occurred [[H]] times. Pick a profitable fixed response.',
    'In this Hawk–Dove game, the enemy made [[H]] Hawk plays but [[D]] Dove plays. Select the higher-scoring strategy.',
)


def curriculum(seed=47,samples_per_template=48):
    from tools import nanojev_gamer_v1_hawk_dove_t08_corpus as old
    from tools import nanojev_gamer_v1_hawk_dove_t08_role_v2 as new
    if samples_per_template < 16 or samples_per_template % 2:
        raise ValueError('T08_V2_SAMPLES_PER_TEMPLATE_REQUIRES_EVEN_AT_LEAST_16')
    if len(old.TRAIN_TEMPLATES)!=15 or len(old.HOLDOUT_TEMPLATES)!=5:
        raise ValueError('T08_V2_SOURCE_TEMPLATE_COUNT_CHANGED')
    if len(REORDERED_TRAIN)!=15:
        raise ValueError('T08_V2_REORDERED_TEMPLATE_COUNT_CHANGED')
    rows=old.generate(seed=seed,samples_per_template=samples_per_template)
    train=[];holdout=[]
    for row in rows:
        if row['split']=='holdout':
            holdout.append(row)
            continue
        if row['case_index']>=samples_per_template//2:
            continue
        idx=row['template_id']
        reversed_row=old._render(REORDERED_TRAIN[idx],row['hawk_count'],row['dove_count'],
                                 comma=(row['case_index']%3==0))
        for r,variant in ((row,'original'),(reversed_row,'reordered')):
            record=dict(r)
            record.update(split='train',template_id=idx,case_index=row['case_index'],
                          variant=variant,is_boundary_probe=bool(row['is_boundary_probe']))
            train.append(record)
    if len(train)!=15*samples_per_template or len(holdout)!=5*samples_per_template:
        raise ValueError('T08_V2_CORPUS_WRONG_SIZE')
    if {r['hawk_count'] for r in train}&{r['hawk_count'] for r in holdout}:
        raise ValueError('T08_V2_TRAIN_HOLDOUT_COUNT_LEAK')
    if {r['first_is_hawk'] for r in train}!={0,1}:
        raise ValueError('T08_V2_TRAIN_CLASS_COLLAPSE')
    for idx in range(15):
        rr=[r for r in train if r['template_id']==idx]
        if sum(r['first_is_hawk'] for r in rr)!=samples_per_template//2:
            raise ValueError(f'T08_V2_TEMPLATE_{idx}_ORDER_NOT_BALANCED')
    # Validate that *all* gold role labels agree with adjacent role-bearing
    # source markers. No role detection from text enters inference.
    for row in train+holdout:
        parsed=new.generic_number_spans(row['prompt'])
        if len(parsed)!=2 or [x[3] for x in parsed]!=[x[3] for x in row['spans']]:
            raise ValueError('T08_V2_NUMERIC_SPAN_GENERATION_MISMATCH')
    return train,holdout
