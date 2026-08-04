# Thirty-Two-System Route Scenario Seeds

Contract: `game.thirty-two-system-route-scenarios.v1`

Status: speculative route-content design; not runtime metadata.


The filename is retained for repository continuity, but the active topology is the thirty-two-system graph. Fifteen edges that touched removed system ids were replaced by seven contracted corridors. Absorbed planets remain local content inside Solace Reach and Vela Gate and never appear as route endpoints.

The current navigation graph contains forty-two bidirectional routes. The runtime correctly treats those routes as the authority for direct travel, but their present data carries only topology, presentation duration, and world-time cost. This document gives every route a working dramatic identity so travel can eventually carry local history and persistent consequences.

A route scenario should never be a detached random encounter table. It should express pressure moving between the systems it connects.

## Route content fields

| Field | Purpose |
| --- | --- |
| Identity | Player-facing idea that distinguishes the corridor |
| Default condition | Initial state before campaign outcomes modify it |
| Dominant traffic | Ships and people normally encountered |
| Scenario seeds | Encounters justified by the connected dossiers |
| Consequence flow | State that should propagate across the route |

## Major destination timing contract

Route topology determines which destinations are legal. Campaign framing determines when two legal routes become a major destination choice.

For a major paired choice:

```text
both routes lead to central authored content
whichever route is selected receives the better-choice affirmation
the selected destination reaches a decisive intervention window
the unchosen destination remains actionable later
```

The affirmation is not route metadata proving one edge objectively superior. It is a campaign presentation rule that makes the player's committed path feel timely and central.

The opening pair from Solace Reach is:

```text
Solace Reach → Vela Gate
Solace Reach → Pax
```

Vela Gate should bring the player to the beacon-corruption and disappearance crisis just in time to change it. Pax should bring the player to the ceasefire, assassination, and refugee crisis just in time to change it. Either branch receives:

> Congratulations, Captain, on picking the better of the two systems.

Routine jumps, return trips, and logistical travel do not require this presentation.

## Origin Cluster routes

| Route | Identity | Default condition | Dominant traffic | Scenario seeds and consequence flow |
| --- | --- | --- | --- | --- |
| Solace Reach ↔ Vela Gate | Official rescue corridor | Open, monitored, beacon-compromised | patrols, relief ships, registered freight | Inspections for the Atlas Core; cleanup teams hunting opening evidence; Vela reform can make the route trustworthy, while a corrupt outcome turns it into an extortion lane. |
| Vela Gate ↔ Carina Watch | Warning line | Open, militarized | sensor tenders, customs craft, militia | Phantom-contact alerts; ships diverted into storm zones; Carina reform reduces false encounters, while emergency command increases compulsory searches. |
| Carina Watch ↔ Orison | Long-listening corridor | Signal-noisy | relay tenders, pilgrim ships, reconnaissance craft | Conflicting forecasts; silent probes; manipulated threat reports; trustworthy Carina data improves Orison predictions. |
| Orison ↔ Cinder | Evacuation clock | Open, forecast-dependent | crawler parts, refugee hulks, weather probes | Prophecies alter convoy timing; volcanic distress calls; a compromised Choir may redirect aid for political purposes. |
| Cinder ↔ Lumen | Thermal exchange | Hazardous freight lane | heat-control equipment, ice cargo, engineers | Reactor modules, geothermal specialists, sabotage, and refugees; Cinder control determines whether Lumen receives public aid or family-controlled contracts. |
| Lumen ↔ Ardent | Biosphere quarantine line | Monitored | medical ships, seed carriers, research craft | Unknown organisms in ice samples; refugee screening; ecological contamination; choices on both worlds determine quarantine severity. |
| Ardent ↔ Pax | Bread corridor | Open, politically sensitive | food convoys, diplomats, civilian carriers | Food embargoes, biosphere protests, convoy protection; Ardent’s outcome directly changes Pax supply and conference stability. |
| Pax ↔ Solace Reach | Civilian relief route | Crowded, under-defended | refugee ships, food transports, witnesses | Rescue calls, hidden agents, testimony from the opening attack; Pax security policy determines whether refugees are protected or screened as threats. |

## Meridian Cluster routes

