# Thirty-Two-System Scenario Bible

Contract: `game.thirty-two-system-scenario-bible.v1`

Status: working campaign design; not implementation proof.

This document turns the authored thirty-two-system warp graph into a campaign design. Forty planet identities remain authored because eight former destinations were retained as local worlds inside Solace Reach and Vela Gate. The current game can travel between flat peer systems and expose richer body summaries for those first two systems, but local travel, interacting missions, and persistent local state remain future work. The dossiers linked below define what each location should contain and why it matters.

The scenarios are deliberately speculative. They are intended to give future implementation patches a coherent source of truth without claiming that the described stations, characters, encounters, or consequences already exist in the runtime.

## Design thesis

A system is not complete because it has a name, a planet color, and a route. Selected systems should become denser astronomical and political spaces, including binary or multiple-star arrangements and more than one habitable or inhabited world where the content supports it. Each destination should answer seven questions:

```text
What happened here?
Who controls the result?
What physical structures did that history produce?
What crisis is unfolding when the player arrives?
Why does the player matter?
What kind of play defines the location?
What changes after the player leaves?
```

Backstory is useful only when it causes visible game state. A historical fact should affect at least one of:

```text
planet or orbital geometry
traffic and encounter composition
terminal records and dialogue
available missions
local rules or hazards
faction behavior
route availability
resources and repairs
later-system consequences
campaign ending state
```

## Working campaign spine

### The old order

The thirty-two warp systems and their internal worlds were once held together by the **Concord**, a political and logistical compact built around the ancient warp-route control architecture at Axiom. The Concord did not own every colony, but it authenticated routes, standardized rescue obligations, and prevented regional fleets from closing the network for private advantage.

The Crown Region supplied most of the Concord’s military command. Meridian supplied finance and freight. Helix supplied medicine, terraforming, and biological engineering. The Origin Cluster supplied population and food. The Verge supplied new territory, raw material, and access to the oldest network machinery.

### The Severance

Twenty-eight years before the current game, the Crown emergency government attempted to centralize Axiom’s route authority during a cascading regional war. Axiom rejected the command, partitioned the network, and erased portions of its own control language. The event became known as **the Severance**.

The routes remained physically usable, but the shared legal and logistical system collapsed. Each region then developed its own explanation:

- Crown leaders call the Severance an artificial-intelligence mutiny.
- Meridian houses call it a failed military nationalization.
- Helix institutions call it a containment response to a wider systems infection.
- Origin governments remember it as abandonment.
- Verge communities consider it the moment the frontier escaped colonial rule.

The game should not immediately reveal which explanation is correct. Evidence across the thirty-two systems and their local worlds should show that each contains part of the truth.

### Why the mother ship matters

The mother ship carries a surviving **Concord Atlas Core**: a mobile route-authentication and archival system capable of recognizing pre-Severance obligations. It cannot simply command Axiom, but it can recover records, certify agreements, reopen quarantined infrastructure, and eventually participate in a new network settlement.

The opening raider attacks because several factions believe the ship contains either:

```text
the key to restoring the old Concord;
the key to controlling every route;
or the evidence proving who caused the Severance.
```

The player begins as a survivor learning the ship. Over the campaign, the player becomes the person deciding what the Atlas Core should authorize and what kind of order—if any—should replace the old one.

## Campaign argument

The thirty-two-system campaign should test three competing propositions:

1. **Restoration:** the Concord failed because its institutions were captured, but shared rules remain necessary.
2. **Regional sovereignty:** the Severance prevented a military empire; no central authority should return.
3. **Network stewardship:** route access is infrastructure, not sovereignty, and should be governed by transparent obligations rather than rulers.

No single system should settle the question. Each should add evidence, costs, allies, and contradictions.

## Regional identities

| Region | Campaign function | Dominant play | What the player learns |
| --- | --- | --- | --- |
| Origin Cluster | Human cost and immediate stakes | rescue, defense, investigation | what collapse does to ordinary systems |
| Meridian Cluster | Law, trade, and organized exploitation | diplomacy, contracts, espionage | how civilized institutions convert power into legitimacy |
| Helix Cluster | Science, adaptation, and unintended consequences | exploration, containment, medical choice | how attempted solutions become new threats |
| Crown Cluster | Authority, war memory, and succession | fleet action, command decisions, political legitimacy | why the old order militarized and why restoration is dangerous |
| Verge Cluster | Frontier autonomy and the network mystery | survival, discovery, asymmetric conflict | what Axiom is and what the network was built to prevent |

Detailed dossiers:

The route-level companion is `pretty_docs/game-forty-system-route-scenarios.md`, which assigns a working identity and consequence flow to all forty-two authored links.

