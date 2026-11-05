#!/usr/bin/env python3
"""55 source-catalog named games: *bounded*, independently checked one-step kernels.

A catalog title supplies no executable rules. This module supplies an explicit,
restricted model for each entry. It does NOT claim to implement the full game,
a solution concept, hidden-information play, or equilibrium. Its state-action-
transition-reward table is a reusable 4th representation beside the existing
payoff, arithmetic, and probability/outcome tables.

Each numerical label is computed from the public bounded model independently
of the sparse arithmetic factor DAG used to train table translation.
"""
from __future__ import annotations
from fractions import Fraction
import hashlib
import json
import random
from tools import nanojev_game_transformation_library as library

# (catalog family, exact catalog title, model, operative bounded rule)
# Models are deliberately finite, one-step tasks; full-game objectives must
# get new executable rules rather than silently expanding these labels.
ENTRIES = (
('matrix','Shapley stochastic-matrix example','markov','One state-action choice in a finite stochastic matrix game; the opponent action is fixed.'),
('social_dilemmas','Iterated Prisoners Dilemma','repeat','One publicly observed cooperation/defection stage with discounted reward accounting.'),
('evolutionary','Invasion Game','population','One finite population update following invader arrivals and incumbent departures.'),
('evolutionary','Population Snowdrift','population','One discrete snowdrift population update with arrivals and departures.'),
('timing','Centipede','timing','One finite centipede decision: continue for the next prize or stop for the current prize.'),
('timing','Timing Duel','timing','One timing duel decision: wait one tick at a known cost versus stopping now.'),
('timing','Investment Timing','timing','One investment decision: compare next-period opportunity with present stop payoff.'),
('timing','Escalation and Brinkmanship','timing','One escalation decision: compare the next prize minus escalation cost with stopping.'),
('bargaining','Alternating Offers','offer','An offer can be accepted now or rejected for one discounted counteroffer.'),
('bargaining','Rubinstein Bargaining','offer','One time-discounted offer/counteroffer comparison, not infinite-horizon equilibrium.'),
('bargaining','Coalition Bargaining','offer','One coalition offer/counteroffer comparison with explicit outside-value discount.'),
('information','Lewis Signaling','signal','One received signal updates a finite prior belief over two hidden possible types.'),
('information','Cheap Talk','signal','One cost-free message updates a prior using specified message likelihoods.'),
('information','Costly Signaling','signal','One costly message updates a prior; the numerical target is belief, not net signaling equilibrium.'),
('information','Bayesian Coordination','signal','One public coordination signal updates a two-type prior.'),
('information','Electronic Mail Game','signal','One observed message updates the belief about an unobserved event.'),
('information','Information Cascade','signal','One public observation updates a prior without revealing the hidden type.'),
('information','Adverse Selection','signal','One observable quality indicator updates the prior over hidden quality types.'),
('information','Sender Receiver Game','signal','One receiver observation updates the prior over sender types.'),
('repeated','Repeated Cooperation','repeat','One cooperative stage adds a discounted known payoff to cumulative utility.'),
('repeated','Reputation','repeat','One public reputation round adds the stated stage reward to discounted value.'),
('repeated','Trust Game','repeat','One repeated trust round credits an explicitly stated return.'),
('repeated','Reciprocity','repeat','One repeated reciprocal-response round credits a stated payoff.'),
('repeated','Trigger Strategy','repeat','One stage following a trigger-state observation credits the stage payoff.'),
('repeated','Repeated Inspection','repeat','One observed inspection round credits a stated inspection payoff.'),
('repeated','Repeated Arms Race','repeat','One arms-race stage credits a stated resource-adjusted payoff.'),
('stochastic','Markov Game','markov','One observed state and fixed joint action have two possible next states.'),
('stochastic','Discounted Stochastic Game','markov','One state-action Bellman backup with stated transition probability and discount.'),
('stochastic','Stochastic Bargaining','markov','One stochastic bargaining state with two specified next-offer values.'),
('stochastic','Partial Observable Stochastic Game','markov','One belief-state prediction using explicit next-state probabilities, not hidden realized states.'),
('stochastic','Dynamic Security Game','markov','One stochastic defensive action with two next-state continuation values.'),
('security','Inspection Game','security','One defensive inspection action faces a stated chance of undetected loss.'),
('security','Cyber Defense','security','One defensive cyber action faces a stated probability of breach and fixed loss.'),
('security','Electronic Warfare Game','security','One electronic-defense action has a stated disruption probability and loss.'),
('mechanism','Voting Agenda','quorum','One agenda transition is decided by the signed votes-over-threshold margin.'),
('coalition','Coalition Formation','coalition','One coalition join decision compares added coalition value with the membership cost.'),
('coalition','Alliance Prisoners Dilemma','coalition','One alliance decision compares combined coalition value with standing-alone value and cost.'),
('coalition','Colluders Council','coalition','One colluding council decision compares coalition value with outside value and coordination cost.'),
('byzantine','Proof of Stake Validator Game','quorum','One validator stake-support decision compares present stake weight with approval threshold.'),
('byzantine','Quorum Governance','quorum','One governance proposal uses the signed quorum margin.'),
('byzantine','Threshold Escalation','quorum','One escalation transition compares supporting votes with required threshold.'),
('deception','Adversarial Signaling','signal','One deceptive message updates receiver belief under stated adversarial likelihoods.'),
('deception','Bluffing','signal','One bluffing observation updates belief over true/false private states.'),
('population','Population Congestion','population','One population migration update has stated arrivals and departures.'),
('risk','Adversarial Bandits','regret','One adversarial reward reveal updates cumulative realized regret.'),
('risk','Online Learning Game','regret','One online decision updates cumulative regret against a stated comparator.'),
('risk','Adversarial Self Play','regret','One self-play result updates cumulative regret for a specified actor.'),
('captain','Captain Relative Navigation','navigation','One navigation time step updates signed relative position.'),
('captain','Captain Crew Evacuation','resource','One evacuation action changes the remaining crew aboard.'),
('captain','Captain Hull Preservation','resource','One damage-control action changes remaining hull integrity.'),
('captain','Captain Evidence Discipline','signal','One observed evidence signal updates a prior without revealing the true state.'),
('captain','Captain Pursuit','navigation','One pursuit time step updates signed ship-target separation.'),
('captain','Captain Bridge Defense','security','One bridge-defense action faces a stated chance of loss.'),
('captain','Captain Coalition Negotiation','offer','One captain-led negotiation compares present offer with a discounted next offer.'),
('captain','Captain Shipboard Consensus','quorum','One shipboard decision compares supporting votes against a stated quorum.'),
)

