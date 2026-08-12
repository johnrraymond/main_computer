# MCEL reference app and fixture roles

MCEL now keeps reusable mechanics in shared modules and keeps app-specific facts
in current app declarations or current profiles. App-specific wrappers are
compatibility entry points only when they still exist and are still referenced;
they should dispatch into shared machinery instead of owning projection,
evidence, proof, promotion, or browser-observation mechanics.

## Canonical authoring example

Calculator is the only canonical real MCEL app in this repository right now.
Use `mcel_apps/calculator/application.js` to understand the modern MCEL app
authoring surface.

Counter and Workbench are retired historical fixture names. They are not current
MCEL app assets, product-migration targets, or examples for new real app
authoring. Historical migration notes may still mention them by name, but current
docs should not point at absent fixture trees, absent fixture profiles, or the
retired Workbench/profiled-package helper family.

For a short authoring guide, see
`pretty_docs/mcel-canonical-app-authoring.md`.

## Calculator

Calculator is the real host-bound reference app.

It demonstrates an existing HTML/CSS presentation surface bound to an authored
MCEL semantic declaration, deterministic core, view-model layer, capability
bridge, and DOM shell. Its profile is
`main_computer/mcel_calculator_host_bound_profile.py`.

## Historical fixture names

Contract Counter was the old explicit-package compatibility fixture name.
Contract Workbench was the old profiled-package / authoring fixture name. Their
checked-in app trees and fixture-specific profiles are absent in this snapshot.

Do not recreate those retired fixture artifacts to satisfy stale documentation.
If a future compatibility edge needs one of those names again, add it as a
separate, bounded migration slice with its own app tree, profile, tests, and
status entry.

## Guardrail

Do not add new large `mcel_<app>_*` operational modules for projection,
evidence, parity, IR-native proof, promotion rehearsal, or promotion execution.
Add shared MCEL mechanics first, then keep app wrappers thin and app facts in a
current app declaration or current profile.


## Related guide

For the patch-by-patch workflow that keeps these roles intact, see
`pretty_docs/mcel-app-patching-lifecycle.md`. That guide distinguishes semantic
feature patches from platform cleanup patches and names which layer should own
each kind of change.
