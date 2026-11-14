# Gamer v1 — Fourth Table: State–Action–Transition–Reward (v13)

## Scope and certification

- Optional curriculum includes **33 native-verified + 58 bounded catalog objectives + 55 bounded transition objectives = 146 named entries**.
- **18 cases per entry** yields 2,628 examples: 1,752 train, 438 dev, 438 test. Fourth table applies to the 990 new cases. The 55 additions are bounded **one-step** source-catalog mathematical problems, not complete strategic simulators or proofs of equilibrium.
- Anonymous randomized game identities remain retired. Numerical parameters for each named, explicit game rule are varied deterministically by seed; labels are checked independently from the arithmetic factor table.
- The fourth table does **not** replace existing calculation, payoff, or probabilistic-outcome tables. It adds state, actor, information available, action/event, transition probability, next state, reward and terminal status.
- Both the LoRA and C0 head consume the **original human-readable *08 prompt** independently. The LoRA produces a sparse table with results blank. The numerical head does not read the generated table.
- Hidden-information cases describe **possible types and likelihoods** without revealing the realized private type to the acting receiver. Probability mass is checked to sum to one separately per conditional outcome set.
- Source catalog is `tools/nanojev_game_transformation_library.py`; an entry there is only a title and family equation. Each new one-step rule is explicitly registered in `tools/nanojev_gamer_v1_transition_games.py`.

## Fourth-table columns

| State | Actor | Information available | Action / event | Probability | Next state | Reward | Terminal |
|---|---|---|---|---:|---|---|---|
| Current state or prior belief | Decision maker or chance | Observations; never private realized type | Chosen action or chance outcome | Conditional transition probability | Symbolic destination; numerical result blank | Blank when computed | Yes/no |

All calculated numerical results in the separate factor table are also kept blank. A probability table can accompany the fourth table for signaling and stochastic games.

## Mathematical kernels (explicit bounded subproblems)

| Kernel | One-step mathematical objective | Catalog entries |
|---|---|---:|
| `coalition` | `joined_value-standalone_value-join_cost` | 3 |
| `markov` | `reward_now+discount*(p_good*value_good+(1-p_good)*value_bad)` | 6 |
| `navigation` | `gap+target_speed-pursuer_speed` | 2 |
| `offer` | `offer-discount*counteroffer` | 4 |
| `population` | `population+arrivals-departures` | 3 |
| `quorum` | `support-threshold` | 5 |
| `regret` | `prior_regret+best_reward-chosen_reward` | 3 |
| `repeat` | `bank+stage_reward*discount` | 8 |
| `resource` | `remaining-used` | 2 |
| `security` | `asset-defense_cost-breach_probability*loss` | 4 |
| `signal` | `prior*likelihood_high/(prior*likelihood_high+(1-prior)*likelihood_low)` | 11 |
| `timing` | `next_prize-wait_cost-stop_prize` | 4 |

## Exact 55-game crosswalk

