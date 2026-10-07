# Game Runtime Patch U.3 — Annotation Disk Autosave

## Purpose

Patch U stored annotations in the live Game Editor project and marked the
project dirty. Patch U.3 makes the annotation dialog's **Save annotation**
action persist that updated project to disk immediately.

## Runtime behavior

When an annotation is submitted:

```text
merge annotation into scenes[].metadata.shuttle3d.polygonAnnotations
mark the active project dirty
queue the annotation write behind any earlier annotation write
POST the complete project to /api/applications/game-editor/project/write
update the current content hash
clear dirty state only after a successful write
show "annotation saved to disk" in the WebGL editor status
```

The queue prevents two quick annotation saves from using the same stale
`expected_content_hash`.

## Failure behavior

If the project write route rejects the save, the project remains dirty and the
Game Editor displays the write error. The UI does not claim that the annotation
was saved to disk.

## Acceptance checks

```text
Save annotation invokes saveGameEditorProject()
the project write route receives the updated scene metadata
successful save updates contentHash and clears dirty state
rapid annotation saves are serialized
write failures are reported through reportGameEditorError
normal manual Save Project behavior remains unchanged
```

## Files

```text
main_computer/web/applications/scripts/game-editor.js
tests/test_shuttle3d_polygon_annotation_tool.py
```
