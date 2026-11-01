# MCEL application migration — 2026-10-31

## Source and scope

Based on `main_computer_test-20261031-172317.zip`. This is a **functional parity patch** for the existing source-bounded UI bridges; it does not represent proof of a completed MCEL package migration. The source snapshot lacks both `mcel_experiments/file-explorer-candidate/application.js` and `mcel_experiments/task-manager-notebook/application.js`. Their corresponding generated candidate IR cannot replace authenticated canonical authoring sources. Do not assert that missing-source candidate DSL compilation passed.

## Code Editor

- `code-editor-core.js`: immutable per-file document store, active tab switching, independent unsaved drafts, explicit dirty-close refusal, repo-switch refusal while files are open, and invalidation of pending reviewed patches on edits, saves, opens, and closes.
- `code-editor.js` / `code-editor.css`: visible, accessible document tabs in the legacy fidelity and runtime fallback surfaces; scoped close confirmation; Monaco remains in its primary editor host.
- Original Aider command prepare/run, context/thread history, and archive buttons remain on their existing backend and DOM ownership. Two **separate** buttons drive hash-constrained source-transaction prepare/apply, with reviewed and approved gates. An Aider natural-language command is **not** the same operation as a reviewed source transaction. This patch does not claim complete Aider feature parity for every fallback mode.

## File Explorer

- `mcel-file-explorer-candidate-bridge.js`: nested expandable hierarchy (`role=tree`) with independent cached directory reads. Expansion and file preview use the existing `FileExplorerSemanticAdapter.executeIntent` scope; no new write or arbitrary-path authority was added. Source enumeration is bounded by existing adapter/backend policy.
- This is an opt-in candidate surface, **not** a promoted canonical File Manager package.

## Task Manager

- `mcel-task-manager-notebook-bridge.js`: application-mode operations panel with selected observed PID, terminate/force-kill confirmation, server status/start/restart/shutdown confirmation, scheduling and schedule deletion, and host receipts.
- `terminal.js`: existing `runTaskAction` now returns success/failure receipts to the panel. Host remains the action authority; generated read-only notebook IR cannot grant machine mutations.
- Automatic refresh is the pre-existing 5-second `scheduleTaskManagerAutoRefresh()` interval. Application-mode `auto-refresh` field is bridged to the original checkbox and its change listener, not a competing timer.
- Both generated applications require their existing explicit candidate/application opt-in and runtime checks; no production rollout was performed.

## Verification and remaining gates

Run non-Windows checks from repository root:

```powershell
python -m pytest -q tests/test_mcel_migration_parity_20261031.py tests/test_mcel_code_editor_runtime_authority.py mcel_apps/code-editor/tests/test_operations.py
```

On the **actual Windows host** running Main Computer, execute:

```powershell
python -m tools.mcel_migrations_windows_smoke --base-url http://127.0.0.1:8765
```

Optional isolated termination test: `python -m tools.mcel_migrations_windows_smoke --allow-process-probe`. This spawns and terminates only an ephemeral child process. The script deliberately refuses non-Windows environments and never restarts/shuts down the live server. The generated JSON report is `runtime/reports/mcel-migrations/windows-live-smoke.json`.

The Windows operator must still verify real browser Monaco/tab behavior, Aider natural-language run and history, File Explorer tree expansion against Windows roots, Task Manager automatic refresh over several intervals, and explicitly authorized lifecycle actions. The smoke script explicitly labels those untested controls `UNVERIFIED` rather than passing them. The source snapshot also lacks `game_projects/webgl-demo/game.json`, so importing the live viewport from this snapshot fails an unrelated repository fixture prerequisite.

## Installation

The manifest-free patch ZIP contains only repository-relative changed/new paths. From the Main Computer repo root, use `python new_patch.py "C:\path\to\mcel_app_migration_parity_patch_20261031.zip"`, review its report, and run the tests. It does not delete legacy files or alter installed running processes by itself.