| Family | Named catalog game | Bounded mathematical task |
|---|---|---|
| bargaining | Alternating Offers (`alternating_offers`) | An offer can be accepted now or rejected for one discounted counteroffer. |
| bargaining | Coalition Bargaining (`coalition_bargaining`) | One coalition offer/counteroffer comparison with explicit outside-value discount. |
| bargaining | Rubinstein Bargaining (`rubinstein_bargaining`) | One time-discounted offer/counteroffer comparison, not infinite-horizon equilibrium. |
| byzantine | Proof of Stake Validator Game (`proof_of_stake_validator_game`) | One validator stake-support decision compares present stake weight with approval threshold. |
| byzantine | Quorum Governance (`quorum_governance`) | One governance proposal uses the signed quorum margin. |
| byzantine | Threshold Escalation (`threshold_escalation`) | One escalation transition compares supporting votes with required threshold. |
| captain | Captain Bridge Defense (`captain_bridge_defense`) | One bridge-defense action faces a stated chance of loss. |
| captain | Captain Coalition Negotiation (`captain_coalition_negotiation`) | One captain-led negotiation compares present offer with a discounted next offer. |
| captain | Captain Crew Evacuation (`captain_crew_evacuation`) | One evacuation action changes the remaining crew aboard. |
| captain | Captain Evidence Discipline (`captain_evidence_discipline`) | One observed evidence signal updates a prior without revealing the true state. |
| captain | Captain Hull Preservation (`captain_hull_preservation`) | One damage-control action changes remaining hull integrity. |
| captain | Captain Pursuit (`captain_pursuit`) | One pursuit time step updates signed ship-target separation. |
| captain | Captain Relative Navigation (`captain_relative_navigation`) | One navigation time step updates signed relative position. |
| captain | Captain Shipboard Consensus (`captain_shipboard_consensus`) | One shipboard decision compares supporting votes against a stated quorum. |
| coalition | Alliance Prisoners Dilemma (`alliance_prisoners_dilemma`) | One alliance decision compares combined coalition value with standing-alone value and cost. |
| coalition | Coalition Formation (`coalition_formation`) | One coalition join decision compares added coalition value with the membership cost. |
| coalition | Colluders Council (`colluders_council`) | One colluding council decision compares coalition value with outside value and coordination cost. |
| deception | Adversarial Signaling (`adversarial_signaling`) | One deceptive message updates receiver belief under stated adversarial likelihoods. |
| deception | Bluffing (`bluffing`) | One bluffing observation updates belief over true/false private states. |
| evolutionary | Invasion Game (`invasion_game`) | One finite population update following invader arrivals and incumbent departures. |
| evolutionary | Population Snowdrift (`population_snowdrift`) | One discrete snowdrift population update with arrivals and departures. |
| information | Adverse Selection (`adverse_selection`) | One observable quality indicator updates the prior over hidden quality types. |
| information | Bayesian Coordination (`bayesian_coordination`) | One public coordination signal updates a two-type prior. |
| information | Cheap Talk (`cheap_talk`) | One cost-free message updates a prior using specified message likelihoods. |
| information | Costly Signaling (`costly_signaling`) | One costly message updates a prior; the numerical target is belief, not net signaling equilibrium. |
| information | Electronic Mail Game (`electronic_mail_game`) | One observed message updates the belief about an unobserved event. |
| information | Information Cascade (`information_cascade`) | One public observation updates a prior without revealing the hidden type. |
| information | Lewis Signaling (`lewis_signaling`) | One received signal updates a finite prior belief over two hidden possible types. |
| information | Sender Receiver Game (`sender_receiver_game`) | One receiver observation updates the prior over sender types. |
| matrix | Shapley stochastic-matrix example (`shapley_stochastic_matrix_example`) | One state-action choice in a finite stochastic matrix game; the opponent action is fixed. |
| mechanism | Voting Agenda (`voting_agenda`) | One agenda transition is decided by the signed votes-over-threshold margin. |
| population | Population Congestion (`population_congestion`) | One population migration update has stated arrivals and departures. |
| repeated | Reciprocity (`reciprocity`) | One repeated reciprocal-response round credits a stated payoff. |
| repeated | Repeated Arms Race (`repeated_arms_race`) | One arms-race stage credits a stated resource-adjusted payoff. |
| repeated | Repeated Cooperation (`repeated_cooperation`) | One cooperative stage adds a discounted known payoff to cumulative utility. |
| repeated | Repeated Inspection (`repeated_inspection`) | One observed inspection round credits a stated inspection payoff. |
| repeated | Reputation (`reputation`) | One public reputation round adds the stated stage reward to discounted value. |
| repeated | Trigger Strategy (`trigger_strategy`) | One stage following a trigger-state observation credits the stage payoff. |
| repeated | Trust Game (`trust_game`) | One repeated trust round credits an explicitly stated return. |
| risk | Adversarial Bandits (`adversarial_bandits`) | One adversarial reward reveal updates cumulative realized regret. |
| risk | Adversarial Self Play (`adversarial_self_play`) | One self-play result updates cumulative regret for a specified actor. |
| risk | Online Learning Game (`online_learning_game`) | One online decision updates cumulative regret against a stated comparator. |
| security | Cyber Defense (`cyber_defense`) | One defensive cyber action faces a stated probability of breach and fixed loss. |
| security | Electronic Warfare Game (`electronic_warfare_game`) | One electronic-defense action has a stated disruption probability and loss. |
| security | Inspection Game (`inspection_game`) | One defensive inspection action faces a stated chance of undetected loss. |
| social_dilemmas | Iterated Prisoners Dilemma (`iterated_prisoners_dilemma`) | One publicly observed cooperation/defection stage with discounted reward accounting. |
| stochastic | Discounted Stochastic Game (`discounted_stochastic_game`) | One state-action Bellman backup with stated transition probability and discount. |
| stochastic | Dynamic Security Game (`dynamic_security_game`) | One stochastic defensive action with two next-state continuation values. |
| stochastic | Markov Game (`markov_game`) | One observed state and fixed joint action have two possible next states. |
| stochastic | Partial Observable Stochastic Game (`partial_observable_stochastic_game`) | One belief-state prediction using explicit next-state probabilities, not hidden realized states. |
| stochastic | Stochastic Bargaining (`stochastic_bargaining`) | One stochastic bargaining state with two specified next-offer values. |
| timing | Centipede (`centipede`) | One finite centipede decision: continue for the next prize or stop for the current prize. |
| timing | Escalation and Brinkmanship (`escalation_and_brinkmanship`) | One escalation decision: compare the next prize minus escalation cost with stopping. |
| timing | Investment Timing (`investment_timing`) | One investment decision: compare next-period opportunity with present stop payoff. |
| timing | Timing Duel (`timing_duel`) | One timing duel decision: wait one tick at a known cost versus stopping now. |

