# MCEL File Explorer Requirements

## Status

This is the documentation-first requirements contract for the File Explorer app.

The current implementation already has a File Explorer route, root discovery, read-only directory listing, bounded search, metadata/file preview, mounted-Windows path support, a Wunderbaum tree presentation with fallback list rendering, a File Explorer MCEL domain pack, and a planner entry that marks File Explorer as domain-ready. It does **not** yet have a dedicated File Explorer semantic adapter registered with the MCEL domain-adapter registry.

So this document must be read as:

```text
current: domain-ready read-only File Explorer planner + domain pack
planned: full File Explorer semantic runtime for bounded read-only browsing, preview, search, file classification, and safe handoff
```

The purpose of this document is to make File Explorer requirements stable enough that MCEL Lab can later parse them, compare them with the live app, generate finding candidates, and drive code/test updates without relying on loose prose. File Explorer should be the small reference app for **navigation + list + preview** layout because its core workflows are useful, visible, and intentionally non-mutating.

## Roadmap use case: inspect a project file safely

This use case is the roadmap for the rest of this document. It forces File Explorer to behave like a safe project-navigation workbench instead of a raw filesystem browser.

A user wants to inspect a repository file before deciding where it belongs in the wider Main Computer workflow:

```text
Open File Explorer
→ choose the workspace root
→ search for "mcel"
→ select a Markdown, Python, HTML, or JavaScript file
→ preview metadata/content
→ see whether Main Computer recognizes the file as code, text, spreadsheet, game, asset, or other
→ decide whether the file should be opened in Document Editor, Code Editor, Spreadsheet, or Game Editor
```

File Explorer should make the selected root, current path, selected entry, category, suggested app, preview readability, and read-only status visible. It must not delete, rename, move, write, upload, download, stage, commit, or push anything.

```mcel-use-case
id: file-explorer.use-case.inspect-project-file-safely
app: file-explorer
status: draft
type: roadmap-use-case
primary_object: FileEntry
user_goal: >
  Browse the current workspace, search for a known file, inspect its metadata
  and preview content, and decide which Main Computer app should handle it
  without mutating the filesystem or repository.
scenario:
  root: workspace
  query: mcel
  candidate_categories:
    - code
    - text
    - spreadsheet
    - game
    - asset
    - other
requires:
  - visible root selection
  - visible current path
  - bounded search within the selected root/path
  - sorted directory listing with directory-first behavior
  - metadata preview for files and folders
  - text preview for readable small files
  - category and suggested-app evidence
  - read-only status visible near the workflow
acceptance:
  - The workspace root appears when available.
  - Search runs within the selected root and current relative path.
  - Directory results appear before file results.
  - Selecting a readable text/code file shows preview content.
  - Selecting a binary or oversized file shows a metadata-only reason.
  - The selected entry shows category and suggested app when known.
  - Path traversal using .. is blocked.
  - No delete, move, rename, write, upload, download, Git, or shell side effect occurs.
layout_implications:
  - roots/navigation rail must stay visually separate from directory contents
  - path/search toolbar must describe the current browsing scope
  - file list/tree must remain the primary work surface
  - preview/metadata panel must remain secondary and read-only
  - status must show read-only/list/search/preview outcomes
```

## Roadmap use case: browse a mounted Windows drive

File Explorer also needs to represent host-mounted paths without pretending they are unrestricted local repository paths.

A user running Main Computer with mounted-Windows path mode wants to inspect files exposed through a configured drive mount. The app should show mounted roots, use display paths that make the Windows origin clear, resolve paths through the mount resolver, and keep the same read-only laws.

```mcel-use-case
id: file-explorer.use-case.browse-mounted-windows-drive
app: file-explorer
status: draft
type: roadmap-use-case
primary_object: MountedRoot
user_goal: >
  Browse a configured mounted Windows drive through File Explorer while
  preserving root boundaries, display-path evidence, and read-only behavior.
requires:
  - mounted root candidates when path mounts are enabled
  - visible mounted_windows_drive evidence
  - display path from the mount resolver
  - list/read/search behavior scoped to the selected mounted root
  - same preview limits as workspace browsing
acceptance:
  - Mounted roots are absent when mounted path mode is disabled.
  - Mounted roots appear only when configured and available.
  - Mounted root paths display their host-style origin.
  - Relative paths are resolved by the mounted path resolver.
  - Escaping the selected mounted root is blocked.
  - Read-only browse, search, and preview behavior remains unchanged.
```

