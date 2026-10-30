# MCEL 14.7.5d — Main Computer application-shell runtime wiring repair

## Incident

The flat L18 gallery mounted eighteen *empty* specimens, each reporting
`MCEL_14_7_LIFECYCLE_RUNTIME_UNAVAILABLE`. This was **not** evidence that eighteen
Taguchi configurations independently failed. The application page simply did
not load the universal semantic composition runtime. Two other MCEL services
required by the laboratory had also been omitted from that page's include list.

Previous Chromium Frobs used `html_fixture()` and then explicitly inserted
`mcel-layout-continuity.js` and `mcel-universal-semantic-layout-lab.js` into
that standalone test environment. Consequently they could pass while the
actual Applications page lacked those modules. This was a deployment
integration omission and a test-oracle blind spot.

## Fix

`main_computer/web/applications.html` now includes:

- `mcel-layout-continuity.js` before semantic layout inference.
- `mcel-observation-integrity.js` before semantic collection workspace.
- `mcel-universal-semantic-layout-lab.js` after element registry/core and before
  lifecycle acid fixtures / gallery.

No production Task Manager actions or network requests are introduced, and no
existing element behavior or Taguchi factor is changed.

## Acceptance

```
python -m tools.mcel_lifecycle_shell_runtime_frob
python -m pytest -q tests/test_mcel_lifecycle_shell_runtime.py
```

The new Frob derives its module list and dependency order from the **real
`applications.html` includes**, rather than supplying missing modules on its
own. It mounts all 18 real universal semantic specimens at 390px and 1440px,
asserts nonempty rows, matching authoritative source membership, and valid
canonical layout. It records failures, page errors, and module presence in
`runtime/reports/mcel-lifecycle-shell-runtime/report.json`.

The test uses the existing offline Task Manager browser fixture as its page
body, because the checked-in full Applications page cannot currently be
expanded in this archive (an unrelated `game_projects/webgl-demo/game.json`
manifest is missing). It therefore validates the actual application-shell
module wiring and renderer together, but **does not claim an HTTP production
browser end-to-end test**. Confirm the installed page after a hard refresh.

This patch is additive to the previously installed 14.7.5c flat-gallery patch.