## Source lineage and acceptance

1. A new run must use a **new output folder** and `--include-transition-games`. That flag also includes the previous 58 catalog games; it does not silently mutate the old 33- or 91-game experiment manifests.
2. `prepare` independently regenerates each source-identified case with a deterministic seed; `validate` re-computes all cases and refuses mismatched source hashes, corrupted tables, missing case rows or any anonymous random game.
3. Each one-step oracle explicitly computes its own equation; the sparse factor DAG re-derives the result and must match exactly as a rational number. Conditional probability groups must each sum to one.
4. New training transfers adapter weights from a chosen old run but starts a **fresh optimizer** when the dataset changes. An unchanged v13 dataset can continue beyond the step limit with `--resume --lora-steps N`.
5. A 17-prompt bounded development probe now fixes six anchors evenly across original, prior bounded, and transition curricula and rotates other cases. **It is not full retention certification**. The final `test` split must be evaluated independently before promoting a model.
6. This work does not implement full policies, equilibria, multi-step tree search, causal planning, or hidden-information inference beyond the stated one-step mathematical target. Other 82 named entries from the 137-game remainder still need additional verified rules or structures.

## Example invocation (PowerShell)

```powershell
& $py -m tools.nanojev_gamer_v1_handoff prepare `
  --output runtime\reports\gamer-v1\transition-005 `
  --include-transition-games

& $py -m tools.nanojev_gamer_v1_handoff lora `
  --output runtime\reports\gamer-v1\transition-005 `
  --include-transition-games `
  --init-lora-from runtime\reports\gamer-v1\catalog-004 `
  --init-lora-checkpoint latest `
  --lora-steps 3000 --lora-eval-every 200 --device cuda
```

Replace `catalog-004` with the **actual completed and non-pending** run containing the weights you want to preserve. The new `--lora-steps` target must exceed that parent checkpoint step; use `--resume` only for an interrupted run using the **same** v13 dataset.
