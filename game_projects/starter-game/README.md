# Starter Game

This is the default repo-backed Phase 4 sprite/particle game project for the Main Computer Game Editor.

- `project.json` stores isometric sprite actors, parented particle emitters, linked spell projectiles, finale metadata, settings, asset references, and choreography beats.
- `assets/` can hold sprite sheets, audio, VFX masks, and future authored frame data.
- `scripts/` can hold encounter logic, cast-state triggers, and editor automation helpers.

The starter scene demonstrates the intended character technique: sprite actors remain the character model while layered particles, rune rings, chain bolts, starfall, nova rings, and shockwaves sell the spell-combat power. It intentionally avoids mesh actors, and model placeholders.

Left-clicking the isometric floor moves the main sprite/particle character to the clicked tile, Diablo-style, without adding WASD movement.

## VFX density controls

The default scene now starts at 4x particle density and 4x effect intensity. Use the Game Editor VFX Density sliders or 1x/2x/4x preset buttons to dial the showcase up or down without editing JSON by hand.