| Route | Identity | Default condition | Dominant traffic | Scenario seeds and consequence flow |
| --- | --- | --- | --- | --- |
| Meridian Prime ↔ Tethys | Creditor freight artery | Open, toll-controlled | bulk food, finance couriers, weather parts | Debt seizures, priority passage auctions, storm-aid convoys; Tethys public-control outcomes weaken Exchange leverage. |
| Tethys ↔ Ilyra | Water-and-grain exchange | Weather-sensitive | agricultural carriers, climate engineers | Blight inspections, storm diversions, seed smuggling; failures on either world increase famine encounters. |
| Ilyra ↔ Daedalus | Harvest-machine line | Open, contract-bound | machinery, migrant labor, estate cargo | Repossessed equipment, bonded crews, sabotage; worker control at Daedalus can support Ilyrian communes. |
| Daedalus ↔ Nacre | Fabrication-material corridor | Industrially polluted | yard components, biomaterials, worker transports | Toxic cargo, labor flight, corporate security; destruction of Daedalus yards reduces traffic and repairs. |
| Nacre ↔ Sable | Insured luxury lane | Quiet, heavily documented | high-value materials, auditors, private escorts | Disappearing claims, sick workers, evidence couriers; Nacre exposure creates archive pressure at Sable. |
| Sable ↔ Vesper | Black archive escape | Covert, intermittently blockaded | witnesses, intelligence craft, smugglers | Pursuit of stolen records, false identities, insurer assassins; Sable publication increases Crown interception. |
| Vesper ↔ Kestrel | Unofficial courier road | Open to trusted captains | smugglers, couriers, refugees | Route-code races, betrayed convoys, message interception; guild trust can make this the fastest information channel. |
| Kestrel ↔ Meridian Prime | Independent dispatch line | Economically pressured | couriers, negotiators, priority freight | Acquisition attempts, fuel embargoes, contract warrants; Kestrel independence limits Meridian’s information monopoly. |

## Helix Cluster routes

| Route | Identity | Default condition | Dominant traffic | Scenario seeds and consequence flow |
| --- | --- | --- | --- | --- |
| Helix Prime ↔ Aster | Clinical migration lane | Open, medically screened | patients, genetic material, administrators | Consent disputes, vanished children, quarantine orders; Helix containment choices affect inspection intensity. |
| Aster ↔ Calyx | Seed pipeline | Monitored, biologically active | terraformers, seed vaults, research crews | Altered seed cargo, baseline refugees, living hull growth; Calyx outcomes can close or transform the route. |
| Calyx ↔ Remora | Symbiosis quarantine | Hazardous | bio-ships, containment teams, mobile labs | Organisms attaching to hulls, missing researchers, competing quarantine rules; controlled symbiosis can unlock special travel benefits. |
| Remora ↔ Eos | Compressed recovery corridor | Open, medically monitored | repair cultures, injured workers, medical transports | Preserves the linked Remora and Eos pressures without a Talon warp stop; contamination, augmentation debt, and recovery choices now meet on one longer route. |
| Eos ↔ Morrow | Memory-and-sleep corridor | Quiet, medically controlled | therapists, cryogenic specialists, witnesses | Altered identities, thawed patients, disputed testimony; Eos evidence changes who Morrow trusts to manage awakening. |
| Morrow ↔ Halcyon | Humanitarian thaw route | Capacity-limited | medical convoys, awakened populations, supply ships | Triage disputes, failed cryo transports, political claimants; Morrow’s demographic choice can overwhelm Halcyon. |
| Halcyon ↔ Helix Prime | Sanctuary supply loop | Open, priority-managed | medicines, hospital staff, quarantine escorts | Diversion of scarce treatment, secret priority lists, outbreak scares; trust determines whether the player receives emergency care. |

## Crown Cluster routes

| Route | Identity | Default condition | Dominant traffic | Scenario seeds and consequence flow |
| --- | --- | --- | --- | --- |
| Crown Prime ↔ Regulus | Command procession | Militarized | officer transports, cadet fleets, ceremonial escorts | Competing orders, loyalty inspections, cadet mutiny; Crown succession determines which transponders are recognized. |
| Regulus ↔ Kepler | Compressed command-and-supply corridor | Militarized | cadet fleets, supply carriers, veterans | Carries the former Chiron and Bellatrix pressures as evidence, prototypes, conscription disputes, and fleet logistics between the surviving systems. |
| Kepler ↔ Crown Prime | Compressed narrative-and-legitimacy corridor | Monitored, politically volatile | media ships, diplomats, intelligence couriers | Carries the former Lyra, Antares, and Seraph consequences into Crown legitimacy, weapons policy, refugees, and public narrative. |

## Verge Cluster routes

