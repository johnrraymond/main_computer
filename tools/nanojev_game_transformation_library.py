#!/usr/bin/env python3
"""Deterministic SQL game-form/transformation corpus, with verified executable oracles.

No model downloads, no side effects on an existing NanoJev training lineage.
The broader taxonomy is intentionally labelled CATALOG_ONLY when no checked oracle
is implemented; catalog entries never masquerade as supervised ground truth.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import math
from pathlib import Path
import random
import sqlite3
from typing import Any

SCHEMA_VERSION = 'nanojev.game-transform-library.v1'
SPLITS = ('train', 'dev', 'test')
SOURCE_REFS = {
    'gamut': 'https://ai.stanford.edu/~eugnud/papers/gamut.pdf',
    'openspiel': 'https://github.com/google-deepmind/open_spiel/blob/master/docs/games.md',
    'gambit': 'https://www.gambit-project.org/',
    'user': 'Prior NanoJev Space Captain / hawk-dove / game-as-unit conversations',
}

def canon(v: Any) -> str:
    return json.dumps(v, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)

def digest(v: Any) -> str:
    return hashlib.sha256(canon(v).encode('utf8')).hexdigest()

# Mathematical families are axes of generalization, not exclusive boxes.
# The family equation is a representative operator, not a claim that each member
# obeys an identical specialized payoff equation.
FAMILIES = [
 ('matrix','Simultaneous normal form','u_i(a_1,...,a_n)','strategic,simultaneous,complete-information'),
 ('coordination','Coordination and anti-coordination','BR_i(a_-i)=argmax u_i(a_i,a_-i)','strategic,equilibrium-selection'),
 ('social_dilemmas','Cooperation, commons and contribution','u_i=b_i(a)-c_i(a)','externalities,cooperation'),
 ('evolutionary','Population, invasion and evolutionary games','dx/dt=x*(f_H-f_bar)','population,fitness'),
 ('timing','Waiting, preemption, attrition','U_i(t_i,t_-i)=V*Pr(win)-c_i(t_i)','continuous-time,timing'),
 ('bargaining','Negotiation and allocation','max (u_1-d_1)*(u_2-d_2)','sequential,bargaining'),
 ('information','Signaling, screening and Bayesian games','P(theta|signal)=P(signal|theta)*P(theta)/P(signal)','types,beliefs'),
 ('repeated','Repeated games and reputation','V_i=E[sum gamma^t*r_i,t]','repeated,discounting'),
 ('stochastic','Stochastic/Markov games','Q_i(s,a)=r_i(s,a)+gamma*E[V_i(s_prime)]','markov,dynamic'),
 ('differential','Differential and continuous control','dx/dt=f(x,u_1,...,u_n)','continuous,control'),
 ('resource','Resource allocation and contests','sum_j x_i,j <= B_i','allocation,contests'),
 ('security','Security, inspection and interdiction','U_def=-E[loss|allocation]','attacker-defender,inspection'),
 ('spatial','Spatial pursuit, coverage and movement','d_next=norm(x_p_next-x_e_next)','space,reachability'),
 ('network','Network, congestion and routing','C_i(path)=sum_e c_e(load_e)','network,externalities'),
 ('mechanism','Auctions, voting and mechanism design','u_i=v_i(outcome)-payment_i','mechanism,incentives'),
 ('coalition','Coalition formation, alliances and governance','v(S), sum_i x_i=v(N)','coalitional,governance'),
 ('byzantine','Consensus, faults and epistemic games','agree(honest_nodes) under f adversaries','distributed,information'),
 ('deception','Deception, hypergames and unknown models','U_i(a|subjective_game_i)','perception,belief'),
 ('population','Mean-field, swarm and collective games','m_next=F(m,policy)','mean-field,distributed'),
 ('market','Competition, oligopoly and investment','pi_i=(P(Q)-c_i)*q_i','market,equilibrium'),
 ('risk','Robust/adversarial learning games','max_pi min_theta E[U(pi,theta)]','robust,uncertainty'),
 ('captain','Space Captain decision and tactical games','Q_jacket(s,a)=sum_d w_d*phi_d(s,a)','mission,roles,temporal'),
 ('combinatorial','Perfect/incomplete-information board and card games','V(s)=max_a min_b V(T(s,a,b))','search,turn-based'),
]
# Entries here are catalogued with their family's general mathematical form.
# The independently executable subset is registered explicitly below.
CATALOG = {
 'matrix': 'Generic normal-form game|Constant-sum game|Zero-sum game|Random payoff matrix|Polymatrix game|Graphical game|Local-effect game|Uniform local-effect game|Bidirectional local-effect game|Compound game|Shapley stochastic-matrix example',
 'coordination': 'Pure coordination|Stag Hunt|Battle of the Sexes|Assurance game|Hawk–Dove|Chicken|Matching Pennies|Rock Paper Scissors|Dispersion game|Minimum effort game|Common-interest game|Anti-coordination game',
 'social_dilemmas': 'Prisoners Dilemma|Iterated Prisoners Dilemma|Volunteer Dilemma|Public Goods|Threshold Public Goods|Tragedy of Commons|N-player Prisoners Dilemma|Collective-risk social dilemma|Snowdrift game|Collaboration game',
 'evolutionary': 'Evolutionary Hawk–Dove|Replicator Dynamics|Evolutionarily Stable Strategy|Invasion Game|Population Snowdrift|Evolutionary Cooperation',
 'timing': 'War of Attrition|Preemption|Grab the Dollar|Centipede|Timing Duel|Investment Timing|Escalation and Brinkmanship',
 'bargaining': 'Ultimatum|Dictator|Nash Bargaining|Alternating Offers|Rubinstein Bargaining|Holdout|Coalition Bargaining|Negotiation with Outside Option',
 'information': 'Lewis Signaling|Cheap Talk|Costly Signaling|Spence Screening|Bayesian Coordination|Electronic Mail Game|Global Game|Information Cascade|Principal Agent|Adverse Selection|Moral Hazard|Sender Receiver Game',
 'repeated': 'Repeated Cooperation|Reputation|Trust Game|Reciprocity|Trigger Strategy|Folk-Theorem Game|Repeated Inspection|Repeated Arms Race',
 'stochastic': 'Markov Game|Discounted Stochastic Game|Markov Soccer|Stochastic Bargaining|Partial Observable Stochastic Game|Dynamic Security Game|Stochastic Pursuit',
 'differential': 'Differential Pursuit|Continuous-Time Resource Duel|LQ Differential Game|H-infinity Robust Control|Hamilton Jacobi Isaacs Game|Model Predictive Control Game|Lanchester Attrition',
 'resource': 'Colonel Blotto|General Lotto|Tullock Contest|All Pay Auction|Arms Race|Swarm Task Allocation|Shapley Resource Allocation|Budget-Constrained Defense',
 'security': 'Inspection Game|Attacker Defender Game|Interdiction|Security Patrolling|Cyber Defense|Drone Interception|Search and Rescue Competition|Sensor Placement Game|Electronic Warfare Game',
 'spatial': 'Pursuit Evasion|Hide and Seek|Territorial Defense|Coverage Game|Multi Agent Path Finding|Capture the Flag|Search Game|Laser Tag',
 'network': 'Congestion Game|Routing Game|Braess Paradox|Network Formation|Common-Pool Network|Network Interdiction|Network Cascades|Packet Scheduling Game',
 'mechanism': 'First Price Auction|Second Price Auction|Vickrey Auction|Combinatorial Auction|Majority Voting|Voting Agenda|Public Project Mechanism|Median Voter|Matching Market|Traveler Dilemma',
 'coalition': 'Coalition Formation|Shapley Value|Core Allocation|Alliance Prisoners Dilemma|Colluders Council|Quorum Escalation|Three Party Bargaining|Coalition Blocking',
 'byzantine': 'Byzantine Generals|Byzantine Agreement|Proof of Stake Validator Game|Quorum Governance|Sybil Resistance|Consensus with Dishonest Reports|Threshold Escalation',
 'deception': 'Hypergame|Adversarial Signaling|Deceptive Routing|Information Warfare|Disinformation Response|Opponent Model Uncertainty|Bluffing',
 'population': 'Mean Field Game|Population Congestion|Swarm Consensus|El Farol Bar|Minority Game|Market Entry|Collective Herding|Ant Colony Foraging',
 'market': 'Cournot Duopoly|Bertrand Oligopoly|Stackelberg Competition|Price War|Location Game|Hotelling Competition|Common-Value Auction|Market Maker Game',
 'risk': 'Minimax|Distributionally Robust Game|Regret Minimization|Robust Security|Adversarial Bandits|Online Learning Game|Adversarial Self Play|Ambiguity-Averse Bargaining',
 'captain': 'Captain Fuel Reserve|Captain Relative Navigation|Captain Crew Evacuation|Captain Hull Preservation|Captain Mission Commitment|Captain Evidence Discipline|Captain Pursuit|Captain Resource Assignment|Captain Bridge Defense|Captain Coalition Negotiation|Captain Tactical Time Step|Captain Shipboard Consensus|Captain Risk Assessment',
 'combinatorial': 'Chess|Go|Poker Kuhn|Poker Leduc|Goofspiel|Liars Dice|Battleship|Hanabi|Bridge|Tic Tac Toe|Stratego|Reconnaissance Blind Chess|Hex',
}
# Human-facing and math-facing dictionary. Each includes a precise training role.
TERMS = [
 ('game','Parameterized strategic interaction','G=(N,S,A,T,O,I,U,H)','schema'),
 ('player','Agent controlling an action or policy','i in N','entity'),
 ('state','Condition determining possible next transitions','s_t in S','state'),
 ('action','Choice admissible at state and information set','a_i in A_i(s)','action'),
 ('strategy','Map from information history to action distribution','pi_i(a_i|h_i)','policy'),
 ('information_set','Indistinguishable decision histories','I_i(h)','information'),
 ('type','Unobserved payoff, preference or competence parameter','theta_i in Theta_i','information'),
 ('observation','State-dependent measurement accessible to agent','o_i~O_i(.|s)','information'),
 ('belief','Probability distribution over hidden conditions','b_i(theta|h)','information'),
 ('transition','State update conditioned on joint actions','P(s_next|s,a)','dynamics'),
 ('payoff','Utility to a player for an outcome','u_i(s,a)','utility'),
 ('reward','Immediate utility in a state transition','r_i(s,a,s_next)','utility'),
 ('horizon','Number or duration of consequential future decisions','H','temporal'),
 ('discount','Weight on future utility','gamma in [0,1)','temporal'),
 ('best_response','Payoff maximizing move given others','argmax_ai u_i(ai,a_-i)','solution'),
 ('nash_equilibrium','Strategy profile with no profitable unilateral deviation','u_i(a*)>=u_i(ai,a*_-i)','solution'),
 ('pure_nash','Equilibrium with deterministic actions','a* in product_i A_i','solution'),
 ('mixed_nash','Equilibrium involving action distributions','sigma_i in Delta(A_i)','solution'),
 ('pareto_dominance','One outcome weakly helps all and strictly helps some','forall_i u_i(x)>=u_i(y)','solution'),
 ('dominant_strategy','Best action regardless of opponents','forall a_-i: u_i(ai*,a_-i)>=u_i(ai,a_-i)','solution'),
 ('maximin','Maximize lowest possible payoff','max_a min_b U(a,b)','solution'),
 ('minimax','Minimize worst-case loss','min_a max_b L(a,b)','solution'),
 ('regret','Difference between best response utility and chosen utility','R_i=max_ai u_i(ai,a_-i)-u_i(a)','evaluation'),
 ('equilibrium_regret','Largest player gain from unilateral deviation','epsilon=max_i R_i','evaluation'),
 ('role','Semantic assignment of story element to symbolic variable','bind(role,variable)','semantic'),
 ('operand_x','First numeracy operand by semantic role','X','semantic'),
 ('operand_y','Second numeracy operand by semantic role','Y','semantic'),
 ('orientation','Equivalent presentation changing mention order','P_forward <=> P_reversed','representation'),
 ('jacket','Stable Captain decision-preference profile','w_jacket in R^d','captain'),
 ('mission','Longer-horizon objective constraining decisions','M(s,a)','captain'),
 ('ship_preservation','Preference for retained ship integrity','phi_ship(s,a)','captain'),
 ('crew_loyalty','Preference for crew survival and trust','phi_crew(s,a)','captain'),
 ('risk_tolerance','Preference for uncertainty versus safer options','rho','captain'),
 ('initiative','Preference for first action and opportunistic timing','phi_initiative(s,a)','captain'),
 ('patience','Preference for later versus immediate payoff','gamma or time_weight','captain'),
 ('adaptability','Value assigned to revising a plan after evidence','phi_adapt(s,a)','captain'),
 ('evidence_discipline','Evidence threshold before consequential action','tau_evidence','captain'),
 ('payoff_matrix','Tensor of utility by joint action','U[a1,a2,i]','representation'),
 ('extensive_form','Decision tree including observations and contingencies','(nodes,edges,info,terminal_U)','representation'),
 ('markov_game','Dynamic multi-agent state/action/reward model','(S,A,P,r,gamma)','representation'),
 ('normal_form','A payoff table over complete strategies','(N,A,U)','representation'),
 ('replicator','Population growth proportional to fitness advantage','xdot=x*(f-fbar)','solution'),
 ('resource_budget','Aggregate spending constraint','sum_j x_j<=B','constraint'),
 ('externality','Payoff dependence on other players actions','partial u_i/partial a_j','mechanism'),
 ('coalition','Subset of agents that coordinate choices','C subset N','coalition'),
 ('credible_commitment','Action whose future incentive is consistent','sequentially rational policy','mechanism'),
 ('signaling','Message strategically chosen under hidden types','P(m|theta)','information'),
 ('inspection','Detection contest between inspector and violator','P(detect|patrol,attack)','security'),
 ('blotto','Budget allocation contest across battlefields','sum_j x_ij<=B_i','resource'),
 ('byzantine_fault','Arbitrary deviating node behavior','fault_set F, |F|<=f','byzantine'),
 ('quorum','Decision threshold over participants','votes>=q','byzantine'),
 ('hypergame','Game perceptions that differ between players','G_i != G_j','deception'),
 ('information_reveal','Operation that changes observation/belief channel','O -> O_prime','transform'),
 ('action_restriction','Operation that removes admissible choice','A_i -> A_i subset','transform'),
 ('action_expansion','Operation adding a strategic option','A_i -> A_i union {a_new}','transform'),
 ('payoff_affine','Positive payoff rescale and shift for one player','U_i_prime=k_i*U_i+b_i, k_i>0','transform'),
 ('strategy_relabel','Permutation of a player action labels','A_i -> pi(A_i)','transform'),
 ('player_swap','Permutation of agents and their payoffs','U_prime[a2,a1,0]=U[a1,a2,1]','transform'),
 ('counterfactual','New game after explicit parameter perturbation','G_prime=T(G,delta)','transform'),
 ('game_isomorphism','Bijection of strategies/players preserving strategic structure','G ~ G_prime','transform'),
 ('functional_equivalence','Same outputs under stated domain constraints','f(x)=g(x) for x in D','transform'),
 ('structural_invariant','Property conserved under a permitted mapping','I(G)=I(T(G))','transform'),
 ('operator_composition','Sequential application of transformation operators','T2(T1(G))','transform'),
 ('held_out_family','Evaluation game structure withheld from training','family_id not in train','evaluation'),
 ('held_out_instance','Novel parameters for a seen mathematical family','params not in train','evaluation'),
 ('paired_joint_accuracy','Both operands pass in both semantically equivalent presentations','all_o all_xy relative_error<=tol','evaluation'),
 ('threshold_5pct','Relative error acceptance band','abs(pred-y)/max(1,abs(y))<=0.05','evaluation'),
 ('threshold_10pct','Relaxed relative error acceptance band','abs(pred-y)/max(1,abs(y))<=0.10','evaluation'),
 ('transformation_validity','Does target structurally follow the operator and premises?','oracle(T,G)==G_prime','evaluation'),
 ('equilibrium_preservation','Pure equilibrium mapped or unchanged where proved','NE(T(G))=T(NE(G))','evaluation'),
 ('incentive_change','Impact on unilateral best-response payoffs','delta_BR=BR(G_prime)-BR(G)','evaluation'),
 ('identifiability','Whether observations discriminate latent strategic state','P(o|s1) != P(o|s2)','evaluation'),
 ('split_reservation','All renderings/counterfactuals of same base case share split','split(group_id) fixed','evaluation'),
]

TRANSFORMS = [
 ('swap_players','isomorphism','exchange both player identities, actions and utility channels','matrix','preserve_equilibria','executable'),
 ('permute_row','isomorphism','rename both of the row player actions','matrix','map_equilibria','executable'),
 ('permute_column','isomorphism','rename both of the column player actions','matrix','map_equilibria','executable'),
 ('positive_affine','strategic_equivalence','U1 -> 2 U1 + 3 and U2 -> 3 U2 - 2','matrix','preserve_equilibria','executable'),
 ('shared_payoff_shift','strategic_equivalence','add 5 to all utilities','matrix','preserve_equilibria','executable'),
 ('change_one_payoff','game_mutation','change one action-profile payoff for player 1 by +2','matrix','recompute_equilibria','executable'),
 ('reverse_payoff_sign','game_mutation','negate all utility for player 1; usually changes incentives','matrix','recompute_equilibria','executable'),
 ('increase_stakes','game_mutation','double only hawk-hawk conflict cost through a new game instance','parameterized','recompute_equilibria','catalog_only'),
 ('reveal_type','information','make an unobserved type public and recompute posterior policy','bayesian','change_information','catalog_only'),
 ('hide_observation','information','remove an observation channel from one player','markov','change_information','catalog_only'),
 ('new_action','option_set','add an admissible action to player action set','general','change_options','catalog_only'),
 ('remove_action','option_set','remove an available move, with side effects on strategic possibilities','general','change_options','catalog_only'),
 ('coalition_merge','coalition','replace two separately optimizing agents with joint objective','coalitional','change_actors','catalog_only'),
 ('defect_to_cooperate','strategy','change chosen policy, holding game payoffs fixed','matrix','change_policy','catalog_only'),
 ('repeat_game','temporal','embed one-shot game in discounted sequence','repeated','change_horizon','catalog_only'),
 ('commitment','temporal','bind a player future response before opponent chooses','extensive','change_timing','catalog_only'),
 ('resource_constraint','physical','restrict strategies to resource-feasible allocations','resource','change_feasibility','catalog_only'),
 ('observation_noise','physical','distort observed signals under stochastic sensor errors','dynamic','change_information','catalog_only'),
 ('relocate_units','spatial','change physically reachable positions in tactical game','captain','change_reachability','catalog_only'),
 ('jacket_reweight','captain','change priorities while keeping ship state unchanged','captain','change_utility','catalog_only'),
 ('option_manipulation','metagame','alter opponents feasible choices, constraints, coalitions or incentives','general','change_game','catalog_only'),
 ('semantic_paraphrase','representation','change words while preserving X/Y meanings and game structure','text','preserve_targets','executable'),
 ('swap_mentions','representation','reverse description order without changing operand roles','text','preserve_targets','executable'),
 ('scalar_parameter_shift','counterfactual','change one scalar model parameter and recalculate actual oracle','scalar','recompute_outputs','executable'),
 ('compose_transformations','composition','run an operator and its inverse and check identity','matrix','preserve_source','executable'),
]

OBJECTIVE = {
 'id':'nanojev_games_transformation_supervision_v1',
 'scope':'game as unit: math/structural equivalence, changed-games counterfactuals, Captain scenario and tactical games',
 'mathematical_state':'G=(N,S,A,T,O,I,U,H); physically realizable state/action constraints precede strategic analysis',
 'model_initialization':'Continue current frozen Qwen + numeric X/Y attention head checkpoint; new structured multi-field targets require a separately trainable transformation head/adapter.',
 'inputs':['canonical game G=(N,S,A,T,O,I,U,H)', 'parameter instantiation','requested transform T','orientation/paraphrase','jacket and scenario when applicable'],
 'targets':{
  'identify':['game family','roles and state variables','players/actions','constraints','observation and hidden type','time horizon'],
  'execute':['operator id','operator inputs','actual transformed payoff tensor / numeric output','counterfactual deltas','transformation preconditions'],
  'reason':['best responses','pure equilibria where finite matrix','changes in equilibria','invariants vs non-invariants','equivalence certificate'],
  'transfer':['same underlying game under new story','different games with same math structure','modified action/information/utility structures'],
  'capture':['canonical operands X,Y for existing numerical head','full structured game transform for additional head'],
 },
 'mix_policy': {'composition':'explicit fixed per-family distribution; never transition by training step', 'batch_size':16, 'paired_orientations':True},
 'readiness': {'joint_paired_10pct_target':0.90, 'numeric_retention_5pct_target':0.90, 'transformation_operator_accuracy_target':0.95, 'structure_exact_match_target':0.90, 'cross_family_held_out_report_required':True},
 'loss_weights_for_new_transformation_head':{'xy_numeric':0.20,'transformed_numeric':0.20,'operator':0.10,'structure':0.15,'equilibrium':0.15,'paired_consistency':0.10,'counterfactual_contrast':0.10},
 'losses':{
  'operand_sign_exponent_mantissa':'existing v3 supervision on X and Y where defined',
  'numeric_transformation':'smooth regression to checked post-transform output and delta',
  'operator_classification':'cross-entropy for transformation id',
  'structural_fields':'structured categorical/sequence objective on actors, actions, conditions, and invariants',
  'equilibrium':'set-exact matching or regret-weighted loss on checked matrix games',
  'paired_consistency':'both orientations must independently predict the SAME canonical target',
  'counterfactual_contrast':'distinguish equivalence-preserving maps from incentive-changing maps',
 },
 'validation':{
  'strict_paired_joint_thresholds':[0.01,0.05,0.10],
  'structure_exact_match':True,'operation_exact_match':True,
  'numeric_no_oracle_contamination':True,
  'matrix_payoff_exact_match':True,
  'equilibria_verify_with_exhaustive_best_response':True,
  'held_out':['parameter instance','surface frame','orientation','game family where feasible','operator composition'],
  'report':['per_family','per_operator','per_representation','numeric X/Y','structure','invariant','counterfactual','coverage'],
 },
 'scheduling':{'unit':'game family and operator','strategy':'explicit user-selected distribution, not step milestones'}
}

# Exact payoff tables U[row_action][column_action] = [u_row,u_column].
# They are illustrative parameter choices, not claims of a unique canonical
# cardinal payoff for every named game.
MATRICES = {
 'prisoners_dilemma':('Prisoners Dilemma','social_dilemmas',['Cooperate','Defect'],['Cooperate','Defect'],[[[3,3],[0,5]],[[5,0],[1,1]]]),
 'hawk_dove':('Hawk–Dove','coordination',['Hawk','Dove'],['Hawk','Dove'],[[[-2,-2],[6,0]],[[0,6],[3,3]]]),
 'chicken':('Chicken','coordination',['Swerve','Straight'],['Swerve','Straight'],[[[0,0],[-1,2]],[[2,-1],[-5,-5]]]),
 'stag_hunt':('Stag Hunt','coordination',['Stag','Hare'],['Stag','Hare'],[[[4,4],[0,3]],[[3,0],[3,3]]]),
 'battle_of_sexes':('Battle of the Sexes','coordination',['Opera','Football'],['Opera','Football'],[[[3,2],[0,0]],[[0,0],[2,3]]]),
 'matching_pennies':('Matching Pennies','coordination',['Heads','Tails'],['Heads','Tails'],[[[1,-1],[-1,1]],[[-1,1],[1,-1]]]),
 'coordination':('Pure coordination','coordination',['A','B'],['A','B'],[[[2,2],[0,0]],[[0,0],[3,3]]]),
 'anti_coordination':('Anti-coordination','coordination',['A','B'],['A','B'],[[[0,0],[3,3]],[[3,3],[0,0]]]),
 'volunteer':('Volunteer Dilemma','social_dilemmas',['Volunteer','Wait'],['Volunteer','Wait'],[[[2,2],[2,5]],[[5,2],[0,0]]]),
 'simple_inspection':('Inspection Game','security',['Inspect','Ignore'],['Violate','Comply'],[[[2,-2],[-1,1]],[[-4,4],[0,0]]]),
 'arms_race':('Arms Race','resource',['Arm','Disarm'],['Arm','Disarm'],[[[-1,-1],[3,-4]],[[-4,3],[2,2]]]),
 'captain_ship_defense':('Captain Hull Preservation','captain',['Intercept','Evade'],['Raid','Withdraw'],[[[3,-2],[-2,0]],[[-4,3],[2,2]]]),
 'captain_evidence':('Captain Evidence Discipline','captain',['Act','Wait'],['Threat','False alarm'],[[[4,-2],[-3,1]],[[-5,4],[2,0]]]),
 'zero_sum_duel':('Zero-sum game','matrix',['Attack','Guard'],['Attack','Guard'],[[[3,-3],[-2,2]],[[-1,1],[1,-1]]]),
}
SCALAR_MODELS = {
 # id: (catalog name, family, formula as descriptive text, parameter base, changed param, amount)
 'hawk_dove_mixed':('Evolutionary Hawk–Dove','evolutionary','p_H=V/C for 0<V<C',{'V':6.0,'C':10.0},'C',2.0),
 'nash_bargain_split':('Nash Bargaining','bargaining','x1=d1+(V-d1-d2)/2',{'V':20.,'d1':2.,'d2':4.},'V',4.),
 'bayes_posterior':('Bayesian Coordination','information','P(type|signal)=prior*l_true/(prior*l_true+(1-prior)*l_false)',{'prior':.35,'l_true':.8,'l_false':.2},'prior',.05),
 'discounted_repeat':('Repeated Cooperation','repeated','V=r/(1-gamma)',{'r':3.,'gamma':.7},'gamma',.05),
 'markov_one_step':('Markov Game','stochastic','Q=r+gamma*(p*v_good+(1-p)*v_bad)',{'r':2.,'gamma':.8,'p':.4,'v_good':7.,'v_bad':1.},'p',.1),
 'lanchester_square':('Lanchester Attrition','differential','advantage=A^2-B^2',{'A':12.,'B':9.},'A',2.),
 'blotto_remaining':('Colonel Blotto','resource','remaining=B-x1-x2',{'B':30.,'x1':8.,'x2':9.},'B',4.),
 'inspection_loss':('Inspection Game','security','expected_loss=attack_probability*(1-detection_probability)*damage',{'attack_probability':.5,'detection_probability':.6,'damage':100.},'detection_probability',.1),
 'pursuit_gap':('Pursuit Evasion','spatial','gap_next=gap+evader_speed-pursuer_speed',{'gap':15.,'evader_speed':5.,'pursuer_speed':7.},'pursuer_speed',1.),
 'routing_delay':('Routing Game','network','delay=base+congestion_coefficient*flow',{'base':5.,'congestion_coefficient':.4,'flow':10.},'flow',4.),
 'second_price_utility':('Second Price Auction','mechanism','winner_utility=value-second_price',{'value':30.,'second_price':20.},'second_price',4.),
 'coalition_surplus':('Coalition Formation','coalition','surplus=v_merged-v_one-v_two',{'v_merged':22.,'v_one':8.,'v_two':11.},'v_merged',5.),
 'quorum_margin':('Quorum Governance','byzantine','margin=honest_votes-quorum',{'honest_votes':4.,'quorum':3.},'quorum',1.),
 'population_signal':('Population Congestion','population','utility=benefit-cost_per_agent*n',{'benefit':12.,'cost_per_agent':.5,'n':10.},'n',2.),
 'cournot_quantity':('Cournot Duopoly','market','q_i=(a-c)/(3*b) for symmetric interior Cournot',{'a':100.,'c':10.,'b':2.},'c',5.),
 'robust_minimum':('Minimax','risk','minimax=max(loss_case_1,loss_case_2)',{'loss_case_1':9.,'loss_case_2':12.},'loss_case_1',5.),
 'captain_fuel_reserve':('Captain Fuel Reserve','captain','remaining=initial-reserve_required-fuel_spent',{'initial':100.,'reserve_required':20.,'fuel_spent':35.},'fuel_spent',5.),
 'captain_crew_risk':('Captain Crew Evacuation','captain','expected_saved=crew_count*(1-casualty_probability)',{'crew_count':60.,'casualty_probability':.1},'casualty_probability',.05),
 'captain_signed_offset':('Captain Relative Navigation','captain','relative=ship_offset-beacon_offset',{'ship_offset':18.,'beacon_offset':-9.},'ship_offset',3.),
}

def scalar_oracle(game: str, p: dict[str,float]) -> float:
    if game=='hawk_dove_mixed':
        if not 0<p['V']<p['C']: raise ValueError('mixed interior requires 0<V<C')
        return p['V']/p['C']
    if game=='nash_bargain_split': return p['d1']+(p['V']-p['d1']-p['d2'])/2
    if game=='bayes_posterior':
        d=p['prior']*p['l_true']+(1-p['prior'])*p['l_false']
        return p['prior']*p['l_true']/d
    if game=='discounted_repeat': return p['r']/(1-p['gamma'])
    if game=='markov_one_step':return p['r']+p['gamma']*(p['p']*p['v_good']+(1-p['p'])*p['v_bad'])
    if game=='lanchester_square':return p['A']**2-p['B']**2
    if game=='blotto_remaining':
        if p['x1']+p['x2']>p['B']:raise ValueError('infeasible Blotto allocation')
        return p['B']-p['x1']-p['x2']
    if game=='inspection_loss':return p['attack_probability']*(1-p['detection_probability'])*p['damage']
    if game=='pursuit_gap':return p['gap']+p['evader_speed']-p['pursuer_speed']
    if game=='routing_delay':return p['base']+p['congestion_coefficient']*p['flow']
    if game=='second_price_utility':return p['value']-p['second_price']
    if game=='coalition_surplus':return p['v_merged']-p['v_one']-p['v_two']
    if game=='quorum_margin':return p['honest_votes']-p['quorum']
    if game=='population_signal':return p['benefit']-p['cost_per_agent']*p['n']
    if game=='cournot_quantity':return (p['a']-p['c'])/(3*p['b'])
    if game=='robust_minimum':return max(p['loss_case_1'],p['loss_case_2'])
    if game=='captain_fuel_reserve':return p['initial']-p['reserve_required']-p['fuel_spent']
    if game=='captain_crew_risk':return p['crew_count']*(1-p['casualty_probability'])
    if game=='captain_signed_offset':return p['ship_offset']-p['beacon_offset']
    raise KeyError(game)

def pure_nash(matrix: list) -> list[list[int]]:
    nr=len(matrix); nc=len(matrix[0]); found=[]
    for r in range(nr):
        for c in range(nc):
            a,b=matrix[r][c]
            if a>=max(matrix[rr][c][0] for rr in range(nr))-1e-10 and \
               b>=max(matrix[r][cc][1] for cc in range(nc))-1e-10:
                found.append([r,c])
    return found

def matrix_transform(matrix: list, op: str) -> list:
    if op=='swap_players':return [[[matrix[c][r][1],matrix[c][r][0]] for c in range(len(matrix))] for r in range(len(matrix[0]))]
    if op=='permute_row':return list(reversed(matrix))
    if op=='permute_column':return [list(reversed(row)) for row in matrix]
    if op=='positive_affine':return [[[2*u+3,3*v-2] for u,v in row] for row in matrix]
    if op=='shared_payoff_shift':return [[[u+5,v+5] for u,v in row] for row in matrix]
    if op=='change_one_payoff':
        ans=[[list(cell) for cell in row] for row in matrix]
        ans[0][0][0]+=2
        return ans
    if op=='reverse_payoff_sign':return [[[-u,v] for u,v in row] for row in matrix]
    if op=='compose_transformations':return list(reversed(list(reversed(matrix))))
    raise KeyError(op)

def expected_eq_mapping(eq: list[list[int]], op: str) -> list[list[int]]:
    if op=='swap_players':return sorted([[c,r] for r,c in eq])
    if op=='permute_row':return sorted([[1-r,c] for r,c in eq])
    if op=='permute_column':return sorted([[r,1-c] for r,c in eq])
    return sorted(eq)

SCHEMA_SQL = '''
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS source_references(ref_id TEXT PRIMARY KEY,url TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS families(family_id TEXT PRIMARY KEY,label TEXT NOT NULL,math TEXT NOT NULL,tags TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS games(game_id TEXT PRIMARY KEY,family_id TEXT NOT NULL REFERENCES families(family_id),name TEXT NOT NULL,
   math TEXT NOT NULL,status TEXT NOT NULL CHECK(status IN ('catalog_only','oracle_verified')),
   provenance TEXT NOT NULL,representation TEXT NOT NULL,parameters_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS dictionary(term_id TEXT PRIMARY KEY,definition TEXT NOT NULL,math TEXT NOT NULL,role TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS transformations(op_id TEXT PRIMARY KEY,kind TEXT NOT NULL,definition TEXT NOT NULL,
   domain TEXT NOT NULL,claim TEXT NOT NULL,status TEXT NOT NULL CHECK(status IN ('catalog_only','executable')));
CREATE TABLE IF NOT EXISTS game_relations(left_game TEXT NOT NULL REFERENCES games(game_id),right_game TEXT NOT NULL REFERENCES games(game_id),
   relation TEXT NOT NULL,conditions TEXT NOT NULL,PRIMARY KEY(left_game,right_game,relation));
CREATE TABLE IF NOT EXISTS training_objectives(objective_id TEXT PRIMARY KEY,full_objective_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS training_mix(mix_id TEXT NOT NULL, family_id TEXT NOT NULL REFERENCES families(family_id),
  examples_per_batch INTEGER NOT NULL CHECK(examples_per_batch>0),
  purpose TEXT NOT NULL, PRIMARY KEY(mix_id,family_id));
CREATE TABLE IF NOT EXISTS instances(instance_id TEXT PRIMARY KEY,game_id TEXT NOT NULL REFERENCES games(game_id),
   split TEXT NOT NULL CHECK(split IN ('train','dev','test')),kind TEXT NOT NULL,
   parameters_json TEXT NOT NULL,source_json TEXT NOT NULL,oracle_json TEXT NOT NULL,group_hash TEXT NOT NULL UNIQUE);
CREATE TABLE IF NOT EXISTS transformation_cases(case_id TEXT PRIMARY KEY,instance_id TEXT NOT NULL REFERENCES instances(instance_id),
   op_id TEXT NOT NULL REFERENCES transformations(op_id),split TEXT NOT NULL CHECK(split IN ('train','dev','test')),
   input_json TEXT NOT NULL,target_json TEXT NOT NULL,oracle_status TEXT NOT NULL CHECK(oracle_status='verified'));
CREATE TABLE IF NOT EXISTS presentations(presentation_id TEXT PRIMARY KEY,case_id TEXT NOT NULL REFERENCES transformation_cases(case_id),
   orientation TEXT NOT NULL,prompt TEXT NOT NULL,target_json TEXT NOT NULL,
   UNIQUE(case_id,orientation));
CREATE INDEX IF NOT EXISTS idx_cases_split ON transformation_cases(split,op_id);
CREATE INDEX IF NOT EXISTS idx_instances_family ON instances(game_id,split);
CREATE INDEX IF NOT EXISTS idx_presentations_case ON presentations(case_id);
CREATE VIEW IF NOT EXISTS training_view AS SELECT c.case_id,c.split,i.game_id,c.op_id,p.orientation,p.prompt,p.target_json
   FROM transformation_cases c JOIN instances i ON c.instance_id=i.instance_id
   JOIN presentations p ON p.case_id=c.case_id;
'''

def family_game_id(name: str) -> str:
    import re
    return re.sub(r'[^a-z0-9]+','_',name.casefold()).strip('_')

def split_for(group: str, seed: int) -> str:
    # all operators and orientations of a base instance share its split
    k=int(digest(['split',seed,group])[:8],16)%10
    return 'train' if k<8 else ('dev' if k==8 else 'test')

def insert_case(cn, instance_id, split, op, source, target, game_id, *, frame='strategic') -> str:
    case_id=digest(['game_transform',instance_id,op])
    cn.execute('INSERT INTO transformation_cases VALUES (?,?,?,?,?,?,?)',
       (case_id,instance_id,op,split,canon(source),canon(target),'verified'))
    target_json=canon(target)
    if frame=='matrix':
        instr=f"Game {game_id}. Two players choose row and column actions. The payoff tensor U[row][column]=[row_utility,column_utility] is {canon(source['U'])}."
        task=f"Apply operation {op} ({TRANSFORM_LOOKUP[op]}). Return the transformed payoff tensor, pure Nash equilibria, and whether equilibria are preserved."
        math=f"G.U={canon(source['U'])}; T={op}; output matrix and pure best-response fixed points."
    else:
        instr=f"Game {game_id}. Parameters {canon(source['parameters'])}. Current oracle value {source['value']:.9g}."
        task=f"Apply scalar parameter change: {source['changed']} increases by {source['increment']:.9g}. Compute the new oracle value and difference."
        math=f"f(params)={source['formula']}; params={canon(source['parameters'])}; T={op}; compute f(T(params))."
    for order,prompt in (
        ('forward',instr+'\n'+task+'\nAnswer:'),
        ('reordered',task+'\n'+instr+'\nAnswer:'),
        ('symbolic',math+'\nAnswer:')):
        cn.execute('INSERT INTO presentations VALUES (?,?,?,?,?)',
            (digest(['presentation',case_id,order]),case_id,order,prompt,target_json))
    return case_id

TRANSFORM_LOOKUP={t[0]:t[2] for t in TRANSFORMS}

def build(path: Path, seed: int=20261015, cases_per_game: int=16, *, replace: bool=False) -> dict:
    path=Path(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    if path.exists():
        if not replace:raise FileExistsError(f'Existing corpus not overwritten: {path}')
        path.unlink()
    cn=sqlite3.connect(path)
    cn.execute('PRAGMA foreign_keys=ON')
    try:
        cn.executescript(SCHEMA_SQL)
        cn.execute('INSERT INTO meta VALUES (?,?)',('schema_version',SCHEMA_VERSION))
        cn.execute('INSERT INTO meta VALUES (?,?)',('seed',str(seed)))
        cn.execute('INSERT INTO meta VALUES (?,?)',('cases_per_game',str(cases_per_game)))
        cn.executemany('INSERT INTO source_references VALUES (?,?)',sorted(SOURCE_REFS.items()))
        cn.executemany('INSERT INTO families VALUES (?,?,?,?)',FAMILIES)
        cn.executemany('INSERT INTO dictionary VALUES (?,?,?,?)',TERMS)
        cn.executemany('INSERT INTO transformations VALUES (?,?,?,?,?,?)',TRANSFORMS)
        cn.execute('INSERT INTO training_objectives VALUES (?,?)',(OBJECTIVE['id'],canon(OBJECTIVE)))
        cn.executemany('INSERT INTO training_mix VALUES (?,?,?,?)', [
            ('game_transform_bridge_v1','captain',3,'ship state, mission and tactical numeric transformation'),
            ('game_transform_bridge_v1','coordination',3,'Hawk–Dove/Chicken and equilibrium equivalence'),
            ('game_transform_bridge_v1','social_dilemmas',2,'dilemma payoffs, cooperation constraints'),
            ('game_transform_bridge_v1','information',1,'Bayesian belief transformation'),
            ('game_transform_bridge_v1','resource',1,'resource constraints and contests'),
            ('game_transform_bridge_v1','security',1,'inspection and attacker-defender outcomes'),
            ('game_transform_bridge_v1','network',1,'congestion and routing cost'),
            ('game_transform_bridge_v1','market',1,'Cournot response'),
            ('game_transform_bridge_v1','coalition',1,'coalition value change'),
            ('game_transform_bridge_v1','risk',1,'robust adversarial payoff'),
            ('game_transform_bridge_v1','evolutionary',1,'population equilibrium from Hawk–Dove')
        ])
        family_of={family_game_id(n):(f,n,m) for f,entries in CATALOG.items() for n in entries.split('|') for m in [next(row[2] for row in FAMILIES if row[0]==f)]}
        # If different broad categories use the same name, keep the first and
        # add the additional family link as a catalog relation in a later version.
        for gid,(fam,n,eq) in sorted(family_of.items()):
            cn.execute('INSERT INTO games VALUES (?,?,?,?,?,?,?,?)',
                 (gid,fam,n,eq,'catalog_only',fam,'family_math_reference','{}'))
        # Specialized generator IDs have executable oracle and parameter schema.
        for gid,(name,fam,rows,cols,matrix) in MATRICES.items():
            cn.execute('INSERT OR REPLACE INTO games VALUES (?,?,?,?,?,?,?,?)',
                (gid,fam,name,'U[row_action][col_action][player]', 'oracle_verified','user+gamut+openspiel','matrix',canon({'row_actions':rows,'column_actions':cols,'base_U':matrix})))
        for gid,(name,fam,formula,params,changed,increment) in SCALAR_MODELS.items():
            cn.execute('INSERT OR REPLACE INTO games VALUES (?,?,?,?,?,?,?,?)',
                (gid,fam,name+' (scalar)',formula,'oracle_verified','user+math','scalar',canon(params)))
        links=[('hawk_dove','chicken','same_preference_order',
               'Hawk–Dove/Chicken have corresponding anti-coordination ordinal incentives under an appropriate relabeling; not equal cardinal payoffs.'),
               ('prisoners_dilemma','iterated_prisoners_dilemma','one_shot_vs_repeated',
                'Repeated PD adds horizon and contingent histories; not generally strategically equivalent.'),
               ('hawk_dove','hawk_dove_mixed','normal_form_to_population',
                'For payoff matrix with resource V and conflict cost C>V, symmetric mixed Hawk share is V/C.'),
               ('captain_ship_defense','captain_hull_preservation','tactical_instantiation',
                'Illustrative two-action specialization; no empirical tactical outcome claim.'),
               ('captain_signed_offset','captain_relative_navigation','scalar_abstraction',
                'Relative displacement is the subtraction operator on signed offsets.'),
               ('stag_hunt','assurance_game','same_coordination_family',
                'Assurance and Stag Hunt are coordination games; payoff order and risk dominance depend on parameters.'),
               ('public_goods','tragedy_of_commons','externality_dual',
                'Contribution and extraction are two externality mechanisms; payoff functions are not identical.'),
               ('colonel_blotto','general_lotto','budget_constraint_variant',
                'Blotto uses per-realization allocations under hard budget; Lotto may constrain expected allocations.'),
               ('war_of_attrition','escalation_and_brinkmanship','timing_analogy',
                'Both involve costly waiting/escalation but state and payoff mechanisms can differ.'),
               ('routing_game','congestion_game','network_specialization',
                'A routing game is a congestion model over network paths with edge cost functions.'),
               ('braess_paradox','congestion_game','network_counterexample',
                'A network change may increase equilibrium total travel cost; requires a specified network.'),
               ('byzantine_generals','quorum_governance','distributed_agreement_analogy',
                'Fault-tolerant agreement and governance voting share quorum structures under different assumptions.'),
               ('captain_pursuit','pursuit_evasion','tactical_specialization',
                'Ship pursuit is one physical state/action instance of pursuit-evasion.'),
               ('captain_resource_assignment','colonel_blotto','allocation_analogy',
                'Both allocate scarce resources but mission utility and battle payoff rules can differ.'),
               ('captain_crew_evacuation','captain_crew_risk','utility_to_expectation',
                'Expected survivor count is one observable consequence of a crew-protection objective.'),
               ('captain_risk_assessment','minimax','robust_decision_analogy',
                'A pessimistic objective may be evaluated using worst-case loss.'),
               ('captain_coalition_negotiation','coalition_formation','coalitional_specialization',
                'Shipboard alliance negotiation inherits coalition and preference constraints.'),
               ('captain_ship_defense','attacker_defender_game','security_specialization',
                'Tactical attacker defender payoff instance; illustrative, not real combat data.'),
               ('cournot_duopoly','bertrand_oligopoly','choice_variable_change',
                'Cournot competition chooses quantity; Bertrand chooses price; not strategically equivalent.'),
               ('cheap_talk','costly_signaling','message_cost_mutation',
                'Signal cost changes incentives and may change equilibrium informativeness.'),
               ('principal_agent','moral_hazard','hidden_action_specialization',
                'Moral hazard is a principal-agent problem with nonobservable action or effort.'),
               ('first_price_auction','second_price_auction','payment_rule_mutation',
                'Different winner payment rules alter bidding incentives.'),
               ('poker_kuhn','poker_leduc','information_complexity_extension',
                'Both are imperfect-information poker benchmarks with different game trees.'),
               ('iterated_prisoners_dilemma','repeated_cooperation','repeated_policy_analogy',
                'Repeated cooperation includes contingent strategies over Prisoners Dilemma stage games.'),
               ('hypergame','opponent_model_uncertainty','subjective_model_specialization',
                'Agents may have different perceived game models; not a fixed public payoff table.'),
               ('information_cascade','bayesian_coordination','belief_aggregation_analogy',
                'Belief updating and observed previous actions can produce information cascades.'),
               ('volunteer_dilemma','threshold_public_goods','threshold_contribution_variant',
                'Volunteering can be modeled as threshold provision of a public good.'),
               ('crowd_congestion','population_congestion','mean_field_analogy',
                'Population congestion examples differ by exact state and observation rules.')]
        for left,right,relation,condition in links:
            if cn.execute('SELECT 1 FROM games WHERE game_id=?',(left,)).fetchone() and cn.execute('SELECT 1 FROM games WHERE game_id=?',(right,)).fetchone():
                cn.execute('INSERT INTO game_relations VALUES (?,?,?,?)',(left,right,relation,condition))
        r=random.Random(seed)
        for gid,(name,fam,rows,cols,base) in sorted(MATRICES.items()):
            for index in range(cases_per_game):
                # An independent positive-affine perturbation preserves base equilibrium
                # yet supplies real numeric variation in outputs.
                scale1=r.randint(1,5);scale2=r.randint(1,4)
                shift1=r.randint(-12,12);shift2=r.randint(-9,9)
                m=[[[scale1*u+shift1,scale2*v+shift2] for u,v in row] for row in base]
                group=digest(['matrix',seed,gid,index]);split=split_for(group,seed)
                instance_id=digest(['instance',group])
                src={'U':m,'row_actions':rows,'column_actions':cols,'players':['row','column']}
                oracle={'pure_nash':pure_nash(m),'example_status':'synthetic_verified'}
                cn.execute('INSERT INTO instances VALUES (?,?,?,?,?,?,?,?)',
                    (instance_id,gid,split,'matrix',canon({'index':index,'scale1':scale1,'scale2':scale2,'shift1':shift1,'shift2':shift2}),canon(src),canon(oracle),group))
                for op in ('swap_players','permute_row','permute_column','positive_affine','shared_payoff_shift','change_one_payoff','reverse_payoff_sign','compose_transformations'):
                    new=matrix_transform(m,op);nash=pure_nash(new);old_nash=oracle['pure_nash']
                    holds=op in ('swap_players','permute_row','permute_column','positive_affine','shared_payoff_shift','compose_transformations')
                    expected=expected_eq_mapping(old_nash,op) if holds else None
                    if holds and nash!=expected: raise RuntimeError('TRANSFORM_EQUILIBRIUM_VIOLATION:'+gid+':'+op)
                    actual_preservation=(nash==expected_eq_mapping(old_nash,op)) if op in ('swap_players','permute_row','permute_column','positive_affine','shared_payoff_shift','compose_transformations') else nash==old_nash
                    target={'kind':'matrix','operator':op,'game_id':gid,'U_prime':new,'original_pure_nash':old_nash,
                            'pure_nash':nash,'equilibrium_preserved':actual_preservation,'preservation_guarantee':holds,'mapped_original_equilibria':expected,
                            'cell_delta':new[0][0][0]-m[0][0][0],
                            'numeric_bridge':{'x':m[0][0][0],'y':new[0][0][0],'operation':'subtract',
                                              'semantic':'original row utility and post-transform row utility'},
                            'certificate':'exhaustive_pure_best_response'}
                    insert_case(cn,instance_id,split,op,src,target,gid,frame='matrix')
        for gid,(name,fam,formula,base,changed,increment) in sorted(SCALAR_MODELS.items()):
            for index in range(cases_per_game):
                params=dict(base)
                # Moderate deterministic perturbations preserve domain for selected models.
                for k,v in params.items():
                    if k in ('gamma','prior','p','detection_probability','casualty_probability','l_true','l_false','attack_probability'):
                        params[k]=round(min(.89,max(.11,v+r.uniform(-.035,.035))),5)
                    else:
                        params[k]=round(v+r.uniform(-.1,.1)*max(1.,abs(v)),5)
                if gid=='hawk_dove_mixed':params['C']=max(params['C'],params['V']+2.)
                if gid=='blotto_remaining':params['B']=max(params['B'],params['x1']+params['x2']+4.)
                if gid=='cournot_quantity':params['a']=max(params['a'],params['c']+2.)
                if gid=='nash_bargain_split':params['V']=max(params['V'],params['d1']+params['d2']+2.)
                if gid=='robust_minimum':params['loss_case_1']=min(params['loss_case_1'],params['loss_case_2']-1.)
                old=scalar_oracle(gid,params)
                new_params=dict(params);new_params[changed]=round(new_params[changed]+increment,5)
                new=scalar_oracle(gid,new_params)
                group=digest(['scalar',seed,gid,index]);split=split_for(group,seed)
                instance_id=digest(['instance',group])
                source={'parameters':params,'formula':formula,'value':old,'changed':changed,'increment':increment}
                cn.execute('INSERT INTO instances VALUES (?,?,?,?,?,?,?,?)',
                     (instance_id,gid,split,'scalar',canon(params),canon(source),canon({'value':old,'formula':formula}),group))
                target={'kind':'scalar','operator':'scalar_parameter_shift','game_id':gid,'formula':formula,
                        'changed':changed,'new_parameters':new_params,'value_before':old,'value_after':new,
                        'delta':new-old,'numeric_bridge':{'x':old,'y':new,'operation':'subtract',
                               'semantic':'original value and transformed value'},
                        'certificate':'deterministic_scalar_oracle'}
                insert_case(cn,instance_id,split,'scalar_parameter_shift',source,target,gid,frame='scalar')
        cn.commit()
        verify(cn)
        stats=statistics(cn)
        return stats
    except:
        cn.rollback();cn.close()
        if path.exists():path.unlink()
        raise
    finally:
        try:cn.close()
        except Exception:pass

def statistics(cn) -> dict:
    counts={table:cn.execute('SELECT COUNT(*) FROM '+table).fetchone()[0] for table in
        ('families','games','dictionary','transformations','game_relations','training_objectives','training_mix','instances','transformation_cases','presentations')}
    return {'schema':SCHEMA_VERSION,'counts':counts,
            'splits':dict(cn.execute('SELECT split,COUNT(*) FROM transformation_cases GROUP BY split').fetchall()),
            'verified_transformation_cases':counts['transformation_cases'],
            'catalog_only_games':cn.execute("SELECT COUNT(*) FROM games WHERE status='catalog_only'").fetchone()[0],
            'oracle_verified_games':cn.execute("SELECT COUNT(*) FROM games WHERE status='oracle_verified'").fetchone()[0]}

def verify(cn) -> dict:
    if cn.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise AssertionError('SQL_INTEGRITY')
    if cn.execute('PRAGMA foreign_key_check').fetchall():raise AssertionError('FOREIGN_KEYS')
    mismatch=cn.execute('SELECT COUNT(*) FROM transformation_cases c JOIN instances i ON i.instance_id=c.instance_id WHERE i.split<>c.split').fetchone()[0]
    if mismatch:raise AssertionError('SPLIT_LEAK')
    mix_total=cn.execute("SELECT SUM(examples_per_batch) FROM training_mix WHERE mix_id='game_transform_bridge_v1'").fetchone()[0]
    if mix_total!=16:raise AssertionError('TRAINING_MIX_NOT_SIXTEEN')
    mismatch=cn.execute('SELECT COUNT(*) FROM transformation_cases c WHERE (SELECT COUNT(*) FROM presentations p WHERE p.case_id=c.case_id)!=3').fetchone()[0]
    if mismatch:raise AssertionError('PRESENTATION_MISMATCH')
    for row in cn.execute('SELECT c.case_id,c.op_id,c.input_json,c.target_json,i.game_id FROM transformation_cases c JOIN instances i ON i.instance_id=c.instance_id'):
        case_id,op,src_text,target_text,gid=row
        src=json.loads(src_text);target=json.loads(target_text)
        if target['kind']=='matrix':
            new=matrix_transform(src['U'],op)
            if new!=target['U_prime'] or pure_nash(new)!=target['pure_nash']:
                raise AssertionError('MATRIX_ORACLE_DRIFT')
            if target['preservation_guarantee'] and target['pure_nash']!=target['mapped_original_equilibria']:
                raise AssertionError('MATRIX_EQUILIBRIUM_DRIFT')
        else:
            new_params=target['new_parameters']
            actual=scalar_oracle(gid,new_params)
            if abs(actual-target['value_after'])>1e-8*max(1.,abs(actual)):
                raise AssertionError('SCALAR_ORACLE_DRIFT')
        expected=canon(target)
        for presentation in cn.execute('SELECT target_json FROM presentations WHERE case_id=?',(case_id,)):
            if presentation[0]!=expected:raise AssertionError('PRESENTATION_GOLD_DRIFT')
    return {'integrity':'ok','foreign_keys':True,'isolated_splits':True,'three_orientations':True,'oracles_checked':True}

def export_jsonl(database:Path, destination:Path) -> dict:
    destination=Path(destination);destination.mkdir(parents=True,exist_ok=True)
    cn=sqlite3.connect(database)
    try:
        stats={}
        for split in SPLITS:
            out=destination/(split+'.jsonl');n=0
            with out.open('w',encoding='utf8',newline='\n') as fp:
                for cid,gid,op,orient,prompt,target in cn.execute('''SELECT c.case_id,i.game_id,c.op_id,p.orientation,p.prompt,p.target_json
                        FROM transformation_cases c JOIN instances i ON i.instance_id=c.instance_id
                        JOIN presentations p ON p.case_id=c.case_id
                        WHERE c.split=? ORDER BY c.case_id,p.orientation''',(split,)):
                    fp.write(canon({'group_id':cid,'game_id':gid,'operator':op,'orientation':orient,'prompt':prompt,
                                    'target':json.loads(target),'objective':OBJECTIVE['id'],'split':split})+'\n');n+=1
            stats[split]=n
        return stats
    finally:cn.close()

def export_task_bank(database:Path, destination:Path) -> dict:
    """Bridge to existing TrainingTaskStore; NOT an automatic V3 trainer integration.

    Both source and post-transform numeric targets are available to an XY head;
    broader structural game targets remain in the task's provenance/target.
    """
    try:
        from tools.nanojev_training_task_store import TrainingTaskStore
    except ImportError:
        from nanojev_training_task_store import TrainingTaskStore
    destination=Path(destination)
    if destination.exists():raise FileExistsError(destination)
    destination.parent.mkdir(parents=True,exist_ok=True)
    src=sqlite3.connect(database)
    try:
        with TrainingTaskStore(destination) as bank:
            for split in SPLITS:
                bank.add_cohort('game-transform-'+split,family='game_transformation_v1',split=split,source={'database_sha256':hashlib.sha256(Path(database).read_bytes()).hexdigest(),'schema':SCHEMA_VERSION})
            for case,split,op,gid,target,instance in src.execute('''SELECT c.case_id,c.split,c.op_id,i.game_id,c.target_json,c.instance_id
                    FROM transformation_cases c JOIN instances i ON c.instance_id=i.instance_id ORDER BY c.case_id'''):
                t=json.loads(target)
                presentations={name:{'prompt':prompt,'orientation':name,'a':t['numeric_bridge']['x'],'b':t['numeric_bridge']['y']}
                               for name,prompt in src.execute('SELECT orientation,prompt FROM presentations WHERE case_id=? ORDER BY orientation',(case,))}
                bank.put(cohort_id='game-transform-'+split,family='game_transformation_v1',task=op,split=split,
                   fingerprint=case,oracle='checked-game-transform-v1',label_status='verified',target={
                       'x':t['numeric_bridge']['x'],'y':t['numeric_bridge']['y'],
                       'operation':t['numeric_bridge']['operation'], 'transformation':t},
                   provenance={'game_id':gid,'instance_id':instance,'operator':op,'structured_objective':OBJECTIVE['id']},variants=presentations)
            bank.conn.commit()
            bank.verify()
            return bank.stats()
    finally:src.close()

def demo(path:Path) -> dict:
    cn=sqlite3.connect(path)
    row=cn.execute("SELECT c.input_json,c.target_json FROM transformation_cases c JOIN instances i ON c.instance_id=i.instance_id WHERE i.game_id='hawk_dove' AND c.op_id='positive_affine' LIMIT 1").fetchone()
    if not row:raise RuntimeError('DEMO_MISSING')
    source=json.loads(row[0]);target=json.loads(row[1]);cn.close()
    return {'game':'hawk_dove','operator':'positive_affine','before':source['U'],'after':target['U_prime'],
            'equilibria_before':target['original_pure_nash'],'equilibria_after':target['pure_nash'],
            'invariant':target['equilibrium_preserved']}

def main(argv=None) -> int:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',type=Path,default=Path('runtime/game-transformation-library/game_transformations.sqlite3'))
    p.add_argument('--seed',type=int,default=20261015)
    p.add_argument('--instances-per-game',type=int,default=16)
    p.add_argument('--verify',action='store_true')
    p.add_argument('--export-jsonl',type=Path)
    p.add_argument('--export-task-bank',type=Path)
    p.add_argument('--smoke',action='store_true')
    args=p.parse_args(argv)
    if not args.verify:
        if args.instances_per_game<4:raise ValueError('instances-per-game must be >=4')
        report=build(args.output,args.seed,args.instances_per_game)
    else:
        db=sqlite3.connect(args.output)
        report={'stats':statistics(db),'verified':verify(db)}
        db.close()
    if args.export_jsonl:report['jsonl_samples']=export_jsonl(args.output,args.export_jsonl)
    if args.export_task_bank:report['training_task_bank']=export_task_bank(args.output,args.export_task_bank)
    if args.smoke:report['hawk_dove_smoke']=demo(args.output)
    print(json.dumps(report,indent=2,sort_keys=True))
    return 0

if __name__=='__main__':raise SystemExit(main())
