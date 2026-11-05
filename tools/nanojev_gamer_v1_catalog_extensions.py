#!/usr/bin/env python3
"""Opt-in, bounded mathematical objectives for unused catalog games.

These are *not* claims to solve full unrestricted games. Each source catalog
entry has an explicit restricted public question, numerical oracle contract,
and a mathematical-table target. Only these bounded tasks are certified.
A game title alone is never used as a numerical supervision label.
"""
from __future__ import annotations

import ast
from fractions import Fraction
import math
from tools import nanojev_game_transformation_library as library

# Each matrix is an explicit named-game example, not a random game generator.
# U[row][col]=[row utility, column utility]; action order is part of the rule.
# Most objectives ask for the best-response ACTION INDEX against a stated
# opponent action. Rock-Paper-Scissors asks for the best-response PAYOFF since
# its three-way maximum can use the existing arithmetic factor table.
MATRIX_SPECS = [
 ('matrix','Constant-sum game',('High','Low'),('High','Low'),(((4,6),(1,9)),((6,4),(3,7)))),
 ('matrix','Zero-sum game',('Attack','Defend'),('Attack','Defend'),(((0,0),(3,-3)),((-2,2),(1,-1)))),
 ('coordination','Assurance game',('Invest','Safe'),('Invest','Safe'),(((6,6),(-4,1)),((1,-4),(2,2)))),
 ('coordination','Rock Paper Scissors',('Rock','Paper','Scissors'),('Rock','Paper','Scissors'),(((0,0),(-1,1),(1,-1)),((1,-1),(0,0),(-1,1)),((-1,1),(1,-1),(0,0)))),
 ('coordination','Dispersion game',('Site A','Site B'),('Site A','Site B'),(((0,0),(4,4)),((4,4),(0,0)))),
 ('coordination','Minimum effort game',('High','Low'),('High','Low'),(((5,5),(0,2)),((2,0),(2,2)))),
 ('coordination','Common-interest game',('Joint plan','Separate plan'),('Joint plan','Separate plan'),(((7,7),(1,1)),((1,1),(3,3)))),
 ('coordination','Pure coordination',('Signal A','Signal B'),('Signal A','Signal B'),(((3,3),(0,0)),((0,0),(2,2)))),
 ('coordination','Battle of the Sexes',('Concert','Match'),('Concert','Match'),(((3,2),(0,0)),((0,0),(2,3)))),
 ('coordination','Anti-coordination game',('Left','Right'),('Left','Right'),(((0,0),(4,4)),((4,4),(0,0)))),
 ('social_dilemmas','Snowdrift game',('Clear','Wait'),('Clear','Wait'),(((3,3),(1,5)),((5,1),(0,0)))),
 ('social_dilemmas','Collaboration game',('Share','Keep'),('Share','Keep'),(((5,5),(0,2)),((2,0),(1,1)))),
]

