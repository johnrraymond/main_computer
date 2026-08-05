# Game AI Patch 10B — Pax: Neutrality Under Fire

Contract: `game.ai.patch-10-b.pax-neutrality-under-fire.v1`

Status: implemented one complete Pax destination scenario on top of the stable character-policy foundation.

## Stage 1 — Scenario definition

Pax is not a generic combat stop. Its authored identity is:

> A peaceful world whose neutrality is maintained by hiding the violence that pays for it.

The playable scenario is **Neutrality Under Fire**.

```text
arrive at Pax
→ accept conference protection detail
→ protect refugee witness Nera Saye
→ stop a Quiet Service assassin
→ collect evidence before authorities blame refugees
→ open the emergency conference
→ choose the Pax settlement
→ persist consequences
```

### Cast

```text
npc.pax.refugee-witness-01
npc.pax.neutrality-marshal-01
enemy.pax.quiet-service-assassin-01
ship.pax.quiet-service-cutter-01
```

- **Nera Saye** carries testimony linking covert weapons to a concealed detention transfer.
- **Marshal Oren Vale** represents lawful conference security and warns the player that intimidation can collapse the ceasefire.
- **The Quiet Service assassin** attempts to silence the witness and can request extraction support.
- **The Quiet Service cutter** shadows, jams, and attempts to extract the attacker.

### Local rule

Defensive force can protect the conference. Unsupported intimidation damages the player’s political options.

Weapon discharges are classified as:

```text
during the live protection stage → defensive
after the attacker is neutralized → intimidation
```

This conduct record is separate from combat damage and is preserved in scenario state.

### Evidence

```text
evidence.pax.weapon-serial
evidence.pax.refugee-testimony
evidence.pax.cutter-signal
```

At least two evidence threads are required to open the emergency conference.

### Resolutions

1. **Expose the Quiet Service**
   - requires the weapon serial and cutter signal;
   - requires no intimidation discharges.

2. **Controlled disclosure**
   - requires any two evidence threads;
   - tolerates at most one intimidation discharge.

3. **Give refugees formal power**
   - requires all three evidence threads;
   - requires no intimidation discharges.

4. **Withdraw**
   - always available;
   - leaves Pax under emergency internment and closes diplomatic access.

Each result persists consequences for refugee support, diplomatic access, food supply, Kestrel gateway politics, security posture, and force conduct.

## Stage 2 — Infrastructure

### Reusable system-scenario runtime

New browser-safe runtime:

```text
main_computer/web/applications/scripts/system-scenario-runtime.js
```

Schemas:

```text
game.systemScenarios.v1
game.systemScenarios.definition.v1
game.systemScenarios.state.v1
game.systemScenarios.campaignExtension.v1
```

The runtime provides:

- definition normalization and validation;
- project-scoped state ownership;
- navigation-bound active-system selection;
- explicit scenario stages;
- evidence requirements;
- character-completion transitions;
- player-action and weapon-conduct receipts;
- resolution gating;
- persisted consequences;
- snapshot restore;
- campaign-extension export and restore;
- deterministic definition fingerprints;
- no timers, polling, or direct game-state mutation.

### Character activation boundaries

The character runtime now supports:

```text
activeSystemIds
activeScenarioId
activeScenarioStages
supportVesselId
```

This prevents Solace characters from acting in Pax and prevents Pax characters from existing before the protection detail begins.

Support calls are no longer hard-coded to `ship.raider-01`. Each enemy may request only its authored vessel.

Cover points and known vessels can also be system-scoped.

### Game Surface integration

Navigation updates the active system-scenario runtime. The scene renderer supplies the current scenario context to every character perception.

The Pax panel is a direct child of the existing non-obscuring strategic dock and supports the shared expanded, compact, and collapsed modes.

## Stage 3 — Implemented scenario

### Arrival

The Pax panel appears only in `system.pax`.

The player sees:

- the conference crisis;
- the local defensive-force rule;
- the cutter’s current posture;
- a button to accept the protection detail.

### Protection

Starting the scenario activates the three Pax characters in the mother-ship bridge area.

The assassin can:

```text
call cutter support
→ pursue the player
→ attack
→ take cover after damage
→ retreat at low health
```

Nera and Marshal Vale can:

```text
warn player
→ take cover from visible threat
→ remember player protection
→ follow after protection
→ hold position
```

Phaser damage remains game-owned. Character policies cannot directly reduce health.

When the assassin is down, the scenario runtime advances exactly once to investigation.

### Investigation

The player can secure the weapon serial, witness testimony, and cutter signal.

Evidence actions are idempotent and generate scenario receipts.

### Conference

The runtime computes which settlements are available from evidence and conduct. Locked outcomes explain whether evidence or neutrality conduct is missing.

### Resolution

The selected outcome is immutable. Repeating the same resolution is idempotent; attempting a different result after completion is rejected.

The panel renders the persisted consequences rather than recomputing them from UI state.

## Stage 4 — Persistence boundary

The uploaded source snapshot does not contain AI-9.5 repository campaign saves.

Pax therefore persists through:

```text
main-computer.system-scenarios.state.v1:<project-id>
main-computer.character-ai.state.v1:<project-id>
```

Promotion adapters exist:

```text
game.systemScenarios.campaignExtension.v1
game.characterAI.campaignExtension.v1
```

A later repository-save patch should include both extensions atomically beside strategic and navigation envelopes.

## Changed files

```text
game_projects/webgl-demo/project.json
main_computer/web/applications.html
main_computer/web/applications/apps/webgl.html
main_computer/web/applications/scripts/system-scenario-runtime.js
main_computer/web/applications/scripts/character-ai-runtime.js
main_computer/web/applications/scripts/pax-scenario-interaction.js
main_computer/web/applications/scripts/scene-viewer.js
main_computer/web/applications/scripts/webgl-desktop.js
main_computer/web/applications/styles/pax-scenario-interaction.css
tests/test_character_ai_runtime.py
tests/test_pax_system_scenario.py
tests/test_strategic_ai_panel_layout.py
pretty_docs/game-ai-patch-10-b-pax-neutrality-under-fire.md
pretty_docs/game-sophisticated-ai-architecture-plan.md
pretty_docs/game-mother-ship-design-index.md
pretty_docs/index.json
```

## Explicit boundaries

Not included:

- repository-backed campaign-slot promotion;
- free-form generated dialogue;
- a real remote model transport;
- surface or orbital flight around Paxora;
- additional assassins or reinforcement spawning;
- ship-to-ship damage against the cutter;
- dynamic voice acting;
- broader multi-system scenario generalization beyond the reusable state machine.

## Next slice

The smallest durable follow-up is repository promotion:

```text
restore AI-9.5 campaign saves
→ include strategic, navigation, character, and system-scenario extensions
→ reload fresh runtimes
→ reproduce Pax stage, evidence, force conduct, characters, and next legal action
```

After that, one real model call can be attached to Nera or Marshal Vale through `RemoteCharacterPolicy` without changing scenario authority.
