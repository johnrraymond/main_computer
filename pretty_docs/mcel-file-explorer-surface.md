# MCEL File Explorer Surface Pilot

Patch 17 makes File Explorer the first non-editor MCEL surface pilot.

The intent is narrow:

```text
existing File Explorer UI
  + MCEL semantic ridges
  + layout ridges
  -> extractable SemanticSurfaceIR
  -> valid SharedLayoutGrammar
```

This does not rewrite File Explorer and does not add visible panels.

## Surface

```text
file-explorer.surface.primary
```

## Regions

```text
file-explorer.region.roots
file-explorer.region.toolbar
file-explorer.region.file-list
file-explorer.region.details
file-explorer.region.status
```

## Static nodes

```text
file-explorer.node.root-set
file-explorer.node.current-directory
file-explorer.node.directory-list
file-explorer.node.details-panel
```

## Static edges

```text
file-explorer.edge.roots-select-current
file-explorer.edge.current-contains-list
file-explorer.edge.list-describes-details
```

## Controls

```text
file-explorer.control.search
file-explorer.control.up
file-explorer.control.open
```

## Runtime entry ridges

`mcel-file-explorer-surface.js` also provides helper functions for dynamic entries:

```javascript
McelFileExplorerSurface.decorateRootButton(button, root, index)
McelFileExplorerSurface.decorateEntryElement(button, entry, options)
McelFileExplorerSurface.decorateTreeHost(host)
McelFileExplorerSurface.decoratePreviewPanel(panel, entry)
McelFileExplorerSurface.extractCurrentSurface(document)
```

The runtime helpers are defensive. If the MCEL module is absent, File Explorer continues to behave as before.

## Safety rules

- No domain-specific vocabulary from unrelated pilots.
- No visible UI changes.
- No mutation behavior added.
- File Explorer remains read-only.
- MCEL only adds semantic structure, layout records, and extractability.
