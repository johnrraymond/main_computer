#!/usr/bin/env python3
"""TRAINING-ONLY synthetic numeric-game supervision for backbone-first GAMER.

Nothing in this file is imported by query or execution. This generates full
payoff descriptions with renamed strategies, shifted ordering, varying matrix
sizes, and distinct holdout phrasing. It makes no generalization claims.
"""
from __future__ import annotations
import argparse
import json
import random
import re
from pathlib import Path
from tools.nanojev_gamer_v1_backbone_first import canonical,validate_state

# Authoring-only markup: removed completely before prompts are written. Role
# supervision is supplied with precise character offsets; inference never sees
# these markers and never imports the corpus generator.
_ROLE_OPEN = '\ue000'
_ROLE_MID = '\ue001'
_ROLE_END = '\ue002'
_ROLE_RE = re.compile(re.escape(_ROLE_OPEN) + r'([PO])' + re.escape(_ROLE_MID)
                      + r'(.*?)' + re.escape(_ROLE_END), re.DOTALL)


def _mark(role, name):
    if role not in ('P', 'O') or any(x in name for x in (_ROLE_OPEN,_ROLE_MID,_ROLE_END)):
        raise ValueError('GAMER_CORPUS_INVALID_ROLE_MARK')
    return _ROLE_OPEN + role + _ROLE_MID + name + _ROLE_END


def _unmark(marked):
    parts=[]; spans=[]; cursor=0; length=0
    for match in _ROLE_RE.finditer(marked):
        prefix=marked[cursor:match.start()]
        parts.append(prefix);length+=len(prefix)
        name=match.group(2)
        if not name:raise ValueError('GAMER_CORPUS_EMPTY_ROLE_SPAN')
        role='player' if match.group(1)=='P' else 'opponent'
        spans.append({'role':role,'name':name,'start':length,'end':length+len(name)})
        parts.append(name);length+=len(name)
        cursor=match.end()
    parts.append(marked[cursor:])
    prompt=''.join(parts)
    if any(x in prompt for x in (_ROLE_OPEN,_ROLE_MID,_ROLE_END)):
        raise ValueError('GAMER_CORPUS_UNMATCHED_ROLE_MARK')
    return prompt,spans


TRAIN_NAMES=('Hawk','Dove','Attack','Yield','Red','Blue','Amber','Green',
             'Move A','Move B','Move C','Move D','Advance','Retreat')
TEST_NAMES=('Quartz','Jade','Silver','Copper','North','South','East','West',
            'Sigma','Tau','Omega','Delta','Option P','Option Q')
TEMPLATES_TRAIN=(
    'You are playing a one-turn game. Your available actions are {actions}. '
    'The opponent may choose {opponents}. Over {total} encounters, their action counts were {counts}. '
    'The payoffs to you are: {payoffs}. Which action maximizes your total payoff?',
    'Determine the best pure move in this situation. Your possible moves: {actions}. '
    'Rival moves: {opponents}. Their observed frequencies: {counts} (total {total}). '
    'Your reward for each pairing: {payoffs}. Maximize your cumulative reward.',
    'A contestant must commit to one action from {actions}. Another contestant acts from {opponents}. '
    'Action totals for the other contestant: {counts}. The payoff rules are {payoffs}. '
    'Choose the action with the largest aggregate result over all {total} rounds.',
    'For a decision between {actions}, consider these rival options: {opponents}. '
    'The other side has chosen them in these amounts: {counts}. '
    'The full rewards table for your decisions reads: {payoffs}. '
    'Which option gets you the greatest sum over {total} trials?',
)
TEMPLATES_TEST=(
    'There are {total} contests. Choose a single policy from {actions}. '
    'The other player has alternatives {opponents}, with recorded plays {counts}. '
    'Your results for every joint choice are {payoffs}. Find the largest accumulated value.',
    'Compute the action that pays most across the observed encounters. '
    'You may commit to {actions}; your counterpart selects from {opponents}. '
    'Counts of their selections: {counts}. Your earnings per matchup: {payoffs}. Total encounters: {total}.',
    'The objective is maximal overall earnings, not matching the most common rival move. '
    'Pick among {actions}. The other participant can pick {opponents}. '
    'The opponent record is {counts}; rewards are {payoffs}. '
    'There are {total} encounters in all.',
    'With {actions} as your choices and {opponents} as your rival\'s choices, '
    'the observed rival counts were {counts}. The numbers earned for each pairing: {payoffs}. '
    'Across {total} meetings, what is your best fixed action?',
    'Here is a payoff table in prose: {payoffs}. '
    'You have actions {actions}; the opposing agent has {opponents}. '
    'They chose these actions {counts} for a total of {total}. '
    'Select the strategy with maximum cumulative payoff.',
)


