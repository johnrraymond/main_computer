# MCEL app patching lifecycle

MCEL app patches should change app meaning through the same layered path every
time. The app declaration owns semantic authority; runtime files implement and
bind that authority; shared MCEL tooling proves the result.

## Canonical authoring source

Use `mcel_apps/calculator/application.js` as the canonical real-app authoring
example. It is the repo's modern host-bound MCEL app.

Do not use `mcel_apps/contract-counter/` or `mcel_apps/contract-workbench/` as
new-app authoring models. They are reference fixtures for explicit-package and
profiled-package compatibility/projection/proof paths.

## Patch classes

### Semantic feature patch

Use this path when the app gains or changes behavior.

1. Update the app declaration.
   - Add or update states, intents, capabilities, scenarios, and invariants in
     `mcel_apps/<app-id>/application.js`.
   - Treat semantic fingerprint changes as intentional evidence, not noise.
2. Implement deterministic behavior in app code.
   - Put parsing, validation, state transitions, and deterministic compute in
     the app core.
   - Keep DOM access and network/provider calls out of the core.
3. Shape app-facing result contracts.
   - Use the app view-model layer when the feature needs status text, receipts,
     render models, or visible result shaping.
4. Add or adjust capability lanes only when an external system is involved.
   - Capability modules own transport, not deterministic app semantics.
5. Bind through the host or package shell.
   - Host-bound apps should read/write the existing DOM and call the declared
     runtime facade.
   - Explicit/profiled package fixtures should update generated-package inputs
     through the appropriate fixture/profile path.
6. Renew proof.
   - Update deterministic tests, scenarios, browser/projection evidence, and
     promotion/rehearsal checks that are invalidated by the semantic change.

### Platform cleanup patch

Use this path when moving reusable MCEL mechanics out of an app-specific wrapper.

1. Identify whether the code is app fact or platform machinery.
   - App facts belong in the reference profile.
   - Projection, evidence, proof, promotion, browser observation, parity, and
     workspace mechanics belong in shared MCEL modules.
2. Keep wrapper entry points stable.
   - `mcel_<app>_*` modules may remain as compatibility wrappers.
   - Wrappers should delegate to shared machinery and pass a profile/hook set.
3. Preserve app behavior.
   - Semantic fingerprints should stay unchanged unless the app declaration
     intentionally changes.
   - Candidate reports may keep compatibility schema labels while the mechanics
     become generic.
4. Add or update guardrails.
   - Wrapper thinness and forbidden workspace-mechanics imports should be tested.
   - Profiles should state their app or fixture role.

## Current reference cases

### Calculator

Calculator is the real host-bound reference app. A Calculator feature patch
should normally touch:

```text
mcel_apps/calculator/application.js
main_computer/web/applications/scripts/calculator-core.js
main_computer/web/applications/scripts/calculator-view-model.js
main_computer/web/applications/scripts/calculator-capabilities.js
main_computer/web/applications/scripts/calculator.js
main_computer/web/applications/apps/calculator.html
main_computer/web/applications/styles/calculator.css
```

Only the layers required by the feature should change. For example, Unit Arithmetic v1 changed the declaration, deterministic core, display path, host
input handling, and tests, but did not require graph units, Mathics transport,
or model-output guarantees.

### Counter

Counter is the small explicit-package reference fixture. Counter patches should
preserve its role as fixture input for explicit-package import, generated
contract projection, evidence, compatibility, proof, promotion rehearsal, and
promotion execution. Its fixture facts and legacy fixture metadata live in:

```text
main_computer/mcel_counter_reference_fixture_profile.py
main_computer/mcel_counter_legacy_fixture.py
main_computer/mcel_counter_generated_contracts.py
main_computer/mcel_counter_effect_probe.py
```

### Workbench

Workbench is the profiled-package / authoring reference fixture. Workbench
patches should preserve its role as the richer fixture for projection profiles,
constrained expressions, acceptance/evidence aggregation, IR-native proof, and
promotion proof. Its fixture facts are split intentionally:

```text
main_computer/mcel_workbench_reference_fixture_profile.py
main_computer/mcel_workbench_expression_profile.py
main_computer/mcel_projection_profiles/contract_workbench_v1.py
```

## What should not regress

- App wrappers should not re-grow projection/evidence/proof/promotion mechanics.
- App cores should not call DOM, `fetch`, provider APIs, or filesystem APIs.
- Capability modules should not implement deterministic app semantics.
- Host shells should not re-own domain parsers, evaluators, or semantic
  invariants.
- Generated artifacts should stay virtual unless a fixture explicitly exists to
  test generated package materialization.
- Retired legacy app artifacts should stay absent after promotion.