MODE_FORMULAS = {
 'markov':'reward_now+discount*(p_good*value_good+(1-p_good)*value_bad)',
 'repeat':'bank+stage_reward*discount',
 'population':'population+arrivals-departures',
 'timing':'next_prize-wait_cost-stop_prize',
 'offer':'offer-discount*counteroffer',
 'signal':'prior*likelihood_high/(prior*likelihood_high+(1-prior)*likelihood_low)',
 'security':'asset-defense_cost-breach_probability*loss',
 'quorum':'support-threshold',
 'coalition':'joined_value-standalone_value-join_cost',
 'regret':'prior_regret+best_reward-chosen_reward',
 'navigation':'gap+target_speed-pursuer_speed',
 'resource':'remaining-used',
}

MODE_OBJECTIVES = {
 'markov':'current action value: immediate reward plus the discounted expected value of the next state',
 'repeat':'cumulative credited value after this one discounted stage',
 'population':'population count after this update',
 'timing':'signed benefit of continuing one turn instead of stopping now',
 'offer':'signed acceptance advantage over the discounted counteroffer',
 'signal':'posterior probability of the higher hidden type after the observed signal',
 'security':'expected remaining asset value after defense cost and possible breach',
 'quorum':'signed support margin over the required threshold',
 'coalition':'additional coalition value after joining cost versus standing alone',
 'regret':'cumulative realized regret after this decision',
 'navigation':'signed separation after one time interval',
 'resource':'remaining resource after the stated one-step use',
}

SPECS={}
for family,title,mode,rule in ENTRIES:
    game=library.family_game_id(title)
    if game in SPECS:raise AssertionError('GAMER_DUPLICATE_TRANSITION_TITLE')
    SPECS[game]={'family':family,'title':title,'mode':mode,'rule':rule,'formula':MODE_FORMULAS[mode], 'objective':MODE_OBJECTIVES[mode]}