def ref(key):return {'ref':key}


def mul(x,y):return {'op':'mul','args':[ref(x),ref(y)]}


def add(xs):return {'op':'add','args':xs}


def example(rng,split,index):
    names=TEST_NAMES if split=='test' else TRAIN_NAMES
    # All splits have 2-4 actions; novelty is in names, phrasing and numeric
    # combinations. Holdout semantics are independently generated.
    nself=rng.choice((2,2,3,4))
    nopp=rng.choice((2,2,3))
    pool=list(names);rng.shuffle(pool)
    self_actions=pool[:nself]
    opp_actions=pool[nself:nself+nopp]
    if len(opp_actions)!=nopp:raise AssertionError('CORPUS_NAMES_TOO_FEW')
    counts=[rng.randint(1,200) for _ in range(nopp)]
    payoffs=[[rng.randint(-9,13) for _ in range(nopp)] for _ in range(nself)]
    # Reverse clause orders to prevent the first-mention shortcut.
    self_order=list(range(nself));opp_order=list(range(nopp))
    rng.shuffle(self_order);rng.shuffle(opp_order)
    counts_text=', '.join(f'{_mark("O",opp_actions[j])}: {counts[j]}' for j in opp_order)
    payoff_text='; '.join(
      f'if you choose {_mark("P",self_actions[i])} and the rival chooses {_mark("O",opp_actions[j])}, you receive {payoffs[i][j]}'
      for i,j in [(i,j) for i in self_order for j in opp_order])
    facts={f'n{j}':str(counts[j]) for j in range(nopp)}
    for i in range(nself):
        for j in range(nopp):facts[f'p{i}_{j}']=str(payoffs[i][j])
    actions=[{'name':self_actions[i],
              'score':add([mul(f'n{j}',f'p{i}_{j}') for j in range(nopp)])}
             for i in range(nself)]
    state={'status':'ok','objective':'maximize','facts':facts,'actions':actions}
    validate_state(state)
    template=rng.choice(TEMPLATES_TEST if split=='test' else TEMPLATES_TRAIN)
    marked=template.format(actions=', '.join(_mark('P',self_actions[i]) for i in self_order),
             opponents=', '.join(_mark('O',opp_actions[j]) for j in opp_order),
             total=sum(counts),counts=counts_text,payoffs=payoff_text)
    prompt,role_spans=_unmark(marked)
    return {'split':split,'prompt':prompt,'state':state,'role_spans':role_spans,
            'source':'SYNTHETIC_SUPERVISED_GAME_RULES',
            'family':'numeric-action-selection','index':index}


# This fixed payoff system exists ONLY in the training teacher. Neither query
# nor the generic arithmetic engine imports it. The objective is learned rule
# recall on later prompts that name the acquired game but omit its payoffs.
T08_TRAIN=(
    'You realize you are playing Hawk–Dove. Your opponent plays Hawk {h} times and Dove {d} times out of a million. Which pure strategy gives the greater total payoff?',
    'In a million Hawk–Dove meetings, the other player selected Dove {d} times and Hawk {h} times. Which of your two actions has the largest aggregate reward?',
    'A Hawk–Dove opponent chose Hawk in {h} encounters; its remaining {d} encounters were Dove. Find your higher-payoff fixed action.',
    "Choose the best pure Hawk–Dove strategy. The rival's million decisions consist of {h} Hawks and {d} Doves.",
    'For the familiar Hawk–Dove game, the other side made {d} Dove moves and {h} Hawk moves across one million games. Which action maximizes your cumulative payoff?',
)
T08_TEST=(
    'Imagine one million encounters governed by Hawk–Dove. The rival adopted Dove for {d} of them, and Hawk for {h}. Which action maximizes the total you earn?',
    'In the Hawk–Dove payoff game, the recorded opposition choices are: Dove = {d}; Hawk = {h}. Select the fixed strategy with greater aggregate gain.',
    'Across a million trials of Hawk–Dove, {h} saw the other player behaving as Hawk; the other {d} were Dove. Which of your strategies earns more?',
    "The opponent's action history for Hawk–Dove is {d} for Dove and {h} for Hawk. Commit to the better strategy under this frequency distribution.",
)