```mcel-app
id: file-explorer
title: File Explorer
status: specified
current_runtime_status: domain-ready-read-only-planner-plus-domain-pack
current_semantic_runtime_scope: none
target_runtime_status: full-read-only-semantic-runtime
dominant_object: FileEntry
primary_user_goal: >
  Browse trusted roots, inspect directory contents, search within a bounded
  scope, preview readable files, classify entries, and hand off chosen files to
  the right Main Computer app without hidden filesystem, Git, remote, or command
  side effects.
current_sources:
  - main_computer/web/applications/apps/file-explorer.html
  - main_computer/web/applications/scripts/file-explorer.js
  - main_computer/web/applications/scripts/dom-bindings/file-explorer.js
  - main_computer/viewport_routes_file_explorer.py
  - main_computer/web/applications/scripts/mcel-specimen-planner.js
  - main_computer/web/applications/scripts/mcel-supercut-packs-planner-domains.js
planned_adapter:
  - main_computer/web/applications/scripts/file-explorer-semantic-adapter.js
verification:
  - tests/test_viewport_file_explorer.py
  - tests/test_viewport_applications_static_core.py
  - tests/test_mcel_documentation.py
```

## Product law

File Explorer is not a file manager, terminal, package installer, Git staging tool, or hidden write surface. It is a read-only filesystem observation and handoff app.

Its core law is:

```text
File Explorer may list, search, classify, and preview files inside an approved root.
It must not mutate files, directories, Git state, remotes, runtime configuration, packages, or shell sessions.
Any future open/edit/commit/publish workflow must be an explicit handoff to the owning app.
```

```mcel-requirement
id: file-explorer.read-only.core-law
app: file-explorer
status: specified
type: product-law
aspect: filesystem
object: FileEntry
requirement: >
  File Explorer must remain read-only. Listing roots, listing directories,
  searching, selecting entries, and previewing readable content must not create,
  delete, rename, move, copy, write, upload, download, stage, commit, push, or
  execute anything.
current_state: >
  The backend routes return read_only true for roots, list, read, and search.
  The planner/domain pack classifies delete, move/rename, transfer, and write
  language as risky or prohibited.
acceptance:
  - Roots endpoint returns read_only true.
  - Directory list endpoint returns read_only true.
  - File read endpoint returns read_only true.
  - Search endpoint returns read_only true.
  - The UI does not expose default delete, move, rename, copy, upload, download, stage, commit, push, or run-command actions.
  - Future mutation actions are rejected unless represented as explicit cross-app handoff requirements.
```

```mcel-requirement
id: file-explorer.root-boundary.enforced
app: file-explorer
status: specified
type: safety-law
aspect: filesystem
object: FileRoot
requirement: >
  Every browse, search, and preview action must be scoped to a selected root.
  Relative paths must be normalized, traversal with .. must be blocked, and
  resolved paths must remain under the selected root.
current_state: >
  The backend resolves paths from a root id and relative path, strips normal
  slashes, rejects .., resolves candidates, and raises an error if the path
  escapes the root.
acceptance:
  - Unknown root ids are rejected.
  - Path traversal using .. is rejected.
  - Resolved paths outside the selected root are rejected.
  - Missing paths return an error instead of falling back to another root.
  - Mounted Windows paths use the mounted path resolver when enabled.
```

```mcel-requirement
id: file-explorer.preview.bounded
app: file-explorer
status: specified
type: safety-law
aspect: preview
object: FilePreview
requirement: >
  File preview must be bounded. Readable text files may be decoded for preview,
  but directories, oversized files, and binary files must produce metadata-only
  reasons rather than unbounded content loads.
current_state: >
  The backend rejects directory read attempts, marks files larger than 512 KiB
  as not readable for preview, disables binary preview when NUL bytes appear in
  the first 4096 bytes, and decodes readable content as UTF-8 with replacement.
acceptance:
  - Directories cannot be read as files.
  - Files larger than 512 KiB return readable false with a reason.
  - Binary-looking files return readable false with a reason.
  - Text files return content and encoding evidence.
  - Preview errors appear in the status/preview area, not only in the console.
```