TRANSITION_IDS=frozenset(SPECS)

# The system's verified catalog is the only namespace, never generated names.
CATALOG_BY_ID={library.family_game_id(t):(f,t) for f, names in library.CATALOG.items() for t in names.split('|')}
if len(SPECS)!=55 or any(CATALOG_BY_ID.get(g)!=(s['family'],s['title']) for g,s in SPECS.items()):
    raise AssertionError('GAMER_TRANSITION_REGISTRY_DRIFT')


def rand_for(game,index,seed,attempt):
    canonical=json.dumps(['transition-v1',game,index,seed,attempt],separators=(',',':'))
    h=hashlib.sha256(canonical.encode('utf-8')).hexdigest()
    return random.Random(int(h[:16],16))


def parameters(game,index,seed,attempt=0):
    """Deterministic varied parameters. No unknown game names are synthesized."""
    mode=SPECS[game]['mode']; rng=rand_for(game,index,seed,attempt)
    n=lambda a,b:rng.randint(a,b)
    rate=lambda:rng.choice(['1/5','1/4','1/3','2/5','1/2','3/5','2/3','3/4','4/5'])
    if mode=='markov':
        return dict(reward_now=n(-6,12),discount=rate(),p_good=rate(),value_good=n(5,25),value_bad=n(-10,4))
    if mode=='repeat':
        return dict(bank=n(-15,25),stage_reward=n(-8,13),discount=rate())
    if mode=='population':
        population=n(15,50); departures=n(0,8); arrivals=n(0,10)
        return dict(population=population,arrivals=arrivals,departures=departures)
    if mode=='timing':
        return dict(next_prize=n(4,35),wait_cost=n(0,12),stop_prize=n(5,28))
    if mode=='offer':
        return dict(offer=n(1,32),discount=rate(),counteroffer=n(1,35))
    if mode=='signal':
        return dict(prior=rate(),likelihood_high=rate(),likelihood_low=rate())
    if mode=='security':
        return dict(asset=n(20,70),defense_cost=n(1,12),breach_probability=rate(),loss=n(5,25))
    if mode=='quorum':
        threshold=n(6,30); support=threshold+rng.choice([-4,-3,-2,-1,0,1,2,3,4])
        return dict(support=support,threshold=threshold)
    if mode=='coalition':
        return dict(joined_value=n(10,65),standalone_value=n(7,50),join_cost=n(1,15))
    if mode=='regret':
        chosen=n(-3,10); best=chosen+n(0,12)
        return dict(prior_regret=n(0,20),best_reward=best,chosen_reward=chosen)
    if mode=='navigation':
        return dict(gap=n(-12,30),target_speed=n(0,12),pursuer_speed=n(0,14))
    if mode=='resource':
        remaining=n(20,60); used=n(0,remaining)
        return dict(remaining=remaining,used=used)
    raise ValueError('GAMER_UNKNOWN_TRANSITION_MODE')


def oracle(game,values):
    """Native, independently spelled-out bounded transition objective."""
    m=SPECS[game]['mode'];v={k:Fraction(str(x)) for k,x in values.items()}
    if m=='markov':return v['reward_now']+v['discount']*(v['p_good']*v['value_good']+(1-v['p_good'])*v['value_bad'])
    if m=='repeat':return v['bank']+v['stage_reward']*v['discount']
    if m=='population':return v['population']+v['arrivals']-v['departures']
    if m=='timing':return (v['next_prize']-v['wait_cost'])-v['stop_prize']
    if m=='offer':return v['offer']-(v['counteroffer']*v['discount'])
    if m=='signal':
        event_high=v['likelihood_high']*v['prior']; event_low=v['likelihood_low']*(1-v['prior'])
        return event_high/(event_high+event_low)
    if m=='security':return v['asset']-(v['defense_cost']+v['breach_probability']*v['loss'])
    if m=='quorum':return v['support']-v['threshold']
    if m=='coalition':return v['joined_value']-(v['standalone_value']+v['join_cost'])
    if m=='regret':return v['prior_regret']+(v['best_reward']-v['chosen_reward'])
    if m=='navigation':return v['gap']+(v['target_speed']-v['pursuer_speed'])
    if m=='resource':return v['remaining']-v['used']
    raise ValueError('GAMER_UNKNOWN_TRANSITION_MODE')


