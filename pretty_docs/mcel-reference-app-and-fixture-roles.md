# MCEL reference app and fixture roles

MCEL now keeps reusable mechanics in shared modules and keeps app-specific facts
in profiles. The app-specific wrappers are compatibility entry points only; they
should dispatch into the shared machinery instead of owning projection,
evidence, proof, promotion, or browser-observation mechanics.

## Canonical authoring example

Calculator is the only canonical real MCEL app in this repository right now.
Use `mcel_apps/calculator/application.js` to understand the modern MCEL app
authoring surface.

Counter and Workbench are intentionally retained fixtures. They are valid MCEL
assets, but they are bad examples for new real app authoring because they exist
to exercise package compatibility, projection, evidence, proof, and promotion
paths.

For a short authoring guide, see
`pretty_docs/mcel-canonical-app-authoring.md`.

## Calculator

Calculator is the real host-bound reference app.

It demonstrates an existing HTML/CSS presentation surface bound to an authored
MCEL semantic declaration, deterministic core, view-model layer, capability
bridge, and DOM shell. Its profile is
`main_computer/mcel_calculator_host_bound_profile.py`.

## Counter

Counter is the small explicit-package reference fixture.

Do not use Counter as the canonical app-authoring example. It demonstrates legacy explicit-package import, generated contract projection,
candidate evidence, compatibility comparison, IR-native proof, promotion
rehearsal, and promotion execution through the explicit-package generic MCEL
path. Its fixture facts live in
`main_computer/mcel_counter_reference_fixture_profile.py`, with Counter-specific
legacy fixture metadata in `main_computer/mcel_counter_legacy_fixture.py`.

## Workbench

Workbench is the profiled-package / authoring reference fixture.

Do not use Workbench as the canonical app-authoring example. It demonstrates deterministic projection profiles, constrained expression
coverage, richer intent/scenario proof, evidence aggregation, promotion
rehearsal, and idempotent promotion execution through the profiled-package
generic MCEL path. Its fixture facts live in
`main_computer/mcel_workbench_reference_fixture_profile.py`.

## Guardrail

Do not add new large `mcel_<app>_*` operational modules for projection,
evidence, parity, IR-native proof, promotion rehearsal, or promotion execution.
Add shared MCEL mechanics first, then keep app wrappers thin and app facts in a
profile.


## Related guide

For the patch-by-patch workflow that keeps these roles intact, see
`pretty_docs/mcel-app-patching-lifecycle.md`. That guide distinguishes semantic
feature patches from platform cleanup patches and names which layer should own
each kind of change.