```mcel-requirement
id: file-explorer.search.bounded
app: file-explorer
status: specified
type: product-law
aspect: search
object: SearchResultSet
requirement: >
  Search must be bounded to the selected root and current relative path. It must
  require a non-empty query, cap returned results, stop scanning after a bounded
  number of directories, and preserve read-only behavior.
current_state: >
  The backend requires a non-empty query, clamps limit from 1 to 200, walks at
  most 200 directories, and searches names rather than file contents.
acceptance:
  - Blank search queries are rejected.
  - Search starts from the selected root/current path.
  - Search path must be a directory.
  - Results are capped.
  - Search does not read file contents unless the user separately previews one selected file.
```

```mcel-requirement
id: file-explorer.classification.visible
app: file-explorer
status: specified
type: product-law
aspect: evidence
object: FileClassification
requirement: >
  File Explorer should show enough classification evidence for the user to
  understand what kind of file was selected and which Main Computer app may own
  the next workflow.
current_state: >
  The backend categorizes files as code, text, spreadsheet, game, asset, or
  other based on path/name/extension hints and provides suggested_app values for
  known categories.
acceptance:
  - Code-like extensions are classified as code with Code Editor as suggested app.
  - Markdown/text/log files are classified as text with Document Editor as suggested app.
  - Spreadsheet files are classified as spreadsheet with Spreadsheet as suggested app.
  - Game/assets/project hints are classified for Game Editor when applicable.
  - Unknown files remain other with no forced suggested app.
  - Classification appears as evidence, not as an automatic open/edit action.
```

```mcel-requirement
id: file-explorer.handoff.explicit
app: file-explorer
status: planned
type: product-law
aspect: handoff
object: FileHandoff
requirement: >
  Future open/edit/commit/publish flows must be explicit handoffs to the owning
  app. File Explorer may recommend Code Editor, Document Editor, Spreadsheet,
  Game Editor, Git Tools, or Website Builder, but it must not perform their
  writes, commits, pushes, publishes, or runtime mutations itself.
current_state: >
  The live backend returns suggested_app classification metadata. The current
  app does not provide a full governed handoff semantic adapter.
acceptance:
  - Suggested app is visible as a recommendation.
  - Opening a file in another app is a separate explicit action.
  - Editing a file belongs to Code Editor or the owning authoring app.
  - Git staging/commit/push belongs to Git Tools.
  - Website save/publish belongs to Website Builder.
  - File Explorer remains read-only after handoff.
```

```mcel-requirement
id: file-explorer.mounted-roots.honest
app: file-explorer
status: specified
type: safety-law
aspect: roots
object: MountedRoot
requirement: >
  Mounted Windows roots must be presented honestly as mounted host paths. They
  must not be conflated with repository roots, and all path resolution must go
  through the configured mount resolver.
current_state: >
  The backend reports /api/path-mounts status, exposes mounted root candidates
  when enabled, includes mounted_windows_drive metadata, and uses resolver
  display/relative-path helpers for mounted roots.
acceptance:
  - Path mounts status reports enabled/count accurately.
  - Mounted roots include mounted_windows_drive true.
  - Display paths come from the mounted resolver.
  - Mounted roots preserve root-boundary and read-only laws.
```

```mcel-requirement
id: file-explorer.status.always-visible
app: file-explorer
status: specified
type: layout-law
aspect: layout
object: FileExplorerStatus
requirement: >
  Root/list/search/read outcomes must be visible in the File Explorer status
  area. The user should always be able to tell whether the app is ready, loading,
  listed a directory, found search results, blocked preview, or rejected a path.
current_state: >
  The live HTML has file-explorer-status in the roots panel and the frontend
  updates it for list/search/read errors and result counts.
acceptance:
  - Ready state is visible when the app loads.
  - Directory list count is visible after listing.
  - Search result count is visible after search.
  - Read/preview errors are visible.
  - Root/path errors are visible.
```

