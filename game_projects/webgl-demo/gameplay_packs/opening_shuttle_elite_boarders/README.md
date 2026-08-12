# Opening Shuttle: Elite Boarders

This is the working real-JavaScript gameplay-pack authoring surface for the opening shuttle encounter.

It is intentionally small: the current YAGNI runtime loads this pack, runs `setup(pack)` through the gameplay-pack command harness, and lets the shuttle scene apply only the commands this pack emits.

The pack shows how an AI-authored gameplay pack should feel:

- `defineGameplayPack(...)` declares the pack.
- `setup(pack)` registers gameplay hooks.
- `pack.encounter(...)` scopes behavior to the opening shuttle encounter.
- Encounter handlers emit gameplay commands through the pack SDK.
- The engine validates and applies those commands through the shuttle scene adapter.

The pack explicitly authors the live gameplay delta:

- shuttle raiders have 3x health;
- after two hostile defeats, an Elite Boarding Leader spawns;
- the HUD warns the player;
- the encounter completes when Haven orbit is reached;
- failure is declared when the player is defeated.

The pack still must not receive raw renderer, DOM, filesystem, network, save-state, or project mutation access. The current runtime is deliberately narrow and supports only the opening-shuttle commands this pack needs.
