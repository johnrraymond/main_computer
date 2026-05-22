# Starter Game

This is the default repo-backed isometric game project for the Main Computer Game Editor.

- `project.json` stores scenes, isometric sprite actors, particle emitters, settings, asset references, and metadata.
- `assets/` stores raw assets of any file type. The starter scene works without imported textures by drawing a CSS sprite-frame series for the main character.
- `scripts/`, `data/`, and `builds/` are prepared so the editor can add runtime logic, authored data, and exports later.

The starter scene uses a Diablo-style isometric projection with pseudo-3D sprite movement and particle effects layered into the same depth-sorted scene.