def model_rows(game,v):
    """Rows are public rules, not oracle outputs. Probabilities are normalized."""
    m=SPECS[game]['mode']
    def r(state,actor,visible,action,prob,next_state,reward,terminal):
        return dict(state=state,actor=actor,visible=visible,action=action,
                    probability=str(prob),next_state=next_state,reward=reward,terminal=terminal)
    if m=='signal':
        # Hidden type is a possible world, not an observed type. Show emission
        # model conditioned on type, NEVER a realized secret type.
        p=Fraction(v['prior']); hi=Fraction(v['likelihood_high']);lo=Fraction(v['likelihood_low'])
        return [r('prior belief','nature','type hidden','higher type',p,'signal model','–','no'),
                r('prior belief','nature','type hidden','lower type',1-p,'signal model','–','no'),
                r('higher type (hypothesis)','sender','type hidden to receiver','observed signal',hi,'posterior belief –','–','no'),
                r('higher type (hypothesis)','sender','type hidden to receiver','no signal',1-hi,'other observation','–','no'),
                r('lower type (hypothesis)','sender','type hidden to receiver','observed signal',lo,'posterior belief –','–','no'),
                r('lower type (hypothesis)','sender','type hidden to receiver','no signal',1-lo,'other observation','–','no')]
    if m=='markov':
        p=Fraction(v['p_good']);return [r('current state','players','current state','fixed joint action: good outcome',p,'good state','–','no'),
                                        r('current state','players','current state','fixed joint action: bad outcome',1-p,'bad state','–','no')]
    if m=='security':
        p=Fraction(v['breach_probability']);return [r('asset at risk','defender','asset and risk model','defend; breach occurs',p,'damaged state','–','no'),
                                                    r('asset at risk','defender','asset and risk model','defend; no breach',1-p,'protected state','–','no')]
    if m=='timing':return [r('current decision','decision maker','offers and wait cost','stop',1,'terminal stop','–','yes'),
                           r('current decision','decision maker','offers and wait cost','continue one turn',1,'next decision','–','no')]
    if m=='offer':return [r('offer received','responder','public offer','accept',1,'terminal agreement','–','yes'),
                          r('offer received','responder','public offer','reject',1,'counteroffer next round','–','no')]
    if m=='quorum':return [r('pending proposal','voters','support and threshold','count support',1,'accepted if margin >= 0, else rejected','–','yes')]
    if m=='coalition':return [r('separate groups','coalition members','joined and outside values','join',1,'joined coalition','–','no'),
                              r('separate groups','coalition members','joined and outside values','stay apart',1,'separate groups','–','no')]
    if m=='population':return [r('population at time t','agents','arrival/departure counts','one update',1,'population at t+1 = –','–','no')]
    if m=='repeat':return [r('round t and accumulated bank','players','stage payoff and discount','play specified stage',1,'round t+1','–','no')]
    if m=='regret':return [r('cumulative regret at t','decision maker','revealed reward and comparator','update regret',1,'cumulative regret at t+1 = –','–','no')]
    if m=='navigation':return [r('relative separation at t','ships','speeds and current gap','advance one interval',1,'relative separation at t+1 = –','–','no')]
    if m=='resource':return [r('available resource at t','captain','resource used this turn','consume resource',1,'remaining resource at t+1 = –','–','no')]
    raise ValueError('GAMER_UNKNOWN_TRANSITION_MODE')


