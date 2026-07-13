# Starter Game

This repo-backed Main Computer game project is now a simple 3D federation-like shuttlecraft look-around scene.

- `project.json` starts the player as `hero-sprite` / “Player Cadet” inside the shuttle cabin.
- The scene uses the `shuttle-3d` projection and a lightweight CSS/DOM 3D renderer in the shared scene surface.
- Drag inside the Game Surface, or use the arrow keys, to look around the shuttle interior.
- The forward viewport shows a starfield plus the nearby mother ship.
- Shuttle consoles, the forward viewport, seats, hatch, hull ribs, player presence, starfield, mother ship, and ambient particle cues are authored as scene objects.
- `hero-arc-bolt` is intentionally retained as the forward sensor sweep emitter so the existing GPU Forge prebuilt atlas workflow still has a stable default target.
- `assets/` can hold authored shuttle panels, sprites, audio, VFX masks, and future frame data.
- `scripts/` can hold encounter logic, ship systems, onboarding prompts, and editor automation helpers.

The player is stationary for this first 3D pass: the goal is to establish the interior, mouse/keyboard look-around, and the forward view of space before adding walking or ship-system interactions.

## VFX density controls

The default scene starts at 2x particle density and 1.5x effect intensity. Use the Game Editor VFX Density sliders or preset buttons to tune the cabin haze, console glow, starfield, and sensor sweep without editing JSON by hand.
