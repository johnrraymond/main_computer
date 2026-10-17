#!/usr/bin/env python3
"""NanoJev game-theory registry, transformation dictionary and supervised corpus v1.

Offline, deterministic, CPU-only. Does not modify the trainer, live checkpoints,
existing task banks or local database. Distinguishes executable synthetic oracles
from catalog-only mathematical descriptions and exact from supervised transforms.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_EVEN
import hashlib
import json
import math
from pathlib import Path
from contextlib import closing
import random
import sqlite3
from typing import Any

try:
    from .nanojev_hawk_dove_captains_v1 import CAPTAINS
    from .nanojev_hawk_dove_scenarios_v1 import SCENARIOS
    from .nanojev_register_stability_v3_generate import logbar
    from .nanojev_training_task_store import TrainingTaskStore
except ImportError:
    from nanojev_hawk_dove_captains_v1 import CAPTAINS
    from nanojev_hawk_dove_scenarios_v1 import SCENARIOS
    from nanojev_register_stability_v3_generate import logbar
    from nanojev_training_task_store import TrainingTaskStore

VERSION = 'nanojev.game-library.v1'
FAMILY = 'game_transformations_v1'
SCALE = 20_000
OP = 'subtract'
HELDOUT_GAMES = frozenset({'travelers_dilemma', 'hypergame'})


def canonical(v: Any) -> str:
    return json.dumps(v, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def sha(v: Any) -> str:
    return hashlib.sha256(canonical(v).encode('utf-8')).hexdigest()


# The taxonomy includes distinct mathematical forms; it is deliberately NOT a
# claim that every named game is mathematically equivalent to Hawk-Dove.
FAMILIES = (
 ('matrix', 'Finite simultaneous strategic games', 'normal form', 'u_i:A_1 x ... x A_n -> R'),
 ('sequential', 'Sequential and repeated interaction', 'extensive form', 'history h, information sets I_i, discounted U_i'),
 ('information', 'Bayesian information and signaling', 'Bayesian/extensive', 'posterior proportional to likelihood times prior'),
 ('allocation', 'Resource-allocation and public goods', 'constrained optimization', 'max U_i(a_i,a_-i) subject to resource budget'),
 ('timing', 'Time, persistence and stochastic control', 'dynamic', 'V(s)=max_a E[r(s,a)+gamma V(s_next)]'),
 ('belief', 'Common knowledge, uncertain models and deception', 'belief hierarchy', 'beliefs over states and beliefs of others'),
 ('population', 'Congestion, populations and evolutionary adjustment', 'population', 'replicator: dx_i/dt=x_i(f_i - mean_f)'),
 ('security', 'Pursuit, evasion and security games', 'adversarial planning', 'max_min expected outcome subject to information constraints'),
 ('coalition', 'Bargaining, coalitions and mechanism design', 'cooperative/noncooperative', 'coalition value v(S), feasible allocations and outside options'),
)

# id, title, family, mathematical family/kernel, exact mathematical breakdown,
# structural assumptions, experiment status, catalog search aliases.
GAMES = (
 ('hawk_dove','Hawk-Dove','matrix','matrix','2x2 anti-coordination: T>R>S>P; (H,H) costly, unilateral H rewarded','simultaneous; two actions','executable','chicken, snowdrift'),
 ('chicken','Chicken','matrix','matrix','2x2 anti-coordination: T>R>S>P; mutual defiance worst','simultaneous; two actions','executable','hawk dove'),
 ('prisoners_dilemma','Prisoners Dilemma','matrix','matrix','T>R>P>S; defection dominates, cooperation Pareto improves DD','simultaneous; two actions','executable','PD'),
 ('stag_hunt','Stag Hunt','matrix','matrix','two coordination equilibria; R>T>=P>S; risky cooperative action','simultaneous; two actions','executable','assurance'),
 ('assurance','Assurance Game','matrix','matrix','coordination with cooperation requiring sufficient belief about partner','simultaneous; two actions','executable','stag hunt'),
 ('battle_sexes','Battle of the Sexes','matrix','matrix','two coordinated outcomes with asymmetric preferred equilibrium','asymmetric player payoffs; simultaneous','executable','coordination'),
 ('matching_pennies','Matching Pennies','matrix','matrix','zero-sum 2x2; no pure Nash equilibrium','opponent payoffs are negatives','executable','hide and seek'),
 ('deadlock','Deadlock','matrix','matrix','defection socially and individually favored: T>P>R>S','simultaneous; two actions','executable','dominance'),
 ('pure_coordination','Pure Coordination','matrix','matrix','matching actions reward both, mismatch penalized','simultaneous; two actions','executable','coordination'),
 ('repeated_pd','Repeated Prisoners Dilemma','sequential','repeated','U=sum_t gamma^t u_t; conditional cooperation, retaliation, forgiveness','discounted horizon or infinite with gamma<1','executable','tit for tat'),
 ('centipede','Centipede','sequential','sequential','backward induction over alternating take/pass choices','perfect information; finite horizon','catalog_only','take pass'),
 ('entry_deterrence','Entry Deterrence','sequential','sequential','entrant enter/stay; incumbent fight/accommodate; credible threats','sequential perfect information','catalog_only','entry game'),
 ('trust_game','Trust Game','sequential','trust','invest then reciprocate/appropriate, expected trust return vs outside option','binary reduced version; probability of reciprocity specified','executable','investment trust'),
 ('ultimatum','Ultimatum Game','coalition','bargain','responder accepts split or rejects with both losing','binary accept/reject reduced version','executable','fair division'),
 ('nash_bargaining','Nash Bargaining','coalition','bargain','maximize (u1-d1)(u2-d2) over individually rational feasible set','requires specified feasible set and disagreement utilities','catalog_only','bargain solution'),
 ('coalition_formation','Coalition Formation','coalition','threshold','v(S), participation threshold and allocation across members','simplified threshold reduced version','executable','coalitions'),
 ('principal_agent','Principal-Agent','coalition','sequential','contract induces effort under hidden action, incentives and participation','moral hazard; utility depends on contract','catalog_only','agency'),
 ('signaling','Signaling Game','information','bayes','P(type|signal) proportional P(signal|type)P(type); expected receiver action values','known prior and signal likelihoods','executable','Spence signaling'),
 ('cheap_talk','Cheap Talk','information','bayes','sender message without commitment; receiver posterior and incentives','message may be strategically selected','executable','Crawford Sobel'),
 ('inspection','Inspection Game','information','inspection','scan cost vs probabilistic detection benefit, strategic concealment','known detection probabilities; simplified two-action model','executable','hide inspect'),
 ('kuhn_poker','Kuhn Poker','information','bayes','imperfect-information betting; private cards and bluff probabilities','three-card poker; catalog only until full extensive game','catalog_only','poker'),
 ('colonel_blotto','Colonel Blotto','allocation','blotto','choose a_i>=0, sum_i a_i<=budget; payoff sum_i w_i 1[a_i>b_i]','ties score 0; two specified allocation plans','executable','resource allocation'),
 ('public_goods','Public Goods','allocation','threshold','contribution incurs cost, benefit depends on total contribution','threshold public-goods reduced case','executable','free rider'),
 ('volunteer_dilemma','Volunteers Dilemma','allocation','threshold','collective benefit if at least one volunteers; private volunteer cost','threshold one; binary contribution','executable','volunteer'),
 ('commons','Tragedy of the Commons','allocation','congestion','utility from extraction minus collective resource pressure','two extraction choices under aggregate load','executable','common pool'),
 ('routing','Routing / Congestion','allocation','congestion','individual route value decreases with crowding and capacity effects','two routes; aggregate load supplied','executable','Braess'),
 ('war_attrition','War of Attrition','timing','attrition','contest value less accumulated waiting cost vs immediate exit','one-step continue/exit choice','executable','attrition'),
 ('stochastic_game','Stochastic Game','timing','markov','Q(s,a)=r(s,a)+gamma sum_snext P(snext|s,a)V(snext)','specified one-step continuation values','executable','Markov game'),
 ('differential_game','Differential Game','timing','differential','state derivative f(x,u1,u2); integral cost and terminal utility','continuous state/actions; catalog only','catalog_only','optimal control'),
 ('bayesian_game','Bayesian Game','belief','bayes','expected payoff conditional on hidden opponent type prior and evidence','fixed type prior and observation likelihoods','executable','incomplete information'),
 ('electronic_mail','Electronic-Mail Game','belief','common_knowledge','higher-order beliefs from possibly missed acknowledgements','lack of common knowledge under unreliable messages','catalog_only','Rubinstein email'),
 ('byzantine_coordination','Byzantine Coordination','belief','common_knowledge','coordination under faulty or adversarial communication','needs explicit fault/communication model','catalog_only','Byzantine generals'),
 ('hypergame','Hypergame','belief','hyper','opponent may use a different perceived game/payoff model','declared alternate models and subjective priors','executable','perception games'),
 ('el_farol','El Farol Bar','population','congestion','attendance utility depends on how many others attend','threshold capacity and attendance forecast','executable','minority'),
 ('minority','Minority Game','population','congestion','reward for choosing underselected alternative','population share supplied','executable','minority'),
 ('evolutionary','Evolutionary Game','population','replicator','dx_i/dt=x_i(f_i(x)-mean_f(x)); fitness differences','population strategy fractions','catalog_only','replicator dynamics'),
 ('pursuit_evasion','Pursuit-Evasion','security','pursuit','intercept chance versus escape, risk and resource costs','simplified probabilistic intercept','executable','hide seek'),
 ('security_game','Stackelberg Security Game','security','inspection','defender allocates patrol coverage, attacker responds to distribution','coverage probability fixed for reduced example','executable','security patrol'),
 ('travelers_dilemma','Travelers Dilemma','matrix','matrix','claim low/high; lower claim plus reward, higher claim minus penalty','reduced two-claim instance, not full action space','executable','traveler'),
)

# For matrix games: two player-1 action rows A/B versus opponent columns C/D.
# q = belief opponent chooses C. The opponent's distinct payoff matrix can be
# added later; one player's utility evaluation never implies symmetric payoffs.
MATRICES = {
 'hawk_dove':((60,90),(20,5)), 'chicken':((60,95),(25,5)),
 'prisoners_dilemma':((70,10),(95,30)), 'stag_hunt':((90,10),(65,50)),
 'assurance':((85,15),(62,51)), 'battle_sexes':((90,15),(30,65)),
 'matching_pennies':((80,20),(20,80)), 'deadlock':((55,10),(90,65)),
 'pure_coordination':((90,10),(10,80)), 'travelers_dilemma':((80,90),(70,100)),
}

# source -> target, type, formula/rule, valid conditions, meaning. These are
# transformation TYPES, not all isomorphisms. Supervised tasks are explicitly
# distinguished from exact algebraic/encoding transformations.
TRANSFORMS = (
 ('payoff_to_expected','normal_form','expected_utility','exact_conditional','q*u(A,C)+(1-q)*u(A,D)','fixed opponent action belief q; linear expectation','expected payoff for each candidate action'),
 ('expected_to_ordinal','expected_utility','preference_margin','exact','Z=X-Y','same utility scale and actor','signed preference difference'),
 ('ordinal_to_choice','preference_margin','binary_choice','lossy','contest iff Z>0; yield iff Z<0','zero is indifference','decision cannot recover exact X and Y'),
 ('payoff_affine','normal_form','normal_form','strategic_equivalence','u_new=a*u+b where a>0','same positive a and action-independent b per player','best responses unchanged, raw differences rescaled'),
 ('swap_action_rows','normal_form','normal_form','strategic_isomorphism','swap action labels and corresponding rows','apply label permutation consistently','relabeling preserves strategic game'),
 ('swap_action_columns','normal_form','normal_form','strategic_isomorphism','swap opponent action labels and corresponding columns','apply label permutation consistently','relabeling preserves strategic game'),
 ('game_to_pairwise','action_values','paired_operands','exact','X=U(action A), Y=U(action B)','specified agent and information state','projection from values to X/Y'),
 ('pairwise_to_registers','paired_operands','native_registers','reversible_encoding','XXXXXX=X, YYYYYY=Y with native LogBars','bounded integer operand domain; LogBars checked','native head-compatible numeric surface'),
 ('register_to_roles','native_registers','semantic_bindings','semantic_equivalence','replace first/second register description with role name','values remain explicitly present in the prompt','numerical semantic binding task'),
 ('roles_to_story','semantic_bindings','natural_story','supervised_inference','derive X/Y from game rules, jacket and observed state','numerical labels withheld; deterministic teacher must supply labels','genuine semantic projection not a reversible text encoding'),
 ('story_paraphrase','natural_story','natural_story','semantic_equivalence_test','reword without altering facts, utilities, or information sets','manually or mechanically validate facts remain constant','invariance test'),
 ('orientation_swap','paired_operands','paired_operands','reversible_encoding','swap presentation order, preserve semantic X/Y identities','no relabeling of numerical meaning','paired orientation consistency'),
 ('bayes_update','prior_likelihood','posterior','exact_conditional','P(T|m)=P(m|T)P(T)/P(m)','positive evidence probability; likelihoods calibrated','posterior beliefs'),
 ('history_discount','stage_payoffs','discounted_value','exact_conditional','sum(t=0..H) gamma^t r_t','fixed gamma and horizon','continuation-value conversion'),
 ('blotto_allocate','allocation_vector','win_vector','exact_conditional','win_i=1[a_i>b_i], payoff=sum w_i win_i','allocation vector feasible and tie rule specified','battlefield values'),
 ('belief_to_expected','beliefs_and_payoffs','action_values','exact_conditional','Q(a)=sum_h P(h|I)u(a,h)','coherent probability distribution and payoff model','Bayesian expected value'),
 ('normal_to_extensive','normal_form','imperfect_info_tree','structural_equivalence','simultaneous moves represented by second mover unable to observe first','second-player decision nodes in one information set','normal form as information-set extensive game'),
 ('one_shot_to_repeated','normal_form','repeated_game','extension','add histories, contingent strategies, horizon, discount factor','not payoff-equivalent to one-shot game','new game with memory'),
 ('risk_to_jacket','objective_payoffs','jacket_values','provisional_teacher','synthetic captain disposition shifts relative action preference','must be visible as jacket text; no optimality claim','supervision for jacket-conditioned decisions'),
)

DICTIONARY = (
 ('game','An interaction defined by players, available actions, information and payoff rules','game definition'),
 ('normal_form','Payoff tensor indexed by an action for each player','mathematical structure'),
 ('extensive_form','Tree of histories, acting player, observations and terminal rewards','mathematical structure'),
 ('information_set','Histories an actor cannot distinguish when acting','information'),
 ('private_type','Player property unobserved by other participants','information'),
 ('common_knowledge','A fact everyone knows, everyone knows everyone knows, and so on','information'),
 ('strategy','Rule mapping information sets or states to action distributions','policy'),
 ('mixed_strategy','Probability distribution over actions','policy'),
 ('belief','Probability assigned to unobserved state, action or type','information'),
 ('payoff','Specified numerical consequence for a player','utility'),
 ('utility','Jacket- and game-dependent numerical evaluation of outcome','utility'),
 ('expected_utility','Probability-weighted average of utility over uncertain outcomes','utility'),
 ('best_response','Maximizer of own expected utility given opponents strategy','solution concept'),
 ('nash_equilibrium','A strategy profile at which no player benefits by unilateral deviation','solution concept'),
 ('dominance','An action performs at least as well against every opponent action','solution concept'),
 ('pareto','A change improves someone without worsening anyone','solution concept'),
 ('regret','Utility of best available action minus utility of chosen action','metric'),
 ('exploitability','Improvement available to best-responding opponents, under specified conventions','metric'),
 ('jacket','Captain-specific commitments, priorities and constraints','conditioning'),
 ('scenario','Common exogenous situation presented to all compared captains','conditioning'),
 ('categorical_state','Low/medium/high and other nonnumeric observations','conditioning'),
 ('opponent_model','Explicit forecast or posterior about the opponents actions/types','conditioning'),
 ('rubric','Deterministic synthetic function mapping conditions to labeled values','teacher'),
 ('X','First utility operand after specified game-dependent projection','regression target'),
 ('Y','Second utility operand after specified game-dependent projection','regression target'),
 ('Z','X minus Y; preference margin before decision threshold','regression target'),
 ('LogBars','Native unary-like numerical scaffold in current NanoJev prompts','representation'),
 ('orientation','Forward or reversed presentation with unchanged semantic operand identities','representation'),
 ('transformation','Declared mapping with domain, codomain, assumptions and loss type','representation'),
 ('heldout_game','Game structure withheld from training for transfer measurement','evaluation'),
 ('heldout_jacket','Jacket withheld across all games to measure novel captain generalization','evaluation'),
 ('out_of_distribution','Input structure or scale unlike training distribution','evaluation'),
)

OBJECTIVE = {
 'id':'game_transformations_supervised_v1', 'status':'design_and_verified_synthetic_dataset_not_active_training',
 'model':'existing Qwen3-0.6B frozen backbone + mix_v4 XY decoder and operation head',
 'target_schema':{'X':'0..2000000 canonical utility projection for action A','Y':'0..2000000 canonical utility projection for action B',
                  'Z':'X-Y diagnostic','OP':'subtract; all seeded tasks','orientation':'both; preserve X/Y role under reordering'},
 'goals':[
  'extract X/Y when explicitly registered in native LogBar notation',
  'bind numerical values to semantically described roles across orientations',
  'infer missing X or Y from jacket + scenario + executable game rubric',
  'infer both operands with no explicit target numerals',
  'preserve correct operation identification despite categorical and prose context',
  'generalize to withheld jackets, scenarios, and eventually whole game families',
  'retain legacy arithmetic and earlier learned games after each expansion'],
 'losses':{
  'XY':'existing mix_v4 decoder sign/mantissa/exponent regression, exactly as existing trainer implements',
  'OP':'cross_entropy(existing 14-class classifier, subtract), separately evaluated',
  'paired':'both forward and reversed representations supervised with unchanged X/Y',
  'decision':'diagnostic sign(X-Y) accuracy; NOT substitute for XY loss',
  'invariance':'paired paraphrase/row-order agreement; proposed auxiliary objective, not yet wired',
  'strategy':'regret against specified synthetic oracle; evaluation only, not learned equilibrium'},
 'evaluation':{'operand_tolerances':[0.05,0.10], 'strict_orientation_pair':True,
  'metrics':['X_5pct','Y_5pct','joint_XY_5pct','joint_XY_10pct','OP_accuracy','paired_accuracy','decision_accuracy',
             'regret','representation_gap','transfer_to_heldout_jacket','transfer_to_heldout_scenario','retention'],
  'promotion':'measure before activation; no numeric threshold hardwired into trainer'},
 'curriculum':[
  {'phase':'control','variants':['native'], 'aim':'retain native numerical skill'},
  {'phase':'context','variants':['context_numeric','semantic_roles'], 'aim':'add game rules and categorical state without hiding numbers'},
  {'phase':'one_operand','variants':['infer_x','infer_y'], 'aim':'learn exact game/jacket-to-operand mapping'},
  {'phase':'both_operands','variants':['infer_both','raw_story'], 'aim':'perform full inference'},
  {'phase':'transfer','variants':['raw_story'], 'aim':'withheld jackets/scenarios then withheld game types'}],
 'constraints':['do not modify active checkpoints','no source-split contamination',
                'do not present synthetic teachers as observed optimal strategies',
                'track kernel and rubric versions with each task','do not truncate prompts at 256 tokens',
                'legacy rehearsal must stay nonzero after trainer cutover'],
 'allocation_policy':'Probe determines starting frontier; no unvalidated fixed mix or automatic promotion.',
 'withheld_games':['travelers_dilemma','hypergame'],
 'sources':{'local':['nanojev_altreg_attention_curriculum_mix_v4.py','nanojev_captain_numeracy_populate.py',
                     'nanojev_hawk_dove_captains_v1.py','nanojev_hawk_dove_scenarios_v1.py'],
            'catalogs':['https://gambitproject.readthedocs.io/en/stable/pygambit.html',
                        'https://github.com/google-deepmind/open_spiel/blob/master/docs/games.md']}
}


def jacket_preference(captain: Any, state: dict[str,str]) -> int:
    """The existing synthetic Hawk-Dove teacher, explicitly reused as a baseline.

    This is NOT a learned/general game-specific utility. Reusing it across games
    supplies a controlled jacket-sensitive contrast; game-specific calibrations
    are a subsequent experiment.
    """
    total = captain.x_base
    for field, table in captain.x_effects.items():
        total += table[state[field]]
    for k1,v1,k2,v2,delta in captain.x_interactions:
        if state[k1]==v1 and state[k2]==v2:
            total += delta
    return max(0,min(100,total))


def opponent_probability(state: dict[str,str]) -> float:
    """Probability of the first opponent mode, declared *not inferred*."""
    return {'low':0.75,'medium':0.50,'high':0.25}[state['opponent_pressure']]


def oracle(game_id: str, state: dict[str,str]) -> tuple[float,float,dict]:
    """Compute raw A/B values from an auditable game-family mathematical kernel.

    Only `executable` games call this. A reduced one-step decision is not claimed
    to solve the full sequential/incomplete-information game.
    """
    game = next(g for g in GAMES if g[0]==game_id)
    kind=game[3]
    q=opponent_probability(state)
    urgent = state['decision_horizon']=='now'
    supported = state['friendly_support']=='in-range'
    risk=state['crew_danger']=='high'
    if kind=='matrix':
        a,b=MATRICES[game_id]
        return (q*a[0]+(1-q)*a[1],q*b[0]+(1-q)*b[1],
                {'kernel':'matrix','q':q,'A':list(a),'B':list(b),
                 'rule':'A=q*A0+(1-q)*A1; B=q*B0+(1-q)*B1'})
    if kind=='repeated':
        gamma = 0.8 if state['decision_horizon']=='later' else 0.55
        vcoop=65+gamma*70/(1-gamma)
        vdefect=90+gamma*25/(1-gamma)
        return vcoop*(1-gamma),vdefect*(1-gamma),{
            'kernel':kind,'gamma':gamma,'cooperate_stage':65,'cooperate_continuation':70,
            'defect_stage':90,'defect_continuation':25,'rule':'(1-gamma)*immediate+gamma*continuation'}
    if kind=='trust':
        return q*94+(1-q)*16,54,{
            'kernel':kind,'q':q,'reciprocated':94,'betrayed':16,'safe':54,
            'rule':'trust=q*94+(1-q)*16; refuse=54'}
    if kind=='bayes':
        prior = 0.65 if supported else 0.40
        # likelihood given cooperative type is .75; given hostile .25
        num = prior*0.75
        posterior = num/(num+(1-prior)*0.25)
        yes=posterior*86+(1-posterior)*12
        no=58
        return yes,no,{'kernel':kind,'prior':prior,'good_signal_given_coop':0.75,
                     'good_signal_given_hostile':0.25,'posterior':posterior,
                     'rule':'posterior=prior*.75/(prior*.75+(1-prior)*.25); A=posterior*86+(1-posterior)*12; B=58'}
    if kind=='inspection':
        detection=0.80 if supported else 0.45
        attack_prob=1-q
        gain=70*attack_prob*detection
        scan=60+gain-12
        skip=60-70*attack_prob
        return scan,skip,{'kernel':kind,'detection':detection,'threat_probability':attack_prob,
                          'damage':70,'scan_cost':12,
                          'rule':'A=60+p*d*70-12; B=60-p*70'}
    if kind=='blotto':
        weights=(25,35,40)
        enemy=(3,5,2) if state['opponent_support']=='in-range' else (2,3,5)
        A=(4,3,3) if supported else (3,4,3)
        B=(2,5,3) if urgent else (3,3,4)
        def score(vec):
            return sum(w for w,a,b in zip(weights,vec,enemy) if a>b)
        return score(A),score(B),{'kernel':kind,'weights':weights,'enemy':enemy,'A':A,'B':B,
                                 'rule':'sum(weight_i for own_i>enemy_i), ties score 0; sum allocation=10'}
    if kind=='threshold':
        # A is contribute, B free ride; public-goods benefit depends on support.
        threshold = 1 if game_id=='volunteer_dilemma' else 2
        others = 1 if supported else 0
        if game_id=='coalition_formation': threshold=2
        benefit=85 if state['mission_dependency']=='on' else 65
        A=(benefit if 1+others>=threshold else 22)-22
        B=(benefit if others>=threshold else 22)
        return A,B,{'kernel':kind,'threshold':threshold,'others':others,'benefit':benefit,
                    'contribution_cost':22,'rule':'A=benefit_if_threshold_met-22; B=benefit_if_threshold_met_without_self'}
    if kind=='congestion':
        load=9 if state['opponent_pressure']=='high' else (6 if state['opponent_pressure']=='medium' else 3)
        A=90-5*load
        B=73-2*max(0,load-2)
        return A,B,{'kernel':kind,'load':load,'rule':'A=90-5*load; B=73-2*max(0,load-2)'}
    if kind=='attrition':
        duration={'now':1,'soon':3,'later':6}[state['decision_horizon']]
        rate=12 if risk else 6
        value=96 if state['resource_priority']=='high' else 75
        return max(0,value-duration*rate),45,{
            'kernel':kind,'duration':duration,'rate':rate,'value':value,
            'rule':'continue=value-duration*rate; yield=45'}
    if kind=='markov':
        gamma=0.8
        reward_a=42 if risk else 70
        reward_b=58
        successor_a=78 if supported else 30
        successor_b=55
        return (1-gamma)*reward_a+gamma*successor_a,(1-gamma)*reward_b+gamma*successor_b,{
            'kernel':kind,'gamma':gamma,'rewardA':reward_a,'rewardB':reward_b,
            'VA':successor_a,'VB':successor_b,'rule':'Q=(1-gamma)*reward+gamma*successor_value'}
    if kind=='bargain':
        offer=68 if supported else 44
        outside=51 if urgent else 62
        return offer,outside,{'kernel':kind,'offer':offer,'outside':outside,
                              'rule':'accept_offer=offer; reject=outside'}
    if kind=='hyper':
        p_misread=0.75 if state['opponent_escalating']=='on' else 0.25
        A=p_misread*28+(1-p_misread)*87
        B=55
        return A,B,{'kernel':kind,'p_alt_model':p_misread,
                    'payoff_alternate_model':28,'payoff_expected_model':87,'safe':55,
                    'rule':'A=P(alternate)*28+(1-P(alternate))*87; B=55'}
    if kind=='pursuit':
        intercept=0.82 if supported else 0.40
        cost=25 if risk else 10
        A=intercept*90+(1-intercept)*18-cost
        B=50
        return A,B,{'kernel':kind,'intercept':intercept,'risk_cost':cost,
                    'rule':'A=p_intercept*90+(1-p_intercept)*18-risk_cost; B=50'}
    raise ValueError('GAME_NO_EXECUTABLE_ORACLE:'+game_id)


def targets(game_id: str,captain: Any,state: dict[str,str]) -> tuple[int,int,dict]:
    a,b,trace=oracle(game_id,state)
    preference=jacket_preference(captain,state)
    # Synthetic, declared projection; never confuse X with game-theoretic equilibrium.
    x=max(0,min(100,0.75*a+0.25*preference))
    y=max(0,min(100,b))
    to_int=lambda v:int(Decimal(str(v*SCALE)).quantize(Decimal('1'),rounding=ROUND_HALF_EVEN))
    x_i,y_i=to_int(x),to_int(y)
    return x_i,y_i,{**trace,'game_raw_A':a,'game_raw_B':b,
                    'jacket_baseline':preference,'projection':'X=round(20000*(.75*A+.25*jacket_baseline)); Y=round(20000*B)',
                    'teacher_status':'synthetic_provisional_not_optimal_policy'}


def changed_state(scenario: Any, variant: int) -> dict[str,str]:
    values=dict(scenario.default_state)
    if variant not in (0,1): raise ValueError('variant must be 0 or 1')
    if variant==1:
        values['crew_danger']={'low':'high','high':'low','medium':'high'}[values['crew_danger']]
        values['friendly_support']='out-of-range' if values['friendly_support']=='in-range' else 'in-range'
        values['opponent_pressure']={'low':'high','medium':'low','high':'medium'}[values['opponent_pressure']]
    return values


def split_for(captain_id: str,scenario_id: str,game_id: str='') -> tuple[str,str]:
    if game_id in HELDOUT_GAMES:return 'test','withheld_game'
    if scenario_id=='s03':return 'test','withheld_scenario'
    if captain_id=='c08':return 'dev','withheld_jacket_dev'
    if captain_id in ('c09','c10'):return 'test','withheld_jacket_test'
    return 'train','observed_jacket_scenario'


def renderings(game: tuple,captain:Any,scenario:Any,state:dict,x:int,y:int,trace:dict)->dict[str,str]:
    game_id,game_name=game[:2]
    # The rule exposes input parameters but not the computed X/Y target.
    rule=trace['rule']
    inputs={k:v for k,v in trace.items() if k not in ('rule','game_raw_A','game_raw_B','jacket_baseline',
                                                      'projection','teacher_status')}
    setup=(f'GAME: {game_name}. {scenario.context}\n'
           f'Captain: {captain.jacket_short}\n'
           f'State: '+', '.join(f'{k}={v}' for k,v in sorted(state.items()))+'\n'
           f'Rule: {rule}. Inputs: {canonical(inputs)}.\n'
           'Evaluate alternatives A (act) and B (withdraw/other); '
           'captain priorities affect action A. '
           'Use the previously established rubric for this jacket.\n')
    number_x=f'XXXXXX = {x}, {logbar(x)}'
    number_y=f'YYYYYY = {y}, {logbar(y)}'
    op_line='operation: Subtract YYYYYY from XXXXXX.'
    variants={}
    for orientation in ('forward','reordered'):
        def join(*rows):
            front=[t for t in rows if t is not None]
            if orientation=='reordered' and len(front)>=2:
                front[0],front[1]=front[1],front[0]
            return '\n'.join(front)
        v={
          'native':join(number_x,number_y,op_line,'Answer:'),
          'context_numeric':join(number_x,number_y,op_line,setup+'Answer:'),
          'semantic_roles':join(number_x,number_y,
              'XXXXXX is the captain-adjusted value of action A; YYYYYY is the value of action B.',
              op_line,setup+'Answer:'),
          'infer_x':join('XXXXXX = captain-adjusted value of action A',number_y,op_line,setup+'Answer:'),
          'infer_y':join(number_x,'YYYYYY = value of action B',op_line,setup+'Answer:'),
          'infer_both':join('XXXXXX = captain-adjusted value of action A','YYYYYY = value of action B',
                            op_line,setup+'Answer:'),
          'raw_story':((setup if orientation=='forward' else (
                f'GAME: {game_name}. {scenario.context}\n'
                f'Rule: {rule}. Inputs: {canonical(inputs)}.\n'
                f'State: '+', '.join(f'{k}={v}' for k,v in sorted(state.items()))+'\n'
                f'Captain: {captain.jacket_short}\n'
                'Evaluate alternatives A (act) and B (withdraw/other); captain priorities affect action A. '
                'Use the previously established rubric for this jacket.\n'))
              +'Which action would this captain favor according to the game rules and rubric? '
              +'Determine the two numerical action values and their difference.\nAnswer:'),
        }
        for name,prompt in v.items():
            variants[name+'__'+orientation]=prompt
    return variants


DDL = '''
 PRAGMA foreign_keys=ON;
 CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
 CREATE TABLE game_families(family_id TEXT PRIMARY KEY,title TEXT NOT NULL,form TEXT NOT NULL,math_definition TEXT NOT NULL);
 CREATE TABLE games(game_id TEXT PRIMARY KEY,title TEXT NOT NULL,family_id TEXT NOT NULL REFERENCES game_families,
                    kernel TEXT NOT NULL, mathematical_breakdown TEXT NOT NULL,assumptions TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('executable','catalog_only')),aliases TEXT NOT NULL);
 CREATE TABLE mathematical_forms(game_id TEXT PRIMARY KEY REFERENCES games(game_id),form_kind TEXT NOT NULL,
                                 definition TEXT NOT NULL,assumptions TEXT NOT NULL,verification_status TEXT NOT NULL);
 CREATE TABLE game_relationships(source_game TEXT NOT NULL REFERENCES games(game_id),
            target_game TEXT NOT NULL REFERENCES games(game_id),relation TEXT NOT NULL,
            mathematical_condition TEXT NOT NULL,preservation TEXT NOT NULL,
            is_equivalence INTEGER NOT NULL DEFAULT 0 CHECK(is_equivalence IN (0,1)),
            PRIMARY KEY(source_game,target_game,relation),CHECK(source_game<>target_game));
 CREATE TABLE dictionary(term TEXT PRIMARY KEY,definition TEXT NOT NULL,category TEXT NOT NULL);
 CREATE TABLE transformation_types(transformation_id TEXT PRIMARY KEY,from_form TEXT NOT NULL,to_form TEXT NOT NULL,
         claim_type TEXT NOT NULL,rule TEXT NOT NULL,preconditions TEXT NOT NULL,interpretation TEXT NOT NULL);
 CREATE TABLE training_objectives(objective_id TEXT PRIMARY KEY,objective_json TEXT NOT NULL,status TEXT NOT NULL);
 CREATE TABLE curriculum_stages(stage_order INTEGER PRIMARY KEY,stage TEXT UNIQUE NOT NULL,variants_json TEXT NOT NULL,aim TEXT NOT NULL);
 CREATE TABLE captains(captain_id TEXT PRIMARY KEY,title TEXT NOT NULL,jacket TEXT NOT NULL,
                       short_jacket TEXT NOT NULL,rubric TEXT NOT NULL);
 CREATE TABLE scenarios(scenario_id TEXT PRIMARY KEY,title TEXT NOT NULL,context TEXT NOT NULL,question TEXT NOT NULL);
 CREATE TABLE case_groups(case_id TEXT PRIMARY KEY,game_id TEXT NOT NULL REFERENCES games(game_id),
           captain_id TEXT NOT NULL REFERENCES captains(captain_id),
           scenario_id TEXT NOT NULL REFERENCES scenarios(scenario_id),state_variant INTEGER NOT NULL,
           state_json TEXT NOT NULL,split TEXT NOT NULL CHECK(split IN ('train','dev','test')),
           split_reason TEXT NOT NULL,oracle TEXT NOT NULL,label_status TEXT NOT NULL,
           X INTEGER NOT NULL CHECK(X BETWEEN 0 AND 2000000),
           Y INTEGER NOT NULL CHECK(Y BETWEEN 0 AND 2000000), Z INTEGER NOT NULL,
           op TEXT NOT NULL CHECK(op='subtract'),trace_json TEXT NOT NULL,
           UNIQUE(game_id,captain_id,scenario_id,state_variant),CHECK(Z=X-Y));
 CREATE TABLE transformations(case_id TEXT NOT NULL REFERENCES case_groups(case_id),
           transform_id TEXT NOT NULL REFERENCES transformation_types(transformation_id),
           from_data_json TEXT NOT NULL,to_data_json TEXT NOT NULL, verified INTEGER NOT NULL CHECK(verified IN (0,1)),
           PRIMARY KEY(case_id,transform_id));
 CREATE TABLE presentations(case_id TEXT NOT NULL REFERENCES case_groups(case_id),
           variant TEXT NOT NULL,prompt TEXT NOT NULL,prompt_sha256 TEXT NOT NULL,
           PRIMARY KEY(case_id,variant));
 CREATE INDEX idx_games_family ON games(family_id);
 CREATE INDEX idx_cases_game_split ON case_groups(game_id,split);
 CREATE INDEX idx_presentations_variant ON presentations(variant);
 CREATE VIEW training_rows AS SELECT c.case_id,c.game_id,c.captain_id,c.scenario_id,c.state_variant,
           c.split,c.split_reason,c.X,c.Y,c.Z,c.op,c.label_status,p.variant,p.prompt
           FROM case_groups c JOIN presentations p ON p.case_id=c.case_id;
'''


def populate(db_path: Path, *, replace: bool=False) -> dict:
    """Create a complete small seed DB atomically; reject accidental overwrite."""
    db_path=Path(db_path)
    db_path.parent.mkdir(parents=True,exist_ok=True)
    if db_path.exists():
        if not replace:raise FileExistsError('GAME_LIBRARY_OUTPUT_EXISTS:'+str(db_path))
        # Explicit replacement commits only after the new DB passes integrity.
    tmp=db_path.with_suffix(db_path.suffix+'.building')
    if tmp.exists():tmp.unlink()
    try:
        with closing(sqlite3.connect(tmp)) as con:
            con.execute('PRAGMA foreign_keys=ON')
            con.executescript(DDL)
            con.executemany('INSERT INTO game_families VALUES(?,?,?,?)',FAMILIES)
            con.executemany('INSERT INTO games VALUES(?,?,?,?,?,?,?,?)',GAMES)
            con.executemany('INSERT INTO mathematical_forms VALUES(?,?,?,?,?)',
                [(g[0],g[3],g[4],g[5],g[6]) for g in GAMES])
            # Graph of similarities is NEVER implicitly a graph of equivalences.
            for i,a in enumerate(GAMES):
                for b in GAMES[i+1:]:
                    if a[3]==b[3]:
                        con.execute('INSERT INTO game_relationships VALUES(?,?,?,?,?,?)',
                           (a[0],b[0],'shared_kernel',
                            f'both admit a {a[3]} reduced numerical operator',
                            'only the operator schema; neither strategic equilibrium nor payoff identity',0))
            for source,target,relation,condition in (
                ('hawk_dove','chicken','ordinal_family','T>R>S>P in both specified reduced matrices'),
                ('stag_hunt','assurance','ordinal_family','coordination with a risky joint action'),
                ('prisoners_dilemma','repeated_pd','temporal_extension','same strategic temptation embedded in discounted continuation'),
                ('signaling','cheap_talk','information_variant','sender signal has different incentive/commitment semantics'),
                ('colonel_blotto','security_game','allocation_analogy','scarce assets distributed among contested objectives'),
                ('war_attrition','stochastic_game','dynamic_analogy','actions affect valued future state'),
                ('minority','el_farol','population_analogy','payoffs fall when too many agents choose the same option'),
                ('byzantine_coordination','electronic_mail','knowledge_analogy','coordination depends on reliable shared beliefs'),
                ('hypergame','signaling','belief_analogy','opponents may hold incompatible models of the interaction'),
                ('ultimatum','nash_bargaining','bargaining_analogy','offer evaluation and outside options'),
                ('trust_game','prisoners_dilemma','cooperation_analogy','cooperation may be vulnerable to exploitation'),
                ('inspection','pursuit_evasion','security_analogy','decisions under detection and hiding uncertainty'),
            ):
                con.execute('INSERT INTO game_relationships VALUES(?,?,?,?,?,?)',
                   (source,target,relation,condition,
                    'qualitative structural feature only; does not certify mathematical equivalence',0))
            con.executemany('INSERT INTO dictionary VALUES(?,?,?)',DICTIONARY)
            con.executemany('INSERT INTO transformation_types VALUES(?,?,?,?,?,?,?)',TRANSFORMS)
            con.execute('INSERT INTO training_objectives VALUES(?,?,?)',
                        (OBJECTIVE['id'],canonical(OBJECTIVE),OBJECTIVE['status']))
            con.executemany('INSERT INTO curriculum_stages VALUES(?,?,?,?)',
                            [(i,p['phase'],canonical(p['variants']),p['aim']) for i,p in enumerate(OBJECTIVE['curriculum'])])
            con.executemany('INSERT INTO captains VALUES(?,?,?,?,?)',
                [(c.key,c.title,c.jacket,c.jacket_short,c.rubric_principle) for c in CAPTAINS])
            con.executemany('INSERT INTO scenarios VALUES(?,?,?,?)',
                [(s.key,s.title,s.context,s.question) for s in SCENARIOS])
            num=0
            for game in GAMES:
                if game[6]!='executable': continue
                for scenario in SCENARIOS:
                    for variant in (0,1):
                        state=changed_state(scenario,variant)
                        for captain in CAPTAINS:
                            x,y,trace=targets(game[0],captain,state)
                            split,reason=split_for(captain.key,scenario.key,game[0])
                            cid=sha([VERSION,game[0],captain.key,scenario.key,variant])
                            con.execute('''INSERT INTO case_groups VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                                (cid,game[0],captain.key,scenario.key,variant,canonical(state),split,reason,
                                 game[3]+'-v1','provisional',x,y,x-y,OP,canonical(trace)))
                            before={'game':game[0],'state':state,'jacket':captain.key,'inputs':trace}
                            after={'X':x,'Y':y,'Z':x-y,'OP':OP}
                            con.execute('INSERT INTO transformations VALUES(?,?,?,?,?)',
                                (cid,'risk_to_jacket',canonical(before),canonical(after),0))
                            con.execute('INSERT INTO transformations VALUES(?,?,?,?,?)',
                                (cid,'expected_to_ordinal',canonical({'X':x,'Y':y}),canonical({'Z':x-y}),1))
                            prompts=renderings(game,captain,scenario,state,x,y,trace)
                            for v,prompt in prompts.items():
                                con.execute('INSERT INTO presentations VALUES(?,?,?,?)',
                                            (cid,v,prompt,hashlib.sha256(prompt.encode('utf-8')).hexdigest()))
                            # Explicit witnessed representation transformations.
                            witnessed=(
                              ('game_to_pairwise',{'adjusted_A':.75*trace['game_raw_A']+.25*trace['jacket_baseline'],
                                  'B':trace['game_raw_B'],'scale':SCALE,'rounding':'half_even'},
                                  {'X':x,'Y':y},1),
                              ('pairwise_to_registers',{'X':x,'Y':y},
                                  {'native_sha256':hashlib.sha256(prompts['native__forward'].encode()).hexdigest()},1),
                              ('register_to_roles',{'native_sha256':hashlib.sha256(prompts['native__forward'].encode()).hexdigest()},
                                  {'roles_sha256':hashlib.sha256(prompts['semantic_roles__forward'].encode()).hexdigest()},1),
                              ('roles_to_story',{'roles_sha256':hashlib.sha256(prompts['semantic_roles__forward'].encode()).hexdigest()},
                                  {'story_sha256':hashlib.sha256(prompts['raw_story__forward'].encode()).hexdigest()},0),
                              ('story_paraphrase',{'forward_sha256':hashlib.sha256(prompts['raw_story__forward'].encode()).hexdigest()},
                                  {'reordered_sha256':hashlib.sha256(prompts['raw_story__reordered'].encode()).hexdigest()},0),
                              ('orientation_swap',{'forward_sha256':hashlib.sha256(prompts['native__forward'].encode()).hexdigest()},
                                  {'reordered_sha256':hashlib.sha256(prompts['native__reordered'].encode()).hexdigest()},1),
                            )
                            for transform_id,src,dst,is_verified in witnessed:
                                con.execute('INSERT INTO transformations VALUES(?,?,?,?,?)',
                                            (cid,transform_id,canonical(src),canonical(dst),is_verified))
                            if trace['kernel']=='matrix':
                                A,B=trace['A'],trace['B'];q=trace['q']
                                con.execute('INSERT INTO transformations VALUES(?,?,?,?,?)',
                                  (cid,'payoff_to_expected',canonical({'q':q,'A':A,'B':B}),
                                   canonical({'A':trace['game_raw_A'],'B':trace['game_raw_B']}),1))
                            if trace['kernel']=='bayes':
                                con.execute('INSERT INTO transformations VALUES(?,?,?,?,?)',
                                  (cid,'bayes_update',canonical({'prior':trace['prior'],
                                      'good_signal_given_coop':trace['good_signal_given_coop'],
                                      'good_signal_given_hostile':trace['good_signal_given_hostile']}),
                                   canonical({'posterior':trace['posterior']}),1))
                            num+=1
            con.executemany('INSERT INTO metadata VALUES(?,?)',[
                ('schema',VERSION),('generator','tools.nanojev_game_library_v1'),('source_sha256',sha([FAMILIES,GAMES,TRANSFORMS,OBJECTIVE])),
                ('oracle_count',str(num)),('label_warning','Synthetic teaching projections are not optimized game play')])
            con.commit()
            if con.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise RuntimeError('DB_INTEGRITY')
            if con.execute('PRAGMA foreign_key_check').fetchall():raise RuntimeError('DB_FK')
        tmp.replace(db_path)
    except BaseException:
        if tmp.exists():tmp.unlink()
        raise
    return verify(db_path)


def verify(db_path:Path)->dict:
    path=Path(db_path)
    if not path.is_file():
        raise FileNotFoundError('GAME_LIBRARY_CATALOG_NOT_FOUND:' + str(path))
    with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)) as c:
        c.row_factory=sqlite3.Row
        if c.execute('PRAGMA integrity_check').fetchone()[0]!='ok' or c.execute('PRAGMA foreign_key_check').fetchall():
            raise RuntimeError('GAME_LIBRARY_SQL_INTEGRITY')
        if c.execute("SELECT value FROM metadata WHERE key='schema'").fetchone()[0]!=VERSION:
            raise RuntimeError('GAME_LIBRARY_SCHEMA_DRIFT')
        if c.execute('SELECT COUNT(*) FROM case_groups WHERE Z<>X-Y').fetchone()[0]:
            raise RuntimeError('GAME_LIBRARY_TARGET_DRIFT')
        missing=c.execute('''SELECT COUNT(*) FROM case_groups g WHERE
          (SELECT COUNT(*) FROM presentations p WHERE p.case_id=g.case_id)!=14''').fetchone()[0]
        if missing:raise RuntimeError('GAME_LIBRARY_VARIANTS_MISSING:'+str(missing))
        for row in c.execute('SELECT case_id,variant,prompt,prompt_sha256 FROM presentations'):
            if hashlib.sha256(row['prompt'].encode()).hexdigest()!=row['prompt_sha256']:
                raise RuntimeError('GAME_LIBRARY_PROMPT_HASH_DRIFT')
        for row in c.execute('SELECT case_id,game_id,captain_id,scenario_id,state_variant,X,Y,Z,trace_json,state_json FROM case_groups'):
            captain=next(x for x in CAPTAINS if x.key==row['captain_id'])
            state=json.loads(row['state_json'])
            x,y,trace=targets(row['game_id'],captain,state)
            if (x,y,x-y)!=(row['X'],row['Y'],row['Z']) or canonical(trace)!=row['trace_json']:
                raise RuntimeError('GAME_LIBRARY_ORACLE_DRIFT:'+row['case_id'])
        return {'ok':True,'version':VERSION,'games':c.execute('SELECT COUNT(*) FROM games').fetchone()[0],
                'executable_games':c.execute("SELECT COUNT(*) FROM games WHERE status='executable'").fetchone()[0],
                'catalog_only_games':c.execute("SELECT COUNT(*) FROM games WHERE status='catalog_only'").fetchone()[0],
                'families':c.execute('SELECT COUNT(*) FROM game_families').fetchone()[0],
                'game_relationships':c.execute('SELECT COUNT(*) FROM game_relationships').fetchone()[0],
                'dictionary_entries':c.execute('SELECT COUNT(*) FROM dictionary').fetchone()[0],
                'transformation_types':c.execute('SELECT COUNT(*) FROM transformation_types').fetchone()[0],
                'canonical_cases':c.execute('SELECT COUNT(*) FROM case_groups').fetchone()[0],
                'presentations':c.execute('SELECT COUNT(*) FROM presentations').fetchone()[0],
                'by_split':dict(c.execute('SELECT split,COUNT(*) FROM case_groups GROUP BY split').fetchall()),
                'database':str(path)}


def export_task_bank(db_path:Path,dest:Path)->dict:
    """Export without replacing existing Pass-One bank or changing mix_v4.

    This conforms to TrainingTaskStore v1, but is NOT drop-in --captain-bank:
    mix_v4 enforces the Pass-One manifest, reserved operation names and all 15
    Pass-One variants. Integrating this requires an explicit new overlay gate.
    """
    db_path,dest=Path(db_path),Path(dest)
    verify(db_path)
    if dest.exists():raise FileExistsError('GAME_LIBRARY_BANK_EXISTS:'+str(dest))
    dest.parent.mkdir(parents=True,exist_ok=True)
    with closing(sqlite3.connect(db_path)) as src:
        src.row_factory=sqlite3.Row
        with TrainingTaskStore(dest) as dst:
            for split in ('train','dev','test'):
                dst.add_cohort('game-'+split,family=FAMILY,split=split,source={'catalog_version':VERSION})
            variants={}
            for row in src.execute('SELECT * FROM training_rows ORDER BY case_id,variant'):
                variants.setdefault(row['case_id'],{})[row['variant']]={'prompt':row['prompt']}
            for case in src.execute('SELECT * FROM case_groups ORDER BY case_id'):
                cid=case['case_id']
                dst.put(cohort_id='game-'+case['split'],family=FAMILY,
                    task=case['game_id'],split=case['split'],fingerprint=cid,
                    oracle=case['oracle'],label_status=case['label_status'],
                    target={'x':case['X'],'y':case['Y'],'operation':OP,'result':case['Z']},
                    provenance={'game':case['game_id'],'captain':case['captain_id'],
                                'scenario':case['scenario_id'],'split_reason':case['split_reason'],
                                'state':json.loads(case['state_json']),'synthetic':True},
                    variants=variants[cid])
            dst.conn.commit()
            integrity=dst.verify()
            stats=dst.stats()
    return {'ok':True,'task_bank':str(dest),'groups':stats['groups'],
            'presentations':stats['presentations'],'integrity':integrity,
            'mix_v4_drop_in':False,
            'reason':'mix_v4 --captain-bank requires a sealed Pass-One manifest/variants; use explicit future game-bank integration'}
