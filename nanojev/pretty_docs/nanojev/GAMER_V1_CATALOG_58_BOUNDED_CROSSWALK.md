# Gamer v1 — 58 named-catalog mathematical objectives (opt-in)

## Scope and provenance

The source catalog lists these names and family mathematics but **does not** contain executable full-game oracles for them. This patch defines **58 new, restricted mathematical questions** grounded in named examples, with explicit finite source-state parameters, executable numerical formula or fixed payoff tensor, source-recomputed sparse-table factors, and deterministic replay certification. They are not certified full-game solutions, strategic equilibria, policies, or evidence of generalization across all games.

The previous 33 source-native models are preserved byte-for-byte by default. The 58 added entries are separate IDs and must be enabled using `--include-catalog-extensions`. The rejected random-payoff-matrix / blind anonymous-game generator remains retired. Do not claim full game-native certification for any new catalog entry without adding its missing strategic source oracle.

## Training and testing boundary

- Existing tables: calculation; strategic-payoff; probabilistic-outcome. No fourth format.
- Game entries: 33 existing source-verified + 58 bounded extensions = 91 named entries.
- Default 18 certified cases per entry: 1,638 total (1,092 train, 273 dev, 273 test).
- The new 58 supply 1,044 cases. The matrix examples are fixed named matrices with positive-affine, incentive-preserving parameterization, not fabricated anonymous games.
- The LoRA translates the public *08 prompt to uncomputed tables. `to_numerical` still learns from original-prompt activations, not the tables.
- Full-game equilibrium, strategy synthesis, causal transfer, and held-out neural generalization remain unverified. A bounded probe is not full retention certification.

## Exact 58-entry source crosswalk

