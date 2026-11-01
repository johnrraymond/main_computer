# Windows MCEL migration smoke — bounded project-manifest correction

## Reported live result

The October 31 Windows run passed Task Manager overview/status, File Explorer roots/workspace/nested listing, and Code Editor workspace/read. Only `editor.project-manifest` failed. The existing script printed FAIL without the endpoint's reason. The exact Windows failure cannot be established from that console summary alone.

## Cause of the test-design problem

The earlier smoke posted `{"repo_dir":".","project_root":"."}` to `/api/applications/editor/project/manifest`, forcing the transaction backend to recursively enumerate and hash the **entire** repository under the default 12-second HTTP timeout. The endpoint is a project-scoped manifest service; probing a whole live repository is not necessary for Code Editor acceptance. Large runtime artifacts, filesystem errors, or timeouts can make that probe fail even if the Code Editor project manifest operation works for a selected project.

## Correction

`tools/mcel_migrations_windows_smoke.py` now:

- Checks `mcel_apps/code-editor` (the canonical source-authored app package) by default, instead of the whole repository.
- Permits an explicitly chosen repo-relative `--project-root` to inspect a different project without changing the server's path authorization.
- Uses a 30-second HTTP timeout.
- Verifies the manifest schema, reported project root, file count, SHA-256 strings, normalized file paths, nonnegative file sizes, and canonical `application.js` / `mcel.app.json` presence for the default app.
- Prints a failure reason under each failing check and retains it in `runtime/reports/mcel-migrations/windows-live-smoke.json`.

No transaction write permission, source code editing, host lifecycle mutation, or route implementation changed.

## Apply and retest on Windows

From the Main Computer repository root:

```powershell
python new_patch.py "C:\Users\subsi\Downloads\mcel_windows_manifest_smoke_fix_20261031.zip"
python -m pytest -q tests/test_mcel_windows_smoke_manifest_scope.py
python -m tools.mcel_migrations_windows_smoke
```

If the manifest check still fails, inspect the exact reason:

```powershell
$r = Get-Content .\runtime\reports\mcel-migrations\windows-live-smoke.json -Raw | ConvertFrom-Json
$r.checks.'editor.project-manifest' | ConvertTo-Json -Depth 8
```

The optional `--allow-process-probe` test spawns and terminates **only its own child process**; do not use it unless you intend to test host process mutation. Live server start/restart/shutdown, Aider writes, and browser Monaco/tabs remain `UNVERIFIED` and require separate deliberate operator acceptance.

## Local verification

22 focused tests passed, including manifest scope and contract regression checks. Integration tests that load the viewport server are blocked in this ZIP snapshot by the absent unrelated `game_projects/webgl-demo/game.json` fixture; no live Windows verification is claimed by these local tests.