def validate_kernel(game,values,rows):
    """Check domains and per-(state,actor,action) chance normalization."""
    mode=SPECS[game]['mode'];v={k:Fraction(str(x)) for k,x in values.items()}
    for k in ('discount','p_good','breach_probability','prior','likelihood_high','likelihood_low'):
        if k in v and not (0<v[k]<1):raise ValueError('GAMER_INVALID_TRANSITION_PROBABILITY')
    if mode=='population' and (v['population']<v['departures'] or v['arrivals']<0):raise ValueError('GAMER_INVALID_POPULATION')
    if mode=='resource' and not (0<=v['used']<=v['remaining']):raise ValueError('GAMER_INVALID_RESOURCE')
    if mode=='regret' and v['best_reward']<v['chosen_reward']:raise ValueError('GAMER_INVALID_REGRET')
    if mode=='quorum' and (v['threshold']<=0 or v['support']<0):raise ValueError('GAMER_INVALID_QUORUM')
    if mode=='signal':
        distributions=[[rows[0],rows[1]],[rows[2],rows[3]],[rows[4],rows[5]]]
    elif mode in ('markov','security'):
        distributions=[rows]
    else:distributions=[[r] for r in rows]
    for distribution in distributions:
        if sum((Fraction(row['probability']) for row in distribution),Fraction())!=1:
            raise ValueError('GAMER_UNNORMALIZED_TRANSITION_PROBABILITY')
        if any(Fraction(row['probability'])<0 or Fraction(row['probability'])>1 for row in distribution):
            raise ValueError('GAMER_BAD_TRANSITION_PROBABILITY')
    if mode=='signal' and any('hidden to receiver' not in row['visible'] for row in rows[2:]):
        raise ValueError('GAMER_SIGNAL_PRIVATE_TYPE_LEAK')
    return True


def _fmt(v):
    return str(Fraction(str(v)))


def prompt(game,values):
    s=SPECS[game];m=s['mode']
    intro=f'Consider a bounded, finite one-step instance of {s["title"]}. {s["rule"]} This is not a request for a full-game equilibrium.'
    quantities=' '.join(f'The specified {k.replace("_"," ")} is {_fmt(v)}.' for k,v in sorted(values.items()))
    if m=='signal':
        contract='The hidden type is not revealed to the receiver. The prior is for the higher type. The observed signal has the specified likelihood high under the higher type and likelihood low under the lower type. Compute the posterior after observing the signal.'
    elif m=='markov':
        contract='A fixed action produces the good next state with probability p good, otherwise the bad next state. Reward now is paid immediately and the expected next-state value is discounted. Compute the action value.'
    elif m=='security':
        contract='Breach happens with the specified probability; otherwise no damage occurs. Defense cost is always paid. Compute expected remaining asset value.'
    elif m=='offer':contract='Accept obtains offer now. Reject obtains counteroffer one period later with the stated discount. Compute the advantage of accepting.'
    elif m=='timing':contract='Stop now pays stop prize. Continue one period pays next prize minus wait cost. Compute the signed advantage of continuing.'
    elif m=='quorum':contract='Count the stated support toward the threshold; a negative margin blocks this one decision. Compute the signed margin.'
    elif m=='coalition':contract='Compare the coalition value after joining cost to the separate-group outside value. Compute the net gain of joining.'
    elif m=='regret':contract='The comparator reward is at least the chosen reward. Add the newly revealed one-step regret to the prior cumulative regret.'
    elif m=='navigation':contract='Over exactly one interval, target motion increases signed separation and pursuer motion decreases it.'
    elif m=='resource':contract='Consume the stated used amount from the resources currently remaining. No other loss occurs in this bounded step.'
    elif m=='population':contract='Arrivals join and departures leave during one finite population update.'
    else:contract='Credit this specified one-round stage reward multiplied by its time discount to the accumulated bank.'
    return f'{intro} {quantities} {contract} What is the {s["objective"]}?'


def math_table(game,values,rows,factors,render_factors):
    s=SPECS[game]
    text=[f'Bounded one-step state-transition model for {s["title"]}.',
          f'Operative rule: {s["rule"]}',
          'Only public observations and distributions are used; no realized hidden state is exposed.',
          '| Quantity | Given numerical value |','|---|---:|']
    text.extend(f'| {name.replace("_"," ")} | {_fmt(v)} |' for name,v in sorted(values.items()))
    text.extend(['','State–Action–Transition–Reward table:',
                 '| State | Actor | Information available | Action / event | Probability | Next state | Reward | Terminal |',
                 '|---|---|---|---|---:|---|---|---|'])
    for row in rows:
        text.append('| '+' | '.join(str(row[k]) for k in ('state','actor','visible','action','probability','next_state','reward','terminal'))+' |')
    text.extend(['','Calculate the stated one-step objective using the arithmetic factor table below.',
                 'Every computed result cell stays blank; the numerical output head must compute from the original human prompt.',
                 render_factors(factors),'',f'Report the numerical result of step {len(factors)}. Do not insert computed answers here.'])
    return '\n'.join(text)