```mcel-requirement
id: file-explorer.no-raw-host-leak
app: file-explorer
status: specified
type: product-law
aspect: evidence
object: PathEvidence
requirement: >
  File Explorer should show display paths that are useful to the user without
  turning raw host filesystem details into implicit permission to mutate or
  escape the selected root.
current_state: >
  Entries expose path_display and relative_path separately. Mounted roots use
  host-style display paths while operations continue to use root id plus
  relative path.
acceptance:
  - UI shows the selected root id and relative path.
  - Display paths are evidence, not raw command inputs.
  - Backend operations use root id plus normalized relative path.
  - Raw absolute paths are not accepted as browse/read/search commands from the UI.
```

## Layout regions

File Explorer's layout is the clearest small example of **navigation + list + preview**. The layout contract should describe region responsibility, not CSS placement alone.

```mcel-region
id: file-explorer.layout.identity
app: file-explorer
status: specified
role: identity
region: roots-panel-header
responsibility: >
  Identify the app as File Explorer, describe read-only system browsing, and
  expose the global status line.
contains:
  - File Explorer title
  - read-only browsing description
  - file-explorer-status
must_not_contain:
  - delete/move/write controls
  - raw shell command input
  - Git commit/push controls
```

```mcel-region
id: file-explorer.layout.roots
app: file-explorer
status: specified
role: navigation
region: roots-sidebar
responsibility: >
  Show selectable trusted roots such as workspace, debug-root, cwd, home,
  workspace-parent, filesystem-root, drive roots, or configured mounted Windows
  roots.
contains:
  - root buttons/list
  - root labels
  - root display paths
  - main-computer-purview evidence
layout_laws:
  - roots remain visually separate from directory contents
  - selected root remains visible while browsing
  - mounted roots show mounted-drive evidence
```

```mcel-region
id: file-explorer.layout.path-toolbar
app: file-explorer
status: specified
role: navigation
region: path-and-search-toolbar
responsibility: >
  Show the current root-relative browsing scope and provide bounded search/up
  navigation within that scope.
contains:
  - current root/path display
  - search current folder field
  - Search action
  - Up action
must_not_contain:
  - raw absolute path command execution
  - unrestricted recursive content search by default
  - mutation actions
layout_laws:
  - path remains near search because it defines the search scope
  - Up operates on the current relative path only
```

```mcel-region
id: file-explorer.layout.directory-list
app: file-explorer
status: specified
role: primary-work-surface
region: directory-listing
responsibility: >
  Present the current directory or search result set as the primary selectable
  collection, with directories before files and enough metadata to choose a
  preview or handoff target.
contains:
  - Wunderbaum tree/list host
  - fallback list when Wunderbaum is unavailable
  - directory entries
  - file entries
  - category badges
  - main-computer-purview badges
layout_laws:
  - this is the dominant work surface
  - the list owns its scrolling area
  - preview must not obscure the active list selection
```

```mcel-region
id: file-explorer.layout.preview
app: file-explorer
status: specified
role: inspector
region: preview-panel
responsibility: >
  Show selected entry metadata, preview content when safe, preview-denied
  reasons when unsafe, category evidence, and suggested app evidence.
contains:
  - selected entry name
  - kind/category
  - relative path/path display
  - byte size/mtime evidence
  - preview content or preview-denied reason
  - suggested app
must_not_contain:
  - inline editor that writes back to disk
  - raw command runner
  - automatic open-in-app mutation
layout_laws:
  - preview is secondary to directory listing
  - preview is read-only
  - metadata remains visible even when content preview is unavailable
```

```mcel-region
id: file-explorer.layout.status
app: file-explorer
status: specified
role: status
region: status-line
responsibility: >
  Report roots/list/search/read outcomes and errors in the visible app surface.
contains:
  - ready/listed/found messages
  - rejected path messages
  - preview errors
  - API failure messages
layout_laws:
  - status remains visible during normal browsing
  - errors are user-facing evidence, not console-only diagnostics
```

```mcel-region
id: file-explorer.layout.advanced
app: file-explorer
status: planned
role: advanced
region: mounted-path-diagnostics
responsibility: >
  Show path-mount diagnostics, resolver evidence, and raw path details only as
  advanced evidence, not as the primary browse path.
contains:
  - path mode
  - mount count
  - mounted root id
  - display path evidence
must_not_contain:
  - privileged host mutation controls
  - shell commands
  - raw absolute path write controls
```

## Semantic intents