def t08_example(rng,split,index):
    # Counts and word order are varied, but no inference code receives these
    # labels or the payoffs. Payoff coefficients are supervised target facts.
    h=rng.randint(1,999999)
    d=1000000-h
    t=rng.choice(T08_TEST if split=='test' else T08_TRAIN)
    # The title mentions player strategies; the rival-count descriptions
    # mention opponent actions. These are distinct ROLE annotations even when
    # the surface name is the same. Markup never reaches the model.
    title='Hawk–Dove'
    annotated=[];cursor=0
    for match in re.finditer(r'Hawk–Dove|Hawks|Doves|Hawk|Dove',t):
        annotated.append(t[cursor:match.start()])
        word=match.group()
        if word==title:
            annotated.append(_mark('P','Hawk')+'–'+_mark('P','Dove'))
        else:
            annotated.append(_mark('O',word))
        cursor=match.end()
    annotated.append(t[cursor:])
    prompt,role_spans=_unmark(''.join(annotated).format(h=h,d=d))
    facts={'hawk_encounters':str(h),'dove_encounters':str(d),
           'hawk_vs_hawk':'-1','hawk_vs_dove':'3',
           'dove_vs_hawk':'0','dove_vs_dove':'3/2'}
    actions=[
       {'name':'Hawk','score':add([mul('hawk_encounters','hawk_vs_hawk'),
                                  mul('dove_encounters','hawk_vs_dove')])},
       {'name':'Dove','score':add([mul('hawk_encounters','dove_vs_hawk'),
                                  mul('dove_encounters','dove_vs_dove')])},
    ]
    state={'status':'ok','objective':'maximize','facts':facts,'actions':actions}
    validate_state(state)
    return {'split':split,'prompt':prompt,'state':state,'role_spans':role_spans,
            'source':'TRAINING_ONLY_ACQUIRED_GAME_RULES',
            'family':'t08-implicit-rules','index':index}


def generate(output,*,train=1000,dev=100,test=100,
             t08_train=300,t08_dev=40,t08_test=40,seed=47):
    output=Path(output)
    if output.exists():raise FileExistsError('GAMER_CORPUS_IMMUTABLE_OUTPUT_EXISTS')
    if min(train,dev,test)<1 or min(t08_train,t08_dev,t08_test)<0:
        raise ValueError('GAMER_CORPUS_SPLIT_EMPTY_OR_NEGATIVE')
    rng=random.Random(seed)
    seen=set();rows=[]
    for split,n,m in [('train',train,t08_train),('dev',dev,t08_dev),('test',test,t08_test)]:
        for generator,count in ((example,n),(t08_example,m)):
            for index in range(count):
                for attempt in range(100):
                    row=generator(rng,split,index)
                    if row['prompt'] not in seen:break
                else:raise RuntimeError('GAMER_CORPUS_COLLISION_LIMIT')
                seen.add(row['prompt']);rows.append(row)
    output.parent.mkdir(parents=True,exist_ok=True)
    with output.open('x',encoding='utf-8') as file:
        for row in rows:file.write(canonical(row)+'\n')
    return {'path':str(output),'train':train+t08_train,
            'dev':dev+t08_dev,'test':test+t08_test,'seed':seed,
            'unseen_test_templates':len(TEMPLATES_TEST),
            't08_unseen_test_templates':len(T08_TEST),
            'test_names_disjoint_from_train':not bool(set(TEST_NAMES)&set(TRAIN_NAMES)),
            't08_payoffs_supplied_only_as_supervised_target':True}


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--train',type=int,default=1000)
    p.add_argument('--dev',type=int,default=100)
    p.add_argument('--test',type=int,default=100)
    p.add_argument('--t08-train',type=int,default=300)
    p.add_argument('--t08-dev',type=int,default=40)
    p.add_argument('--t08-test',type=int,default=40)
    p.add_argument('--seed',type=int,default=47)
    args=p.parse_args(argv)
    print(json.dumps(generate(args.output,train=args.train,dev=args.dev,test=args.test,
                    t08_train=args.t08_train,t08_dev=args.t08_dev,
                    t08_test=args.t08_test,seed=args.seed),indent=2))

if __name__=='__main__':main()
