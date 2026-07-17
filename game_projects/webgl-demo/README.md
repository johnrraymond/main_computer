# WebGL Demo

This repo-backed Main Computer game project is a compact first-person shuttle boarding-defense scene.

- `project.json` starts the player as `hero-sprite` / “Player Cadet” inside a shortened federation-like shuttle cabin.
- The `shuttle-3d` projection uses a raw WebGL triangle renderer. The hull, fixtures, player phaser, boarding aliens, transport beams, mother ship, hostile alien raider, and star placeholders are built as geometry rather than a flat cockpit image.
- The starfield is a deterministic set of random placeholders on a camera-centered sphere, so it remains the permanent furthest visual layer while the player moves.
- The HUD shows player health, living boarders, kills, and phaser recharge state.
- Click the Game Surface to focus it. Use **W/A/S/D** to walk, **Shift** to sprint, drag or use the arrow keys to look, and click, press **Space**, or press **F** to fire the phaser. Press **R** to restart after defeat.
- Hostile aliens periodically transport onto four pads inside the shuttle, pursue the player around fixture collision boxes, and deal contact-range damage.
- The forward viewport shows the star sphere, nearby mother ship, and hostile alien raider. Shuttle walls continue to occlude external geometry.
- `hero-arc-bolt` remains the forward sensor sweep emitter so the existing GPU Forge prebuilt atlas workflow retains a stable default target.
- `assets/` can hold authored shuttle panels, alien meshes, sprites, audio, VFX masks, and future frame data.
- `scripts/` can hold encounter logic, ship systems, onboarding prompts, and editor automation helpers.

## Default combat tuning

The scene starts at 100 health. The Type-II phaser deals 34 damage with a 280 ms recharge. Boarders have 60 health, arrive at five-second intervals after the opening alert, and attack for 8 damage at close range. These values are editable under `metadata.shuttle3d.combat`.

## VFX density controls

The default scene starts at 2x particle density and 1.5x effect intensity. Use the Game Editor VFX Density sliders or preset buttons to tune ambient cabin effects without editing JSON by hand.
