"""Training-only, author-annotated graph examples. No inference import.

This generates two different generic quantitative decision families using the
same graph contract. Templates/teacher answers MUST NOT be imported by inference.
Additional games must be added as DATA, not new inference code.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import random
from pathlib import Path
from tools.nanojev_gamer_v1_graph_contract import SCHEMA, validate_graph, execute_graph

TRAIN_WORDS = ('Hawk','Dove','North','South','Amber','Violet','Quartz','Juniper',
               'River','Stone','Advance','Yield','Copper','Frost','Move A','Move B',
               'Silver','Retreat','Choice K','Choice L','Pilot','Broker','Scribe','Scout')
CHALLENGE_WORDS = ('Horizon','Willow','Banyan','Aurora','Kestrel','Ember','Crane',
                   'Choice R','Choice S','Aster','Cobalt','Valley','Tundra','Topaz',
                   'Flint','Talon','Nomad','Mistral','Saffron','Lagoon','Basalt','Alder')
DEV_WORDS = ('Piper','Zephyr','Lattice','Mosaic','Cairn','Beacon',
             'Birch','Quartzite','Celadon','Oriole','Ridge','Junia','Sable',
             'Clover','Rainfall','Ibis','Canopy','Trellis','Glacier','Tangent',
             'Meridian','Arbor','Prism','Hearth','Sierra','Lark','Monarch')
TEST_WORDS = ('Sigma','Tau','Orchid','Aspen','Westward','Azure','Axiom','Pylon',
              'Option X','Option Y','Option Z','Vega','Nimbus','Omega','Helix','Slate',
              'Cadence','Harbor','Delta','Cedar','Union','Citrine','Keystone','Lumen')
# Corpus-only diversity: train on many unfamiliar surface forms, never
# use the inventory in the model, decoder, solver, or inference code.
# The split-specific generators have independent seeds and checked disjointness.
SYLLABLES = ('al','ber','cen','dra','eln','fir','gol','han','ian','jor',
             'kel','lun','mar','nel','ori','pal','quin','rel','sol','tar',
             'ulm','ven','wel','xan','yor','zen')


def _synthetic_pool(seed, count):
    rng = random.Random(seed)
    result = set()
    while len(result) < count:
        components = [rng.choice(SYLLABLES) for _ in range(rng.randint(3,4))]
        style = rng.randrange(4)
        if style == 0:
            name = ''.join(components).capitalize()
        elif style == 1:
            name = (''.join(components[:2]) + ' ' + ''.join(components[2:])).title()
        elif style == 2:
            name = (''.join(components[:2]) + '-' + ''.join(components[2:])).title()
        else:
            name = (''.join(components) + ' ' + str(rng.randint(10,999))).capitalize()
        if name.strip() and len(name) < 24:
            result.add(name)
    return tuple(sorted(result))


NAME_POOLS = {
    'train': tuple(TRAIN_WORDS) + _synthetic_pool(151, 180),
    'dev': tuple(DEV_WORDS) + _synthetic_pool(163, 80),
    'challenge': tuple(CHALLENGE_WORDS) + _synthetic_pool(173, 80),
    'test': tuple(TEST_WORDS) + _synthetic_pool(181, 80),
}
if any(set(NAME_POOLS[a]) & set(NAME_POOLS[b])
       for a in NAME_POOLS for b in NAME_POOLS if a < b):
    raise RuntimeError('GAMER_GRAPH_NAME_SPLIT_LEAKAGE')

ACTORS_TRAIN = ('planner','contender','participant','trader','coordinator','rival','other party')
ACTORS_DEV = ('arbiter','custodian','representative','delegate','supervisor','steward','navigator')
ACTORS_CHALLENGE = ('inspector','negotiator','facilitator','cooperator','mediator','director','observer')
ACTORS_TEST = ('agent','decision maker','operator','challenger','counterpart','partner','competitor')


class Author:
    def __init__(self):
        self.text = ''
        self.nodes = []
        self.edges = []
        self._counter = 0

    def write(self, text):
        self.text += str(text)

    def mention(self, kind, text, value=None):
        ident = f'n{self._counter}'; self._counter += 1
        start = len(self.text)
        self.write(text)
        node = {'id':ident,'kind':kind,'span':[start,len(self.text)]}
        if value is not None: node['value'] = str(value)
        self.nodes.append(node)
        return ident

    def latent(self, operator):
        ident = f'n{self._counter}'; self._counter += 1
        self.nodes.append({'id':ident,'kind':'operation','operator':operator})
        return ident

    def edge(self, source, target, kind, pos=None):
        e = {'src':source,'dst':target,'type':kind}
        if pos is not None: e['position'] = pos
        self.edges.append(e)

    def calculation(self, operator, operands):
        ident = self.latent(operator)
        for position, target in enumerate(operands):
            self.edge(ident,target,'operand',position)
        return ident

    def pack(self, objective, choices, family, split, index, template):
        graph={'schema':SCHEMA,'nodes':self.nodes,'edges':self.edges,
               'objective':{'kind':objective,'candidates':choices}}
        validate_graph(graph,complete=True)
        answer=execute_graph(graph)
        # Explicit labels are only in dataset rows, never in the Qwen input.
        object_spans=[{'kind':n['kind'],'start':n['span'][0], 'end':n['span'][1]}
                      for n in self.nodes if 'span' in n]
        return {'prompt':self.text, 'graph':graph,'object_spans':object_spans,
                'expected_decisions':answer['decisions'], 'split':split,
                'family':family,'index':index,'template':template,
                'annotation_provenance':'AUTHOR_MARKED_IN_SOURCE_GENERATOR'}


def take_names(rng,split,count):
    return rng.sample(NAME_POOLS[split],count)


def encounter(rng,split,index, *, actor_override=None, rival_override=None,
              player_override=None, opponent_override=None):
    a=Author()
    n_player=len(player_override) if player_override is not None else rng.choice((2,3))
    n_opponent=len(opponent_override) if opponent_override is not None else rng.choice((2,3))
    if (player_override is None) != (opponent_override is None):
        raise ValueError('GAMER_GRAPH_ENCOUNTER_OVERRIDES_MUST_BE_PAIRED')
    if player_override is None:
        pool=take_names(rng,split,n_player+n_opponent)
        player,opponent=pool[:n_player],pool[n_player:]
    else:
        player,opponent=list(player_override),list(opponent_override)
        if len(set(player+opponent))!=len(player+opponent):
            raise ValueError('GAMER_GRAPH_ENCOUNTER_ACTION_NAMES_NOT_UNIQUE')
    actor_pool = {'train':ACTORS_TRAIN,'dev':ACTORS_DEV,
                  'challenge':ACTORS_CHALLENGE,'test':ACTORS_TEST}[split]
    who=actor_override if actor_override is not None else rng.choice(actor_pool)
    rival=(rival_override if rival_override is not None else
           rng.choice(tuple(x for x in actor_pool if x!=who)))
    if who==rival or who in player or who in opponent or rival in player or rival in opponent:
        raise ValueError('GAMER_GRAPH_ENCOUNTER_ACTOR_ACTION_COLLISION')
    counts=[rng.randint(2,50) for _ in opponent]
    payoff=[[rng.randint(-9,13) for _ in opponent] for _ in player]
    template= index % (3 if split=='test' else 4)
    if template==0:
        a.write('A ');owner=a.mention('actor',who)
        a.write(' chooses from ')
    elif template==1:
        a.write('Consider the choices available to the ');owner=a.mention('actor',who)
        a.write(': ')
    elif template==2:
        a.write('For the ');owner=a.mention('actor',who)
        a.write(', available moves are ')
    else:
        a.write('Available choices for the ');owner=a.mention('actor',who)
        a.write(' are ')
    player_ids={}
    for i,name in enumerate(player):
        if i:a.write(', ')
        pid=a.mention('action',name);player_ids[name]=pid
        a.edge(owner,pid,'owns')
    a.write('. The ');other=a.mention('actor',rival)
    a.write(' may respond with ')
    opp_ids={}
    for i,name in enumerate(opponent):
        if i:a.write(', ')
        oid=a.mention('action',name);opp_ids[name]=oid
        a.edge(other,oid,'owns')
    a.write('. Observed response counts: ')
    count_nodes={}
    for j,name in enumerate(opponent):
        if j:a.write('; ')
        ref=a.mention('action',name);a.edge(ref,opp_ids[name],'refers_to')
        a.write(' = ');number=a.mention('quantity',str(counts[j]),counts[j]);count_nodes[name]=number
        a.edge(ref,number,'measures')
    a.write('. Consequences per pairing: ')
    payoff_nodes={}
    for i,p in enumerate(player):
        for j,o in enumerate(opponent):
            if i or j:a.write('; ')
            a.write('choosing ')
            x=a.mention('action',p);a.edge(x,player_ids[p],'refers_to')
            a.write(' against ')
            y=a.mention('action',o);a.edge(y,opp_ids[o],'refers_to')
            a.write(' ');event=a.mention('outcome','yields');a.write(' ')
            z=a.mention('quantity',str(payoff[i][j]),payoff[i][j]);payoff_nodes[p,o]=z
            a.edge(z,x,'applies_to');a.edge(z,y,'applies_to')
            a.edge(x,event,'causes');a.edge(y,event,'depends_on')
            a.edge(z,event,'measures')
    a.write('. Select a single choice giving the greatest sum over encounters.')
    for p in player:
        products=[a.calculation('mul',[count_nodes[o],payoff_nodes[p,o]]) for o in opponent]
        score=a.calculation('add',products)
        a.edge(player_ids[p],score,'score')
    return a.pack('maximize',list(player_ids.values()),'encounter',split,index,template)


def allocation(rng,split,index, *, actor_override=None, names_override=None):
    a=Author()
    count=len(names_override) if names_override is not None else rng.choice((2,3,4))
    names=list(names_override) if names_override is not None else take_names(rng,split,count)
    actor=(actor_override if actor_override is not None else rng.choice(
        {'train':ACTORS_TRAIN,'dev':ACTORS_DEV,
         'challenge':ACTORS_CHALLENGE,'test':ACTORS_TEST}[split]))
    template=index % (3 if split=='test' else 4)
    if template==0:
        a.write('The '); owner=a.mention('actor',actor);a.write(' compares plans: ')
    elif template==1:
        a.write('For a ');owner=a.mention('actor',actor);a.write(', the available projects are ')
    elif template==2:
        a.write('A ');owner=a.mention('actor',actor);a.write(' has alternatives ')
    else:
        a.write('Project selection for the ');owner=a.mention('actor',actor);a.write(': ')
    ids={}
    for i,name in enumerate(names):
        if i:a.write(', ')
        action=a.mention('action',name);ids[name]=action
        a.edge(owner,action,'owns')
    a.write('. Each has a benefit and cost: ')
    for i,name in enumerate(names):
        if i:a.write('; ')
        m=a.mention('action',name);a.edge(m,ids[name],'refers_to')
        a.write(' ');benefit_event=a.mention('outcome','benefit');a.write(' ')
        benefit=rng.randint(3,100)
        b=a.mention('quantity',str(benefit),benefit)
        a.edge(b,m,'applies_to');a.edge(m,benefit_event,'causes')
        a.edge(b,benefit_event,'measures')
        a.write(' ');cost_event=a.mention('outcome','cost');a.write(' ')
        cost=rng.randint(1,90)
        c=a.mention('quantity',str(cost),cost)
        a.edge(c,m,'applies_to');a.edge(m,cost_event,'depends_on')
        a.edge(c,cost_event,'measures')
        result=a.calculation('sub',[b,c]);a.edge(ids[name],result,'score')
    a.write('. Choose the plan with the greatest benefit minus cost.')
    return a.pack('maximize',list(ids.values()),'allocation',split,index,template)


def generate(output, *, train=200, dev=40, challenge=24, test=40, seed=47):
    path=Path(output)
    if path.exists():raise FileExistsError('GAMER_GRAPH_CORPUS_IMMUTABLE')
    if min(train,dev,challenge,test) < 2:raise ValueError('GAMER_GRAPH_SPLITS_TOO_SMALL')
    rng=random.Random(seed)
    rows=[];seen=set()
    for split,count in [('train',train),('dev',dev),('challenge',challenge),('test',test)]:
        for i in range(count):
            family=encounter if i%2==0 else allocation
            for _ in range(50):
                row=family(rng,split,i)
                if row['prompt'] not in seen:break
            else:raise RuntimeError('GAMER_GRAPH_CORPUS_DUPLICATE')
            seen.add(row['prompt']);rows.append(row)
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('x',encoding='utf-8') as out:
        for row in rows: out.write(json.dumps(row,sort_keys=True,ensure_ascii=False)+'\n')
    return {'path':str(path),'train':train,'dev':dev,'challenge':challenge,'test':test,
            'families':['encounter','allocation'],'train_only_gold_annotations':True,
            'dev_challenge_test_names_disjoint_from_train':True,
            'synthetic_multitoken_training_names':True,
            'test_names_disjoint_from_train':True,'test_not_for_tuning':True}



def _counterfactual_pair(rng, family, mode, index):
    """Author two structurally identical graphs with changed surface identities.

    The same surface appears as an actor in one prompt and an action in the
    other for role_swap. All quantities, graph edges, and decision semantics
    remain unchanged. No lexical lookup is introduced at inference.
    """
    labels=rng.sample(NAME_POOLS['train'],9)
    actor,first,second,third,op_a,op_b,op_c,renamed_actor,renamed_action=labels
    new_actor=first if mode=='role_swap' else renamed_actor
    new_first=actor if mode=='role_swap' else renamed_action
    sample_seed=rng.randrange(2**31)
    if family=='allocation':
        left=allocation(random.Random(sample_seed),'train',index,
                        actor_override=actor,names_override=[first,second,third])
        right=allocation(random.Random(sample_seed),'train',index,
                         actor_override=new_actor,names_override=[new_first,second,third])
    elif family=='encounter':
        # Both sides use the same actor counterpart, actions, counts, and payoffs.
        # Player action 0 and actor switch roles without altering graph topology.
        rival=rng.choice(ACTORS_TRAIN)
        left=encounter(random.Random(sample_seed),'train',index,
                       actor_override=actor,rival_override=rival,
                       player_override=[first,second,third],opponent_override=[op_a,op_b])
        right=encounter(random.Random(sample_seed),'train',index,
                        actor_override=new_actor,rival_override=rival,
                        player_override=[new_first,second,third],opponent_override=[op_a,op_b])
    else:
        raise ValueError('GAMER_GRAPH_UNKNOWN_COUNTERFACTUAL_FAMILY')
    if ([(n['kind'],n.get('value')) for n in left['graph']['nodes']] !=
        [(n['kind'],n.get('value')) for n in right['graph']['nodes']] or
        left['graph']['edges']!=right['graph']['edges'] or
        left['graph']['objective']!=right['graph']['objective'] or
        left['expected_decisions']!=right['expected_decisions']):
        raise RuntimeError('GAMER_GRAPH_COUNTERFACTUAL_SEMANTIC_DRIFT')
    return left,right


def extend_with_counterfactuals(base_dataset, output, *, pairs=300, seed=304):
    """Append balanced, author-annotated counterfactual *training* pairs.

    The original rows, including all dev/challenge/sealed-test samples, are
    preserved exactly. Family and role-change mode are crossed in four cells.
    """
    from tools.nanojev_gamer_v1_graph_curriculum import load_rows
    base=Path(base_dataset);path=Path(output)
    if path.exists():
        raise FileExistsError('GAMER_GRAPH_CORPUS_IMMUTABLE')
    if pairs<4 or pairs%4:
        raise ValueError('GAMER_GRAPH_COUNTERFACTUAL_PAIRS_MUST_BE_MULTIPLE_OF_FOUR')
    rows=load_rows(base)
    rng=random.Random(seed)
    used={row['prompt'] for row in rows}
    extensions=[];counts={family:{mode:0 for mode in ('role_swap','renaming')}
                          for family in ('encounter','allocation')}
    for i in range(pairs):
        family=('encounter','allocation')[i%2]
        mode=('role_swap','renaming')[(i//2)%2]
        for attempt in range(100):
            left,right=_counterfactual_pair(rng,family,mode,i)
            if left['prompt'] not in used and right['prompt'] not in used and left['prompt']!=right['prompt']:
                break
        else:
            raise RuntimeError('GAMER_GRAPH_COUNTERFACTUAL_COLLISION')
        counts[family][mode]+=1
        for side,row in (('a',left),('b',right)):
            row['counterfactual_pair']=f'cf-{seed}-{i}'
            row['counterfactual_side']=side
            row['counterfactual_mode']=mode
            row['counterfactual_family']=family
            extensions.append(row)
            used.add(row['prompt'])
    assert len(set(sum((list(x.values()) for x in counts.values()),[])))==1
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('x',encoding='utf-8') as f:
        for row in rows+extensions:
            f.write(json.dumps(row,sort_keys=True,ensure_ascii=False)+'\n')
    return {'path':str(path),'base_dataset_sha256':hashlib.sha256(base.read_bytes()).hexdigest(),
            'base_rows_preserved':len(rows),'training_only_counterfactual_pairs':pairs,
            'training_only_counterfactual_rows':len(extensions),
            'counterfactual_pairs_by_family_mode':counts,
            'family_mode_balanced':True,'dev_challenge_test_unchanged':True,
            'test_not_for_tuning':True}

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',required=True)
    p.add_argument('--extend-from',help='Existing immutable corpus; append training-only counterfactual pairs')
    p.add_argument('--counterfactual-pairs',type=int,default=300)
    p.add_argument('--train',type=int,default=200)
    p.add_argument('--dev',type=int,default=40)
    p.add_argument('--challenge',type=int,default=24)
    p.add_argument('--test',type=int,default=40)
    p.add_argument('--seed',type=int,default=47)
    args=p.parse_args(argv)
    result=(extend_with_counterfactuals(args.extend_from,args.output,pairs=args.counterfactual_pairs,seed=args.seed)
            if args.extend_from else generate(args.output,train=args.train,dev=args.dev,
                              challenge=args.challenge,test=args.test,seed=args.seed))
    print(json.dumps(result,indent=2))

if __name__=='__main__':main()
