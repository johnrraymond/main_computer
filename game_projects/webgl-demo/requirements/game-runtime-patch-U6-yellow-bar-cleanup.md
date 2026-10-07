# Patch U.6 — Elevated Yellow Bar Cleanup

Patch U.6 removes three legacy non-floor beam primitives from the mother-ship scene.

Removed:

- the Bay Ops entrance amber accent at `z = -8.82`
- the unsupported security-checkpoint amber header at `z = -12.0`
- the Engineering power-core vertical beam at `x = 5.4`, `z = -21.1`

These primitives had no collision, interaction, objective, or other gameplay behavior. The Engineering beam changed color with power state, but it still functioned only as anonymous visual decoration.

The patch deliberately leaves floor markings, threshold strips, room geometry, checkpoint side blocks, the Engineering power-core ellipsoid, interactions, and gameplay state unchanged.

No deletions of repository files are implied.