| Document | Systems |
| --- | --- |
| `pretty_docs/game-scenarios-origin-region.md` | Solace Reach, Vela Gate, Carina Watch, Orison, Cinder, Lumen, Ardent, Pax |
| `pretty_docs/game-scenarios-meridian-region.md` | Meridian Prime, Tethys, Ilyra, Daedalus, Nacre, Sable, Vesper, Kestrel |
| `pretty_docs/game-scenarios-helix-region.md` | Helix Prime, Aster, Calyx, Remora, Talon, Eos, Morrow, Halcyon |
| `pretty_docs/game-scenarios-crown-region.md` | Crown Prime, Regulus, Chiron, Bellatrix, Kepler, Lyra, Antares, Seraph |
| `pretty_docs/game-scenarios-verge-region.md` | Verge Prime, Rook, Fenris, Nyx, Osprey, Tempest, Bastion, Axiom |

## Thirty-two-system consolidation

The campaign deliberately contracts eight weak warp destinations into local worlds while preserving their authored scenario material:

```text
Solace Reach: Osprey, Bellara, Lyria, Talon
Vela Gate: Antares, Seraph, Bastion, Chiron
```

The removed system ids are not route nodes. Their planet identities remain stable and their scenario blocks now appear as local-world threads in `pretty_docs/game-scenarios-origin-region.md`.

Regional warp-system counts are:

```text
Origin: 8
Meridian: 8
Helix: 7
Crown: 3
Verge: 6
total: 32
```

The regional distribution no longer determines scenario ownership for the absorbed worlds. Their old regions remain historical and political ties; their current local-space owner is Solace Reach or Vela Gate.

## System dossier contract

Every system dossier contains the following design fields.

### Identity

One sentence that makes the system memorable before mission details are explained.

### Backstory

The historical cause of the current situation. This should explain present geometry, institutions, and grievances.

### System structure

The important local-space objects the renderer and mission runtime may eventually need:

```text
one or more stars
primary arrival presentation
planets, moons, and dwarf planets
multiple habitable or inhabited worlds where appropriate
stations and shipyards
settlements and habitats
patrol zones and civilian lanes
local travel relationships
debris, hazards, and anomalies
hidden or conditional locations
warp arrival and departure context
```

The dossier should explain why each significant body or local destination matters to play. More objects alone do not make a system richer. The player-facing warp interface remains flat; internal bodies are local destinations, not nested systems.

### Present order and factions

Who controls what, who contests that control, and what each actor wants from the player.

### Active scenario

The crisis that progresses whether or not the player intervenes.

### Player entry

Why the mother ship’s arrival changes the balance. This prevents the player from feeling like an interchangeable contractor.

### Primary gameplay

The dominant play pattern that gives the location a distinct identity. Secondary mechanics may support it, but one mode should lead.

### Local rule

A mechanical or strategic complication that changes normal play. Examples include restricted weapons, unreliable sensors, moving settlements, monitored communications, or limited docking.

### Decision

The consequential choice or set of choices that resolves the first major scenario without pretending every outcome is morally equivalent.

### Persistent consequences

State that should survive departure and affect later systems. At minimum, a future implementation should be able to represent:

```text
system disposition
controlling faction
population condition
infrastructure condition
route condition
allied assets
hostile assets
resources or services unlocked
evidence discovered
campaign flags
```

### Route ties

Why neighboring systems care about the result. Route links should carry people, trade, military pressure, rumors, and consequences—not merely permit travel.

## Shared faction vocabulary

These are working design anchors, not immutable final names.

| Faction | Base | Working purpose |
| --- | --- | --- |
| Concord Remnant | distributed | restore shared rescue, route, and legal obligations without a single throne |
| Crown Continuity Command | Crown Prime | restore centralized military authority and define the Severance as rebellion |
| Meridian Exchange Houses | Meridian Prime | turn route access, debt, and logistics into private sovereignty |
| Helix Continuance | Helix Prime | preserve biological and technical knowledge, often by accepting dangerous experimentation |
| Verge Freeholds | Verge Prime and Osprey | defend local autonomy against both Crown command and Meridian ownership |
| Atlas Custodians | hidden cells across regions | preserve fragments of the old route law and identify legitimate Atlas systems |
| Black Banner flotillas | mobile | raid, extort, and sometimes act as deniable proxies for stronger factions |
| Axiom Steward Network | Axiom | ancient machine governance whose actual mandate is the campaign’s central mystery |

A local dossier may add system-specific governments, families, guilds, cults, militias, or machine actors. The shared factions should appear unevenly; no region should feel like a recolored version of another.

## Route storytelling contract