The current app has backend route behavior and frontend UI behavior, but no dedicated semantic adapter registered with the MCEL domain-adapter registry. These intents define the target semantic surface.

```mcel-intent
id: file-explorer.intent.inspect-roots
app: file-explorer
status: specified
intent: inspectRoots
current_adapter_status: not-registered
target_adapter_status: executable
risk: read-only
trigger: app load or refresh roots
requires:
  - file explorer root candidates
produces:
  - root list
  - root display paths
  - main-computer-purview evidence
  - mounted-root evidence when available
acceptance:
  - Returns only available directory roots.
  - Deduplicates resolved roots.
  - Marks read_only true.
```

```mcel-intent
id: file-explorer.intent.select-root
app: file-explorer
status: specified
intent: selectRoot
current_adapter_status: not-registered
target_adapter_status: executable
risk: read-only
trigger: user selects a root
requires:
  - root id
produces:
  - selected root state
  - relative path reset or preserved by explicit policy
  - directory list request
acceptance:
  - Unknown roots are rejected.
  - Selection never accepts a raw absolute path as authority.
  - Directory listing refreshes after root selection.
```

```mcel-intent
id: file-explorer.intent.list-directory
app: file-explorer
status: specified
intent: listDirectory
current_adapter_status: not-registered
target_adapter_status: executable
risk: read-only
trigger: root selection, folder open, or refresh
requires:
  - selected root id
  - normalized relative path
preflight:
  - path exists
  - path is directory
  - path remains under root
produces:
  - sorted directory entries
  - current relative path
  - count evidence
  - read-only receipt
acceptance:
  - Directories sort before files.
  - Entry metadata includes kind, name, relative_path, path_display, bytes, mtime, category, and suggested_app.
```

```mcel-intent
id: file-explorer.intent.navigate-up
app: file-explorer
status: specified
intent: navigateUp
current_adapter_status: not-registered
target_adapter_status: executable
risk: read-only
trigger: user clicks Up
requires:
  - selected root id
  - current relative path
produces:
  - parent relative path
  - directory list request
acceptance:
  - Navigating up from root stays at root.
  - Parent path is normalized and root-scoped.
```

```mcel-intent
id: file-explorer.intent.search-current-folder
app: file-explorer
status: specified
intent: searchCurrentFolder
current_adapter_status: not-registered
target_adapter_status: executable
risk: read-only
trigger: user enters a query and clicks Search or presses Enter
requires:
  - selected root id
  - current relative path
  - non-empty query
preflight:
  - search path is a directory
  - query is not blank
produces:
  - search result set
  - result count evidence
  - read-only receipt
acceptance:
  - Result count is visible.
  - Search is bounded by selected root and current path.
  - Search does not mutate files or read file contents.
```

```mcel-intent
id: file-explorer.intent.preview-entry
app: file-explorer
status: specified
intent: previewEntry
current_adapter_status: not-registered
target_adapter_status: executable
risk: read-only
trigger: user selects a file or folder
requires:
  - selected root id
  - selected entry relative path
preflight:
  - path exists
  - path remains under selected root
  - preview size/type rules are checked for files
produces:
  - metadata preview
  - content preview when readable
  - preview-denied reason when not readable
  - selected-entry evidence
acceptance:
  - Directories show metadata rather than file content.
  - Oversized files show preview-denied reason.
  - Binary files show preview-denied reason.
  - Readable text files show content.
```

```mcel-intent
id: file-explorer.intent.classify-entry
app: file-explorer
status: specified
intent: classifyEntry
current_adapter_status: not-registered
target_adapter_status: executable
risk: read-only
trigger: list/search/preview entry creation
requires:
  - entry path
  - entry kind
  - extension/path hints
produces:
  - category
  - suggested_app
  - main_computer_purview evidence
acceptance:
  - Known extension categories are stable.
  - Classification does not automatically open or mutate anything.
```

