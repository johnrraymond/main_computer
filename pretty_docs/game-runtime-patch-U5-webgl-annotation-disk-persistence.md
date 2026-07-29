# Game Runtime Patch U.5: WebGL Annotation Disk Persistence

## Purpose

Fix the remaining case where **Save annotation** succeeds in the standalone
WebGL game surface but no annotation appears in `project.json`.

## Root cause

The Game Editor preview supplied `onPolygonAnnotationSave(...)` to the scene
viewer. The standalone WebGL surface did not.

Without that callback, the scene viewer used the legacy window-event fallback.
That event only had a disk writer when the Game Editor application had already
initialized its listener. In a normal standalone WebGL session the annotation
therefore remained only in the live scene/local scene store.

## Implementation

The WebGL surface now supplies its own explicit annotation persistence callback:

```text
renderWebglSceneCandidate(...)
  -> onPolygonAnnotationSave(...)
  -> queueWebglPolygonAnnotationSave(...)
  -> /api/applications/game-editor/project/annotation/write
```

The new annotation-specific route:

1. reads the latest `project.json` from disk;
2. finds the requested scene;
3. merges one annotation by `targetKey`;
4. atomically writes the project;
5. rereads the file;
6. verifies the annotation id and target key;
7. returns the exact write path and content hash.

Because the endpoint merges only one annotation into current disk state, it does
not overwrite the project with a stale full-project browser copy. A stale
content hash is reported as `stale_hash_merged: true` rather than discarding the
annotation.

## Diagnostics

A verified save response includes:

```text
project_id
scene_id
annotation_verified
write_path
write_path_resolved
content_hash
previous_content_hash
stale_hash_merged
```

The WebGL status line and annotation success hint show the repository-relative
path, normally:

```text
game_projects/webgl-demo/project.json
```

## Behavior preserved

Held-P picking, rendered-primitive fallback, modal input suppression, normal
W/A/S/D gameplay, E-key interactions, shuttle flight, docking, and mother-ship
traversal are unchanged.

## Verification targets

```text
node --check webgl-desktop.js
node --check scene-viewer.js
node --check game-editor.js
python -m py_compile main_computer/viewport_routes_game.py
tests.test_shuttle3d_polygon_annotation_tool
focused project annotation write tests
new_patch.py --dry-run exact
```
