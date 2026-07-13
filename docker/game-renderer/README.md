# Game Renderer / GPU Forge scaffold

This container is the game-side sibling of the Astrometric renderer pattern.  It is
not meant to repaint the whole game screen.  The browser remains authoritative for
input, scene composition, hit testing, UI, and animation timing.

The intended contract is asset baking:

- `GET /health`
- `POST /bake/effect-atlas`

The initial image is a smoke/procedural service so Main Computer can wire the
control plane and UI without depending on a CUDA renderer.  A future GPU backend
can replace the implementation while preserving the same JSON contract and local
project-asset output path.