# Restricted, explicit one-step objectives. Each expression uses only the
# existing calculation-table operators (+,-,*,/,max) and observed quantities.
# The English prompt states the entire operative rule rather than presuming
# that the model knows an unspecified game's equilibrium or strategy.
SCALAR_SPECS = [
 ('social_dilemmas','Public Goods','contribution*multiplier/participants-contribution',
  'A one-round public-goods game has {participants} participants. You contribute {contribution} units to a common pool; every contributed unit creates {multiplier} units of group value divided equally among participants. Considering only this contribution, what is your net return (your equal share minus your contribution)?',
  dict(participants=4,contribution=6,multiplier=2)),
 ('social_dilemmas','Threshold Public Goods','endowment-contribution+bonus',
  'In this threshold public-goods round the threshold is already reached. You begin with {endowment} units, contribute {contribution}, and receive the specified threshold bonus of {bonus}. What is your final balance?',
  dict(endowment=20,contribution=6,bonus=11)),
 ('social_dilemmas','Tragedy of Commons','benefit*harvest-cost*harvest*harvest',
  'For this one-round commons example, harvesting {harvest} units produces {benefit} benefit per unit but a quadratic commons-loss coefficient of {cost} per squared harvested unit. What is the resulting net payoff?',
  dict(benefit=12,harvest=4,cost=1)),
 ('social_dilemmas','Collective-risk social dilemma','endowment-contribution-risk_probability*loss',
  'For a specified collective-risk round, you begin with {endowment}, contribute {contribution} toward protection, and still face residual disaster probability {risk_probability} with loss {loss}. What is your expected remaining value?',
  dict(endowment=25,contribution=4,risk_probability=.3,loss=15)),
 ('evolutionary','Evolutionary Cooperation','cooperator_fraction*cooperator_payoff+(1-cooperator_fraction)*defector_payoff',
  'In a one-round evolutionary population, the fraction cooperating is {cooperator_fraction}. A cooperator earns {cooperator_payoff} and a defector earns {defector_payoff}. What is the population mean payoff?',
  dict(cooperator_fraction=.4,cooperator_payoff=7,defector_payoff=3)),
 ('timing','War of Attrition','prize-cost_per_round*waiting_rounds',
  'In a finite attrition contest, you are guaranteed the prize of {prize} after waiting {waiting_rounds} rounds, with a cost of {cost_per_round} each round. What is your resulting net payoff (without modeling the opponent\'s response)?',
  dict(prize=20,cost_per_round=3,waiting_rounds=4)),
 ('timing','Preemption','first_mover_reward-delay_cost*delay',
  'For this preemption example, acting first yields {first_mover_reward} but waiting {delay} ticks incurs {delay_cost} cost per tick. Given that first-mover outcome, what is your payoff?',
  dict(first_mover_reward=18,delay=3,delay_cost=2)),
 ('timing','Grab the Dollar','prize-split_penalty',
  'In a specified grab-the-dollar outcome, you take the prize of {prize} units and incur a collision/split penalty of {split_penalty}. What payoff remains?',
  dict(prize=20,split_penalty=5)),
 ('bargaining','Ultimatum','offer-reservation',
  'In a one-shot ultimatum bargain you are offered {offer} units and your stated reservation payoff is {reservation}. What is the signed advantage of accepting over rejecting? This computes the acceptance margin, not a full equilibrium.',
  dict(offer=9,reservation=7)),
 ('bargaining','Dictator','endowment-gift',
  'A dictator allocates {gift} units of an endowment of {endowment} to the other party. What is the dictator\'s retained allocation?',
  dict(endowment=25,gift=10)),
 ('bargaining','Holdout','settlement-offer_cost-delay_cost*delay',
  'A holdout settles for {settlement} units after {delay} rounds. The agreement has {offer_cost} fixed cost and {delay_cost} cost per round. What is the net settlement payoff?',
  dict(settlement=30,offer_cost=4,delay_cost=2,delay=3)),
 ('bargaining','Negotiation with Outside Option','agreement-outside_option',
  'A proposed negotiated agreement pays {agreement}; walking away pays the known outside option of {outside_option}. What is the agreement\'s signed advantage over walking away?',
  dict(agreement=14,outside_option=11)),
 ('resource','Tullock Contest','prize*effort/(effort+opponent_effort)-cost_per_effort*effort',
  'In a two-player linear Tullock contest, your effort is {effort}, opponent effort is {opponent_effort}, and the prize is worth {prize}. Your win probability is your effort divided by combined effort. Effort costs {cost_per_effort} per unit. What is your expected net payoff?',
  dict(prize=25,effort=6,opponent_effort=4,cost_per_effort=1)),
 ('resource','All Pay Auction','win_probability*prize-bid',
  'In an all-pay auction your bid of {bid} is spent regardless of winning, the prize is worth {prize}, and your specified win probability is {win_probability}. What is the expected net payoff?',
  dict(win_probability=.5,prize=30,bid=9)),
 ('resource','Budget-Constrained Defense','budget-interception-spare_reserve',
  'A budget-constrained defender starts with {budget} units, allocates {interception} to interception, and must retain {spare_reserve} as a reserve. What signed budget margin remains?',
  dict(budget=35,interception=20,spare_reserve=10)),
 ('security','Attacker Defender Game','attack_probability*(1-detection_probability)*damage+defense_cost',
  'A defender faces attack probability {attack_probability}; on attack, defense detects it with probability {detection_probability}. An undetected attack costs {damage}, while defense costs {defense_cost} for this round. What is total expected loss including defense cost?',
  dict(attack_probability=.5,detection_probability=.4,damage=20,defense_cost=3)),
 ('network','Congestion Game','base_delay+slope*load',
  'In a one-link congestion game the route has delay {base_delay} at zero load and adds {slope} delay per unit of load. Current load is {load}. What is this route\'s delay?',
  dict(base_delay=4,slope=.5,load=12)),
 ('mechanism','First Price Auction','value-own_bid',
  'You have won this first-price sealed-bid auction. Your private value is {value}, and you pay your own winning bid of {own_bid}. What is your realized utility?',
  dict(value=30,own_bid=20)),
 ('mechanism','Vickrey Auction','value-second_highest_bid',
  'You have won this single-item Vickrey auction at the second-highest bid of {second_highest_bid}. You value the item at {value}. What is the winner\'s utility?',
  dict(value=30,second_highest_bid=18)),
 ('mechanism','Majority Voting','support_votes-(total_votes/2)',
  'In a majority vote with {total_votes} valid votes, {support_votes} favor a proposal. What is the signed number of support votes above half the total? A positive result indicates a strict majority.',
  dict(total_votes=20,support_votes=11)),
 ('mechanism','Public Project Mechanism','total_benefit-project_cost',
  'A specified public project would produce aggregate benefit {total_benefit} and cost {project_cost}. What is its net social surplus? This does not solve an incentive-compatible tax mechanism.',
  dict(total_benefit=40,project_cost=27)),
 ('mechanism','Median Voter','median_position-proposed_position',
  'In a one-dimensional median-voter setting the already-established median voter is at position {median_position}, while a proposed policy is at {proposed_position}. What is the signed displacement from the proposal to the median? This does not infer the median from ballots.',
  dict(median_position=5,proposed_position=3)),
 ('coalition','Shapley Value','(singleton_value+(grand_value-other_singleton))/2',
  'In a two-player transferable-utility coalition game, your singleton coalition is worth {singleton_value}, the other player alone is worth {other_singleton}, and the grand coalition is worth {grand_value}. What is your two-player Shapley allocation?',
  dict(singleton_value=8,other_singleton=9,grand_value=25)),
 ('coalition','Core Allocation','allocated_value-singleton_value',
  'In a two-player transferable-utility coalition, you are assigned {allocated_value}, while you could obtain {singleton_value} acting alone. What is your individual-rationality margin? This tests one necessary core inequality, not all coalition blocks.',
  dict(allocated_value=13,singleton_value=9)),
 ('coalition','Coalition Blocking','coalition_value-allocated_total',
  'A proposed coalition can create {coalition_value} units but is allocated {allocated_total} units in the current agreement. What is its signed blocking surplus? Positive means this specified coalition could object.',
  dict(coalition_value=24,allocated_total=20)),
 ('coalition','Quorum Escalation','supporting_votes-required_votes',
  'For a proposed quorum-escalation decision, {supporting_votes} members support it and the required threshold is {required_votes}. What is the signed vote margin?',
  dict(supporting_votes=9,required_votes=7)),
 ('coalition','Three Party Bargaining','total_value-party_two-party_three',
  'Three bargaining parties divide exactly {total_value} transferable units. The second party receives {party_two} and the third receives {party_three}. What allocation remains for the first party? This is a feasibility calculation, not a bargaining equilibrium.',
  dict(total_value=36,party_two=12,party_three=10)),
 ('byzantine','Byzantine Generals','honest_nodes-(2*faulty_nodes+1)',
  'For the stated synchronous Byzantine agreement sizing check, there are {honest_nodes} nonfaulty nodes and {faulty_nodes} faulty nodes. What is the slack above the condition honest nodes > 2 times faulty nodes? This is only the stated threshold check.',
  dict(honest_nodes=8,faulty_nodes=2)),
 ('byzantine','Consensus with Dishonest Reports','honest_reports-quorum',
  'A deterministic quorum decision has {honest_reports} authenticated agreeing honest reports and requires {quorum} to finalize. What is the signed honest-report margin? This alone does not prove distributed consensus.',
  dict(honest_reports=9,quorum=7)),
 ('deception','Opponent Model Uncertainty','probability_model_one*payoff_model_one+(1-probability_model_one)*payoff_model_two',
  'You assign probability {probability_model_one} to an opponent behaving according to model one, yielding payoff {payoff_model_one}; otherwise model two yields {payoff_model_two}. What is your expected payoff under this stated belief mixture?',
  dict(probability_model_one=.4,payoff_model_one=10,payoff_model_two=-5)),
 ('population','El Farol Bar','attendance_probability*attend_payoff+(1-attendance_probability)*stay_payoff',
  'For a simplified El Farol attendance policy, a favorable crowd condition occurs with probability {attendance_probability}. You attend if favorable and receive payoff {attend_payoff}; otherwise you stay away and receive {stay_payoff}. What is the expected payoff of this contingent policy?',
  dict(attendance_probability=.6,attend_payoff=8,stay_payoff=3)),
 ('population','Minority Game','win_probability*winning_reward+(1-win_probability)*losing_reward',
  'In a binary minority-game round, the chance your chosen side is the minority is {win_probability}. Minority payoff is {winning_reward} and majority payoff {losing_reward}. What is the expected reward of this choice?',
  dict(win_probability=.4,winning_reward=12,losing_reward=-2)),
 ('population','Market Entry','market_revenue-competition_cost*entrants',
  'For one market-entry round, entering yields fixed revenue {market_revenue}, but each of the {entrants} market entrants imposes congestion cost {competition_cost}. What is the entrant\'s payoff?',
  dict(market_revenue=30,competition_cost=4,entrants=5)),
 ('population','Collective Herding','total_signal-agents*coordination_cost',
  'A collective-herding decision yields aggregate signal value {total_signal}, with {agents} agents each imposing coordination cost {coordination_cost}. What is the net collective signal?',
  dict(total_signal=40,agents=6,coordination_cost=3)),
 ('market','Bertrand Oligopoly','(price-unit_cost)*quantity',
  'A price-setting Bertrand competitor charges {price} per unit, incurs {unit_cost} cost per unit and sells {quantity} units at that price. What is its operating profit for this observed outcome? This does not calculate equilibrium price.',
  dict(price=15,unit_cost=9,quantity=20)),
 ('market','Stackelberg Competition','(intercept-marginal_cost)/(2*slope)',
  'In a standard zero-follower-cost symmetric linear-demand Stackelberg duopoly with inverse demand intercept {intercept}, unit marginal cost {marginal_cost} common to both firms, and positive demand slope {slope}, what is the leader\'s interior output?',
  dict(intercept=100,marginal_cost=20,slope=2)),
 ('market','Price War','(current_price-unit_cost)*quantity',
  'In a specified price-war round your firm charges {current_price}, incurs unit cost {unit_cost}, and sells {quantity} units. What is its immediate profit? This is a realized-outcome calculation, not an optimal price.',
  dict(current_price=10,unit_cost=8,quantity=30)),
 ('market','Common-Value Auction','estimated_common_value-payment',
  'In a specified common-value auction outcome, your estimated common value conditional on available information is {estimated_common_value}, and the price paid is {payment}. What is your estimated utility?',
  dict(estimated_common_value=22,payment=17)),
 ('market','Market Maker Game','ask_price-bid_price-inventory_cost',
  'A market maker executes one round trip buying at bid price {bid_price}, selling at ask price {ask_price}, and incurring inventory cost {inventory_cost}. What is the net spread captured?',
  dict(ask_price=11,bid_price=9,inventory_cost=.5)),
 ('risk','Regret Minimization','best_available_payoff-chosen_payoff',
  'In a regret-minimization round the best available action after the fact earns {best_available_payoff}, while the chosen action earns {chosen_payoff}. What is instantaneous regret?',
  dict(best_available_payoff=19,chosen_payoff=12)),
 ('risk','Robust Security','max(loss_case_one,loss_case_two)+mitigation_cost',
  'A defender has two specified worst-case losses, {loss_case_one} and {loss_case_two}, and must also spend {mitigation_cost} on protection. What is worst-case total loss (max scenario loss plus protection cost)?',
  dict(loss_case_one=8,loss_case_two=11,mitigation_cost=3)),
 ('risk','Distributionally Robust Game','max(expected_loss_model_one,expected_loss_model_two)',
  'Under a restricted distributionally robust ambiguity set containing exactly two specified models, expected losses are {expected_loss_model_one} and {expected_loss_model_two}. What is the worst expected loss over these two models?',
  dict(expected_loss_model_one=15,expected_loss_model_two=18)),
 ('captain','Captain Mission Commitment','mission_value-projected_loss-abandonment_cost',
  'For a specified mission choice, completion produces {mission_value} units, expected attrition costs {projected_loss}, and commitment has additional {abandonment_cost}. What is net mission value?',
  dict(mission_value=50,projected_loss=12,abandonment_cost=4)),
 ('captain','Captain Resource Assignment','remaining_resources-assigned_resources',
  'A captain has {remaining_resources} uncommitted resources and assigns {assigned_resources} to a new task. How much remains uncommitted?',
  dict(remaining_resources=40,assigned_resources=17)),
 ('captain','Captain Tactical Time Step','starting_separation+evader_speed-pursuer_speed',
  'A captain observes separation {starting_separation} before one tactical time step. The evader advances {evader_speed} distance units and the pursuer advances {pursuer_speed}. What signed separation results?',
  dict(starting_separation=20,evader_speed=6,pursuer_speed=8)),
 ('captain','Captain Risk Assessment','hazard_probability*hazard_loss+prevention_cost',
  'A captain faces a hazard with probability {hazard_probability} and loss {hazard_loss}, plus certain prevention cost {prevention_cost}. What is the expected combined cost for this decision?',
  dict(hazard_probability=.3,hazard_loss=40,prevention_cost=6)),
]