| Route | Identity | Default condition | Dominant traffic | Scenario seeds and consequence flow |
| --- | --- | --- | --- | --- |
| Verge Prime ↔ Rook | Custody frontier | Suspicious, defense-controlled | militia, prison transports, investigators | Disputed warrants, escaped prisoners, automated interdiction; Freehold constitutional choices determine jurisdiction. |
| Rook ↔ Fenris | Exile road | Poorly maintained | released prisoners, clan traders, salvage ships | Identity disputes, refugee rescue, Custody Engine hunters; Rook outcomes change Fenris population pressure. |
| Fenris ↔ Nyx | Shadow-mineral route | Hazardous, covert | clan miners, smugglers, anonymous buyers | Drone pursuit, stolen concessions, identity trades; clan sovereignty changes who may legally move resources. |
| Nyx ↔ Tempest | Compressed shadow-energy corridor | Covert and storm-exposed | modified ships, energy carriers, rescue craft | Preserves Osprey-linked identity, rescue, and captain-coalition pressures while shortening the Verge loop. |
| Tempest ↔ Axiom | Compressed fortress approach | Strategically vital, machine-observed | energy tankers, defense fleets, survey craft | Preserves Bastion's preparedness and militarization consequences on the direct approach to Axiom. |
| Axiom ↔ Verge Prime | Steward channel | Quiet, machine-observed | Freehold delegates, survey craft, machine emissaries | Constitutional messages, missing surveys, Axiom tests of cooperation; Verge legitimacy shapes available final agreements. |

## Inter-region gateway routes

| Route | Identity | Default condition | Dominant traffic | Scenario seeds and consequence flow |
| --- | --- | --- | --- | --- |
| Vela Gate ↔ Meridian Prime | Official commercial gateway | Open, heavily inspected | registered freight, customs, financial delegations | Atlas registration demands, debt warrants, beacon manipulation; Vela control and Meridian legitimacy jointly set access. |
| Pax ↔ Kestrel | Civilian diplomatic shortcut | Open but politically fragile | couriers, refugees, negotiators | Ceasefire messages, covert weapons, witness escorts; Pax neutrality and Kestrel trust determine speed and safety. |
| Tethys ↔ Helix Prime | Climate-medicine gateway | Quarantine-capable | climate engineers, medical ships, biological samples | Storm disease, contested quarantine, emergency expertise; public-infrastructure choices affect Helix cooperation. |
| Ilyra ↔ Halcyon | Food-and-care lifeline | Priority-managed | food convoys, patients, medical supplies | Triage bribery, blight screening, refugee transport; Ilyrian seed outcomes directly change Halcyon capacity. |
| Sable ↔ Crown Prime | Archive confrontation line | Monitored and covertly contested | intelligence ships, legal delegations, strike craft | Evidence interception, archive subpoenas, assassination attempts; control of records can trigger or prevent Crown crisis. |
| Vesper ↔ Crown Prime | Sanctuary legitimacy corridor | Covert, symbolically protected | refugees, clergy, witnesses, claimants | Carries the former Seraph sanctuary and legitimacy pressure directly into the Crown capital. |
| Aster ↔ Verge Prime | Settlement dispute corridor | Politically hostile | colonists, genetic clinics, Freehold inspectors | Unapproved settlement programs, baseline refugees, sovereignty confrontations; Aster consent reforms can normalize relations. |
| Eos ↔ Rook | Identity-evidence corridor | Restricted | medical records, prisoners, investigators | Altered-memory appeals, false releases, witness recovery; combined evidence can overturn machine sentences. |
| Regulus ↔ Axiom | Military approach | Closed or challenged | cadet fleets, Crown scouts, machine sentries | War games becoming invasion, Axiom compliance tests, command mutiny; Regulus outcome determines whether the route opens peacefully. |
| Kepler ↔ Tempest | Crown-Verge compression bridge | Long, militarized, resource-sensitive | fleet logistics, energy convoys, intelligence craft | Preserves the former Antares-Bastion cross-region pressure as a direct link between Crown logistics and Verge defense. |

## Future runtime boundary

Route scenario data should eventually be authored separately from basic navigation topology. A future runtime may project route state through stable ids such as:

```javascript
routeScenarioState: {
  [routeId]: {
    condition: "open" | "monitored" | "hazardous" | "blockaded" | "closed",
    controllingFactionId: string | null,
    trafficProfileId: string,
    activeEncounterIds: string[],
    outcomeFlags: string[]
  }
}
```

The first route implementation should be one opening corridor, not a generic random-encounter engine. The route must visibly carry consequences from its two connected systems. Major-choice state, affirmation presentation, decisive arrival timing, and unchosen-destination evolution should be campaign/scenario state layered above route topology rather than hidden inside the edge definition.