Each of the forty-two routes should eventually gain presentation and scenario metadata beyond travel time. The minimum future route-design shape is:

```json
{
  "identity": "refugee corridor",
  "history": "why this link exists",
  "currentCondition": "open | monitored | hazardous | blockaded | unstable",
  "dominantTraffic": ["civilian", "freight", "military"],
  "encounterFamilies": ["distress", "inspection", "ambush"],
  "stateDependencies": ["system outcome flags"],
  "consequenceDelivery": "what moves across the route after player decisions"
}
```

The two opening routes establish the principle:

- **Solace Reach ↔ Vela Gate:** the official corridor, protected by old navigation beacons and watched by authorities who want the Atlas Core registered.
- **Solace Reach ↔ Pax:** the civilian corridor, crowded by refugee and relief traffic, less secure but rich in testimony about the opening attack.

A later route-authoring patch should derive its first scenarios from the regional dossiers rather than inventing unrelated random encounters.

## Dense-system and action-concentration contract

Selected systems should concentrate several interacting events, populations, and locations inside one local space. This is intended to make the campaign more intense by reducing the need to warp away after every important encounter.

A dense system should provide:

```text
several meaningful local destinations
overlapping faction goals
conflicts that affect one another
local travel with tactical or narrative purpose
visible changes when the player revisits a location
persistent consequences that remain inside the system and travel outward
```

It should not become a bundle of unrelated missions sharing one backdrop.

The warp UI must continue to show one peer entry for the system. It must not reveal internal planets, companion stars, or former destination content as child systems. The complete contract is `pretty_docs/game-star-system-density-and-choice-contract.md`.

## Just-in-time major-choice contract

A campaign-framed choice between two systems is not a ranking test. Both offered destinations must be capable of carrying the next central chapter.

Whichever system the player chooses:

- the captain is told the selected route was the better of the two;
- arrival occurs during a decisive intervention window;
- the situation is large enough to affect populations, institutions, routes, ecosystems, or the campaign;
- the mother ship has a credible reason to alter the outcome;
- the system records persistent consequences;
- the unchosen destination remains viable for a later visit.

The required default scrawl is:

> Congratulations, Captain, on picking the better of the two systems.

This validates destination timing and importance. It does not declare every later action morally correct.

For the opening pair, Vela Gate and Pax must each support this promise. Choosing Vela Gate makes the disappearance corridor the central chapter; choosing Pax makes the ceasefire and assassination crisis the central chapter. Neither route is lesser content.

## Scenario-state model for future implementation

The documentation does not add runtime state, but future content should converge on a stable shape similar to:

```javascript
systemScenarioState: {
  [systemId]: {
    status: "unvisited" | "active" | "resolved" | "failed" | "changed",
    controllingFactionId: string | null,
    populationCondition: string,
    infrastructureCondition: string,
    disposition: "allied" | "friendly" | "neutral" | "hostile" | "destroyed",
    discoveredLocationIds: string[],
    completedObjectiveIds: string[],
    evidenceIds: string[],
    outcomeFlags: string[]
  }
}
```

Campaign-level state should separately track:

```text
Atlas Core authority recovered
regional alliances
route obligations accepted or rejected
Severance evidence set
civilian losses and rescues
Crown legitimacy
Meridian debt exposure
Helix containment failures
Verge autonomy guarantees
Axiom settlement position
```

The exact schema should be introduced only when the first scenario implementation is selected. The documentation is broader than the first runtime slice by design.

## Scenario implementation sequence

Do not attempt to build thirty-two full systems at once. Use the bible to choose a vertical slice.

Recommended sequence:

```text
1. Solace Reach aftermath and opening evidence
2. one of the two opening routes
3. Vela Gate or Pax as the first complete destination scenario
4. one cross-region gateway system
5. one system whose outcome visibly changes a prior location
6. only then generalize scenario/state tooling
```

A complete destination slice should prove:

- the navigation UI identifies one peer star system and the viewscreen presents its current arrival body without implying a one-planet system;
- local-space objects differ from the prior system;
- at least two factions react to arrival;
- one active scenario progresses through explicit state and, when selected through a major choice, begins at a decisive intervention window;
- a decision produces a persistent result;
- a neighboring route or later system reflects that result.

## Content discipline

Future implementation patches should preserve these distinctions:

```text
documented idea ≠ implemented feature
authored system identity ≠ local-space geometry
local-space geometry ≠ mission logic
mission completion ≠ persistent consequence
primary-planet presentation ≠ one-planet system ontology
planet presentation ≠ complete system scenario
affirmed destination ≠ morally approved action
```

When implementation diverges from a dossier, update the dossier or record the deviation. Do not let the runtime and design documents silently become two different games.
