# Game Runtime Patch U.4: Explicit Annotation Save Callback

## Purpose

Fix the case where **Save annotation** updates the live scene but the annotation
does not appear in the repository snapshot.

Patch U.3 relied on a window event emitted by the scene viewer. That handoff was
not reliable enough to prove that the active Game Editor project received and
persisted the annotation.

## Implementation

The Game Editor now passes:

```text
onPolygonAnnotationSave(detail)
```

to `MainComputerSceneViewer.renderSceneSurface(...)`.

The annotation modal:

1. updates the live scene annotation store;
2. awaits the explicit editor callback;
3. keeps the modal open while the write is pending;
4. closes only after the callback confirms disk persistence;
5. displays the write error and remains editable when persistence fails.

The editor callback merges the annotation into the authoritative project object,
serializes saves through the existing annotation queue, and writes the project
with:

```text
verify_annotation:
  scene_id
  target_key
  annotation_id
```

The project-write route atomically writes `project.json`, rereads it from disk,
locates the requested annotation, and returns:

```text
annotation_verified: true
annotation: <saved annotation>
annotation_verification: <receipt>
```

The UI does not claim a disk save without that receipt.

## Compatibility

The existing
`main-computer-shuttle3d-polygon-annotation-save` event remains available as a
fallback for non-editor surfaces. The Game Editor path uses the explicit callback
and therefore does not depend on the fallback event.

## Behavior preserved

Normal gameplay controls, held-P picking, modal input suppression, room geometry,
interactions, and the current shuttle-to-bridge route are unchanged.

## Verification targets

```text
node --check scene-viewer.js
node --check game-editor.js
python -m py_compile main_computer/viewport_routes_game.py
tests.test_shuttle3d_polygon_annotation_tool
tests.test_viewport_game_editor...annotation verification
new_patch.py --dry-run exact
```
