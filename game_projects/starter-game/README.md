# Starter Game

This repo-backed Main Computer game project is a simple first-person federation-like shuttlecraft scene.

- `project.json` starts the player as `hero-sprite` / “Player Cadet” inside the shuttle cabin.
- The scene uses the `shuttle-3d` projection and a raw WebGL triangle renderer whose floor, ceiling, side walls, bulkheads, viewport opening, fixtures, stars, and mother ship are built from real vertices.
- Click the Game Surface to focus it. Use **W/A/S/D** to walk, hold **Shift** to sprint, and drag or use the arrow keys to look around.
- Movement is bounded by the shuttle hull and uses fixture collision boxes for the forward consoles, side consoles, seats, and aft hatch.
- The forward viewport shows a starfield plus the nearby mother ship.
- Shuttle consoles, the forward viewport, seats, hatch, hull ribs, player presence, starfield, mother ship, and ambient particle cues are authored as scene objects.
- `hero-arc-bolt` is intentionally retained as the forward sensor sweep emitter so the existing GPU Forge prebuilt atlas workflow still has a stable default target.
- `assets/` can hold authored shuttle panels, sprites, audio, VFX masks, and future frame data.
- `scripts/` can hold encounter logic, ship systems, onboarding prompts, and editor automation helpers.

The player camera now moves in first person inside the enclosed vertex-built hull. The walls continue to occlude space correctly, so the stars and mother ship are only visible through the modeled forward opening.

## VFX density controls

The default scene starts at 2x particle density and 1.5x effect intensity. Use the Game Editor VFX Density sliders or preset buttons to tune the cabin haze, console glow, starfield, and sensor sweep without editing JSON by hand.