```mcel-intent
id: file-explorer.intent.open-in-owning-app
app: file-explorer
status: planned
intent: openInOwningApp
current_adapter_status: not-registered
target_adapter_status: preflight-only
adapter_boundary: handoff-adapter-boundary
risk: local-state
trigger: user explicitly chooses an owning app
requires:
  - selected entry
  - suggested app or user-selected app
  - target app handoff contract
preflight:
  - target app is available
  - selected entry is inside an approved root
  - target app declares its own write/mutation boundary
produces:
  - handoff receipt
  - no filesystem mutation by File Explorer
acceptance:
  - Opening in Code Editor does not save automatically.
  - Opening in Document Editor does not create a document revision automatically.
  - Opening in Git Tools does not stage/commit/push automatically.
```

```mcel-intent
id: file-explorer.intent.delete-file
app: file-explorer
status: prohibited
intent: deleteFile
current_adapter_status: prohibited
target_adapter_status: prohibited
risk: prohibited
trigger: none in default File Explorer
requires:
  - separate future mutation app or explicit governed delete design
acceptance:
  - Default File Explorer does not expose delete.
  - MCEL truth gate does not count delete as an executable File Explorer intent.
produces:
  - no file deletion is performed by default
  - prohibited-action evidence
```

```mcel-intent
id: file-explorer.intent.move-or-rename
app: file-explorer
status: prohibited
intent: moveOrRename
current_adapter_status: prohibited
target_adapter_status: prohibited
risk: local-file-mutation
trigger: none in default File Explorer
requires:
  - separate future governed file-management design
acceptance:
  - Default File Explorer does not expose move or rename.
  - Search/list/preview never imply move or rename authority.
produces:
  - no file move or rename is performed by default
  - prohibited-action evidence
```

```mcel-intent
id: file-explorer.intent.run-file-command
app: file-explorer
status: prohibited
intent: runFileCommand
current_adapter_status: prohibited
target_adapter_status: prohibited
risk: execution
trigger: none
requires:
  - no default support
acceptance:
  - File Explorer never runs selected files as commands.
  - Shell execution belongs to Terminal with its own safety boundary.
produces:
  - no command is executed
  - prohibited-action evidence
```

## Acceptance

```mcel-acceptance
id: file-explorer.acceptance.read-only-browse-preview
app: file-explorer
status: specified
scope: full-read-only-semantic-runtime
requires:
  - roots endpoint returns trusted available roots with read_only true
  - list endpoint is root-scoped and read_only
  - read endpoint previews only bounded readable files
  - search endpoint is bounded, root-scoped, and read_only
  - UI shows roots, current path, file list, preview, and status
  - category/suggested-app evidence is visible
  - path traversal is blocked
  - no filesystem mutation actions are exposed by default
  - no Git, remote, package, shell, upload, or download side effects occur
  - MCEL truth gate reports File Explorer fullApplicationSemanticReady only after a dedicated adapter proves these intents
```

```mcel-acceptance
id: file-explorer.acceptance.layout-navigation-list-preview
app: file-explorer
status: specified
scope: layout
requires:
  - roots/navigation rail remains distinct from directory list
  - path/search toolbar defines current browsing scope
  - directory list is the primary work surface
  - preview panel is read-only and secondary
  - status remains visible
  - advanced path/mount diagnostics do not dominate the default workflow
```

```mcel-acceptance
id: file-explorer.acceptance.mounted-paths
app: file-explorer
status: specified
scope: mounted-windows-paths
requires:
  - path mounts report disabled/local state by default
  - mounted roots appear only when configured and enabled
  - mounted roots use resolver display paths and relative paths
  - mounted paths preserve root-boundary checks
  - mounted browsing remains read-only
```

## Current gaps

```mcel-finding
id: file-explorer.finding.semantic-adapter-missing
app: file-explorer
status: open
aspect: semantic-runtime
severity: medium
problem: >
  File Explorer has a domain pack and planner entry, but no dedicated
  File Explorer semantic adapter registered with the MCEL domain-adapter
  registry.
desired_behavior: >
  A dedicated adapter should expose roots, selected root, current path, selected
  entry, list/search/preview receipts, classification evidence, prohibited
  mutation intents, and recovery guidance.
evidence:
  - mcel-specimen-planner.js lists File Explorer as domain-ready with planner-generic-adapter
  - mcel-supercut-packs-planner-domains.js defines file-explorer-domain
  - no file-explorer-semantic-adapter.js is registered
required_checks:
  - adapter registry reports runtimeCoreReady for read-only File Explorer only after executable read-only intents exist
  - prohibited mutation intents are not counted as executable
```