| Source catalog family | Entry | New supervised objective | Registered mathematical contract |
|---|---|---|---|
| bargaining | Dictator | bounded scalar | `endowment-gift` |
| bargaining | Holdout | bounded scalar | `settlement-offer_cost-delay_cost*delay` |
| bargaining | Negotiation with Outside Option | bounded scalar | `agreement-outside_option` |
| bargaining | Ultimatum | bounded scalar | `offer-reservation` |
| byzantine | Byzantine Generals | bounded scalar | `honest_nodes-(2*faulty_nodes+1)` |
| byzantine | Consensus with Dishonest Reports | bounded scalar | `honest_reports-quorum` |
| captain | Captain Mission Commitment | bounded scalar | `mission_value-projected_loss-abandonment_cost` |
| captain | Captain Resource Assignment | bounded scalar | `remaining_resources-assigned_resources` |
| captain | Captain Risk Assessment | bounded scalar | `hazard_probability*hazard_loss+prevention_cost` |
| captain | Captain Tactical Time Step | bounded scalar | `starting_separation+evader_speed-pursuer_speed` |
| coalition | Coalition Blocking | bounded scalar | `coalition_value-allocated_total` |
| coalition | Core Allocation | bounded scalar | `allocated_value-singleton_value` |
| coalition | Quorum Escalation | bounded scalar | `supporting_votes-required_votes` |
| coalition | Shapley Value | bounded scalar | `(singleton_value+(grand_value-other_singleton))/2` |
| coalition | Three Party Bargaining | bounded scalar | `total_value-party_two-party_three` |
| coordination | Anti-coordination game | best response index | `Best response to a fixed opposing move from a fixed source payoff matrix` |
| coordination | Assurance game | best response index | `Best response to a fixed opposing move from a fixed source payoff matrix` |
| coordination | Battle of the Sexes | best response index | `Best response to a fixed opposing move from a fixed source payoff matrix` |
| coordination | Common-interest game | best response index | `Best response to a fixed opposing move from a fixed source payoff matrix` |
| coordination | Dispersion game | best response index | `Best response to a fixed opposing move from a fixed source payoff matrix` |
| coordination | Minimum effort game | best response index | `Best response to a fixed opposing move from a fixed source payoff matrix` |
| coordination | Pure coordination | best response index | `Best response to a fixed opposing move from a fixed source payoff matrix` |
| coordination | Rock Paper Scissors | best response payoff | `Maximum payoff against a fixed opposing move from a 3×3 source matrix` |
| deception | Opponent Model Uncertainty | bounded scalar | `probability_model_one*payoff_model_one+(1-probability_model_one)*payoff_model_two` |
| evolutionary | Evolutionary Cooperation | bounded scalar | `cooperator_fraction*cooperator_payoff+(1-cooperator_fraction)*defector_payoff` |
| market | Bertrand Oligopoly | bounded scalar | `(price-unit_cost)*quantity` |
| market | Common-Value Auction | bounded scalar | `estimated_common_value-payment` |
| market | Market Maker Game | bounded scalar | `ask_price-bid_price-inventory_cost` |
| market | Price War | bounded scalar | `(current_price-unit_cost)*quantity` |
| market | Stackelberg Competition | bounded scalar | `(intercept-marginal_cost)/(2*slope)` |
| matrix | Constant-sum game | best response index | `Best response to a fixed opposing move from a fixed source payoff matrix` |
| matrix | Zero-sum game | best response index | `Best response to a fixed opposing move from a fixed source payoff matrix` |
| mechanism | First Price Auction | bounded scalar | `value-own_bid` |
| mechanism | Majority Voting | bounded scalar | `support_votes-(total_votes/2)` |
| mechanism | Median Voter | bounded scalar | `median_position-proposed_position` |
| mechanism | Public Project Mechanism | bounded scalar | `total_benefit-project_cost` |
| mechanism | Vickrey Auction | bounded scalar | `value-second_highest_bid` |
| network | Congestion Game | bounded scalar | `base_delay+slope*load` |
| population | Collective Herding | bounded scalar | `total_signal-agents*coordination_cost` |
| population | El Farol Bar | bounded scalar | `attendance_probability*attend_payoff+(1-attendance_probability)*stay_payoff` |
| population | Market Entry | bounded scalar | `market_revenue-competition_cost*entrants` |
| population | Minority Game | bounded scalar | `win_probability*winning_reward+(1-win_probability)*losing_reward` |
| resource | All Pay Auction | bounded scalar | `win_probability*prize-bid` |
| resource | Budget-Constrained Defense | bounded scalar | `budget-interception-spare_reserve` |
| resource | Tullock Contest | bounded scalar | `prize*effort/(effort+opponent_effort)-cost_per_effort*effort` |
| risk | Distributionally Robust Game | bounded scalar | `max(expected_loss_model_one,expected_loss_model_two)` |
| risk | Regret Minimization | bounded scalar | `best_available_payoff-chosen_payoff` |
| risk | Robust Security | bounded scalar | `max(loss_case_one,loss_case_two)+mitigation_cost` |
| security | Attacker Defender Game | bounded scalar | `attack_probability*(1-detection_probability)*damage+defense_cost` |
| social_dilemmas | Collaboration game | best response index | `Best response to a fixed opposing move from a fixed source payoff matrix` |
| social_dilemmas | Collective-risk social dilemma | bounded scalar | `endowment-contribution-risk_probability*loss` |
| social_dilemmas | Public Goods | bounded scalar | `contribution*multiplier/participants-contribution` |
| social_dilemmas | Snowdrift game | best response index | `Best response to a fixed opposing move from a fixed source payoff matrix` |
| social_dilemmas | Threshold Public Goods | bounded scalar | `endowment-contribution+bonus` |
| social_dilemmas | Tragedy of Commons | bounded scalar | `benefit*harvest-cost*harvest*harvest` |
| timing | Grab the Dollar | bounded scalar | `prize-split_penalty` |
| timing | Preemption | bounded scalar | `first_mover_reward-delay_cost*delay` |
| timing | War of Attrition | bounded scalar | `prize-cost_per_round*waiting_rounds` |

## Run commands from repository root

```powershell
& $py -m tools.nanojev_gamer_v1_handoff prepare `
  --output runtime\reports\gamer-v1\catalog-004 `
  --include-catalog-extensions

& $py -m tools.nanojev_gamer_v1_handoff lora `
  --output runtime\reports\gamer-v1\catalog-004 `
  --include-catalog-extensions `
  --init-lora-from runtime\reports\gamer-v1\games-003 `
  --init-lora-checkpoint latest `
  --lora-steps 2000 `
  --lora-eval-every 200 `
  --device cuda
```

The source run (`games-003` in this example) must already have a valid `best_adapter`, finalized or durable source manifest, and non-pending `latest.pt`. The cross-dataset transfer imports *weights* only; a new optimizer is necessary. If the source does not exist, choose the latest verified completed Gamer run instead. To continue an interrupted new run, use the same command and add `--resume`.

## Certification invariants

- Source catalog membership and family/title match for all 58 entries.
- Each example is regenerated from exact game ID, seed, index and attempt; altered prompts, factors, results and scopes are refused.
- One-step formulas are independently recomputed from restricted source expressions and compared with arithmetic-factor reconstruction.
- Separate source signatures for legacy and extended rows preserve old datasets and report lineage.
- Zero-line sampling includes exact cancellations and ±1 margins for nine new named subproblems.
- Old 33-game corpus is byte-for-byte identical with `--include-catalog-extensions` disabled.