CATALOG_IDS={
 library.family_game_id(name):(family,name)
 for family, entries in library.CATALOG.items() for name in entries.split('|')
}

EXTRA_MATRICES={library.family_game_id(name):
 {'family':fam,'title':name,'row_actions':list(rows),'column_actions':list(cols),
  'U':[[list(x) for x in row] for row in matrix],
  'objective':'best_response_payoff' if len(rows)>2 else 'best_response_index'}
 for fam,name,rows,cols,matrix in MATRIX_SPECS}
EXTRA_SCALARS={library.family_game_id(name):
 {'family':fam,'title':name,'formula':formula,'prompt':prompt,'base':base,
  'objective':'bounded_mathematical_subproblem'}
 for fam,name,formula,prompt,base in SCALAR_SPECS}
EXTRA_IDS=frozenset(EXTRA_MATRICES)|frozenset(EXTRA_SCALARS)


def _verify_registry():
 if len(EXTRA_IDS)!=58 or len(EXTRA_MATRICES)!=12 or len(EXTRA_SCALARS)!=46:
  raise AssertionError('GAMER_EXPECTED_58_BOUNDED_CATALOG_EXTENSIONS')
 if set(EXTRA_MATRICES)&set(EXTRA_SCALARS):
  raise AssertionError('GAMER_DUPLICATE_EXTRA_GAME')
 old_ids=set(library.MATRICES)|set(library.SCALAR_MODELS)
 if EXTRA_IDS&old_ids:
  raise AssertionError('GAMER_EXTRA_COLLIDES_WITH_EXISTING_GAME_ID')
 for game, spec in {**EXTRA_MATRICES,**EXTRA_SCALARS}.items():
  if game not in CATALOG_IDS or CATALOG_IDS[game]!=(spec['family'],spec['title']):
   raise AssertionError(f'GAMER_SOURCE_CATALOG_MISMATCH: {game}')
  if game in EXTRA_SCALARS:
   tree=ast.parse(spec['formula'],mode='eval').body
   names={n.id for n in ast.walk(tree) if isinstance(n,ast.Name) and n.id!='max'}
   if names!=set(spec['base']):
    raise AssertionError(f'GAMER_SCALAR_VARIABLES_MISMATCH: {game}')
   for param in names:
    if '{'+param+'}' not in spec['prompt']:
     raise AssertionError(f'GAMER_SCALAR_PROMPT_MISSING_INPUT: {game} {param}')

_verify_registry()


def domain_ok(game,p):
 """Only permit the precise, finite restricted game domain claimed in prose."""
 if any(not isinstance(v,(int,float)) or not math.isfinite(v) for v in p.values()):return False
 for name,value in p.items():
  if any(t in name for t in ('probability','fraction')) and not (0.05<=value<=0.95):return False
  if name in ('participants','effort','opponent_effort','slope','total_votes') and value<=0:return False
 if game=='stackelberg_competition' and p['intercept']<=p['marginal_cost']:return False
 if game=='threshold_public_goods' and p['contribution']>p['endowment']:return False
 if game=='dictator' and p['gift']>p['endowment']:return False
 if game=='captain_resource_assignment' and p['assigned_resources']>p['remaining_resources']:return False
 if game=='public_goods' and p['participants']<2:return False
 if game=='tullock_contest' and p['effort']+p['opponent_effort']<=0:return False
 if game=='majority_voting' and p['support_votes']>p['total_votes']:return False
 if game=='regret_minimization' and p['best_available_payoff']<p['chosen_payoff']:return False
 return True