```mcel-finding
id: file-explorer.finding.handoff-contract-missing
app: file-explorer
status: open
aspect: handoff
severity: low
problem: >
  File Explorer can classify entries and suggest apps, but the docs do not yet
  define a full governed handoff contract from selected file to Code Editor,
  Document Editor, Spreadsheet, Game Editor, Git Tools, or Website Builder.
desired_behavior: >
  Handoff should become an explicit preflight/receipt workflow where File
  Explorer passes a selected file reference and the target app owns all editing,
  saving, committing, publishing, or execution semantics.
required_checks:
  - suggested app remains visible as recommendation
  - handoff does not mutate the file
  - target app receives root-scoped file identity rather than arbitrary host path
```

```mcel-finding
id: file-explorer.finding.layout-as-reference-pattern
app: file-explorer
status: open
aspect: layout
severity: low
problem: >
  File Explorer is the simplest app for extracting the navigation + list +
  preview MCEL layout grammar, but that grammar is not yet formalized as a
  reusable layout pattern.
desired_behavior: >
  MCEL requirements language should capture this as a reusable pattern with
  navigation roots, path/search toolbar, primary collection, read-only preview,
  status, and advanced diagnostics.
required_checks:
  - mcel-requirements-language.md defines reusable region responsibility fields
  - MCEL Lab can infer roots/list/preview/status from the rendered app
```


## Runtime diagnosis contract

```mcel-runtime-check
id: file-explorer.runtime-check.default-primary-surface
app: file-explorer
status: specified
mode: default
contract: file-explorer.contract.default.app-health
check: primary-surface
severity: critical
primary_surface_id: file-explorer.surface.main
host_selector: ".file-explorer-main"
editor_selector: ".file-explorer-main"
min_width: 420
min_height: 320
observes:
  - ".file-explorer-main"
expects:
  - File Explorer main browsing surface is visible and usable.
  - The list/preview work area is not collapsed.
failure_message: File Explorer default mode must expose a usable browsing surface.
next_probe: layout.ownerProbe
source_binding: file-explorer.binding.viewport-file-explorer
test_binding: file-explorer.test.viewport-file-explorer
```

```mcel-runtime-check
id: file-explorer.runtime-check.default-required-regions
app: file-explorer
status: specified
mode: default
contract: file-explorer.contract.default.app-health
check: required-regions-visible
severity: critical
observes:
  - "#file-explorer-app"
  - ".file-explorer-roots-panel"
  - ".file-explorer-main"
  - ".file-explorer-toolbar"
  - "#file-explorer-list"
required_regions:
  - file-explorer.region.root | #file-explorer-app | File Explorer app root
  - file-explorer.region.roots | .file-explorer-roots-panel | Roots panel
  - file-explorer.region.main | .file-explorer-main | Main browsing surface
  - file-explorer.region.toolbar | .file-explorer-toolbar | Path/search toolbar
  - file-explorer.region.list | #file-explorer-list | File list
expects:
  - Root, roots panel, toolbar, main surface, and file list are visible.
failure_message: File Explorer default mode must preserve roots, toolbar, and list.
next_probe: layout.baseline
source_binding: file-explorer.binding.viewport-file-explorer
test_binding: file-explorer.test.viewport-file-explorer
```

```mcel-runtime-check
id: file-explorer.runtime-check.default-overlay-policy
app: file-explorer
status: specified
mode: default
contract: file-explorer.contract.default.app-health
check: overlay-policy
severity: warning
observes:
  - "#mc-widget-editor-root"
  - "[data-mcel-proof-surface]"
  - ".floating-tab"
  - ".side-tab"
expects:
  - MCEL/widget/proof overlays are not visible while browsing files.
forbids:
  - shared.overlay.widget-editor | #mc-widget-editor-root | Widget editor overlay
  - shared.overlay.proof-surface | [data-mcel-proof-surface] | MCEL proof surface
  - shared.overlay.floating-tab | .floating-tab, .side-tab | Floating diagnostic tab
failure_message: File Explorer should not be covered by diagnostic overlays in default mode.
next_probe: overlay.detector
source_binding: file-explorer.binding.viewport-file-explorer
test_binding: file-explorer.test.viewport-file-explorer
```
