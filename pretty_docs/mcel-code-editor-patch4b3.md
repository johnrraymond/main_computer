# MCEL Code Editor 4B.3 — Semantic Workspace Reconstruction

Source snapshot: main_computer_test-20261119-152427(1).zip. Requires 4B.1+4B.2 canonical unification (already present in this snapshot).

## Installation
Extract this archive into your repository root (e.g., `C:\Users\subsi\main_computer`). Restart the web/server process or reload the application to pick up changed Python, JS and CSS assets. No finalizer or deletion is required.

## What changed
- Authored, source-bound placement: Explorer at start; Aider/docs at end; reviewed changes/SCM at bottom; status at bottom; central Monaco document editor remains primary.
- Verified workspace-plan contract uses canonical HTML preferences; no second application behavior authority.
- Shared, opt-in document-workbench presenter creates activity rail, tabbed secondary sidebar, tabbed bottom dock, collapsible panels, keyboard and pointer splitters, responsive narrow layouts, persistent layout preferences and selected tool tabs.
- All 39 intents, 13 capabilities, 36 effects and security/authorization flows in application.js are unchanged. Original operation controls remain mounted.
- Other MCEL applications continue using their previous layout path; CSS is scoped to the document-workbench attribute.

## Tests
Run:

    python -m pytest -q tests/test_mcel_code_editor_4b3_semantic_workspace.py
    python -m pytest -q tests/test_mcel_code_editor_4b12_unification.py tests/test_mcel_semantic_other_apps.py
    python -m pytest -q tests/test_mcel_task_manager_navigation_layout.py tests/test_mcel_task_manager_operational_workspace.py

## Known limitations
- This is VS Code-class semantic organization, not pixel parity or feature parity. Bottom panel currently contains Review and SCM; it does not implement a shell terminal.
- Live Windows browser/session, Monaco CDN load, and actual backend writes were not verified in this Linux sandbox. The tested browser blocked network access. Persistence was verified across a full client-side remount with a localStorage substitute.
- Older tests asserting the retired pre-4B.1 host-bound Code Editor layout can fail by design; the SLH activity rail oracle also has a preexisting obsolete expectation. A full monolithic browser regression run stalled and is not counted as a pass.

## Rollback
Restore the five overwritten files from the source snapshot, and remove the two newly added files. No source data or runtime state is modified by installing this archive.
