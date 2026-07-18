# MCEL Website Builder and Websites Requirements

## Status

This is the documentation-first requirements contract for Website Builder and the saved websites under `runtime/websites/<site-id>`.

The current implementation already has a Website Builder app, selectable saved sites, a GrapesJS design surface, draft/local/dev/remote preview lanes, local server publishing, remote Coolify publishing controls, blog/runtime setup, Directus/SQLite integration, site manifests, generated site server runtime files, and tests around website projects, publish targets, generated compose files, blog runtime setup, and golden website path smokes. It does **not** yet have a dedicated Website Builder semantic adapter registered with the MCEL domain-adapter registry.

So this document must be read as:

```text
current: working Website Builder + saved website project manifests + local/dev/remote publish lanes
planned: full Website Builder semantic runtime for site editing, runtime configuration, publishing, website evidence, and Git Tools handoff
```

The purpose of this document is to make website requirements stable enough that MCEL Lab can later parse them, compare them with the live Website Builder app and saved website folders, generate finding candidates, and drive code/test updates without relying on loose prose.

## Roadmap use cases

These use cases are the roadmap for the rest of this document. They make the website system visible as a product workflow instead of a pile of HTML files, deployment scripts, and Git details.

### Use case 1: edit and preview a saved website

A user opens Website Builder, selects a saved site such as `hub-site` or `johnrraymond`, edits page content or style, previews the draft, saves the site source, and verifies the local preview without publishing to a remote server.

```mcel-use-case
id: website-builder.use-case.edit-preview-saved-site
app: website-builder
status: partially-implemented
type: roadmap-use-case
primary_object: WebsiteProject
user_goal: >
  Select a saved website, edit its visible content or styling, preview the draft,
  save the site source, and verify that the saved site still has a coherent
  manifest, builder state, entry HTML, stylesheet, script, and page runtime.
current_support:
  - selectable sites under runtime/websites/<site-id>
  - site.json as site manifest
  - builder.json as builder/editor state
  - index.html, style.css, script.js, and runtime.js as site files
  - draft, local, dev, and remote preview controls
  - GrapesJS design surface and source preview lanes
acceptance:
  - Selecting a site makes its manifest, builder state, and preview status visible.
  - Saving updates only the selected site's intended source artifacts.
  - Previewing a draft does not publish to local, dev, or remote lanes.
  - A failed preview or parse error leaves dirty state visible.
  - Runtime-only evidence does not silently rewrite author-owned source.
layout_implications:
  - site selector and identity must stay visible
  - design surface and preview must be visually primary
  - save/dirty state must be near the editing surface
  - source preview and generated runtime evidence must be secondary
```

### Use case 2: configure a blog-capable website runtime

A user enables or inspects the blog/runtime layer for a website, understands which layers are local files, SQLite data, Directus/CMS state, API routes, and generated blog pages, then accepts only the required setup steps.

```mcel-use-case
id: website-builder.use-case.configure-blog-runtime
app: website-builder
status: partially-implemented
type: roadmap-use-case
primary_object: WebsiteRuntimeContract
user_goal: >
  Configure or inspect the blog-capable site runtime without confusing source
  pages, local database artifacts, Directus storage, generated API routes, or
  published website files.
current_support:
  - Blog Runtime wizard
  - runtime layer status
  - Directus connection modal
  - .main-computer/runtime/app.py site server runtime
  - .main-computer/runtime/runtime.json runtime receipt
  - blog/index.html generated blog page
  - data/content.sqlite local content database
  - dist/data/publish-manifest.json content publish manifest
acceptance:
  - The wizard shows runtime layers in dependency order.
  - Directus/SQLite choices are explicit and acknowledged before mutation.
  - Generated blog pages are labeled as generated website artifacts.
  - Existing deployed databases are protected unless the user chooses a destructive path.
  - API route evidence is visible before publishing a runtime-backed website.
layout_implications:
  - runtime dependencies must not be hidden behind generic publish buttons
  - CMS/database state must be visually distinct from page source
  - destructive storage choices require a danger boundary
```

### Use case 3: publish a website to a selected lane

A user has saved a site and wants to publish it to a lane: local server, dev, or remote production through Coolify. Website Builder should show the target, command preview, publish preflight, required acknowledgement, visit URL, and receipt.

```mcel-use-case
id: website-builder.use-case.publish-selected-lane
app: website-builder
status: partially-implemented
type: roadmap-use-case
primary_object: WebsitePublishPlan
user_goal: >
  Publish a saved website to one explicit lane, verify the target URL, and keep
  local authoring, local server, dev deployment, and remote production separate.
current_support:
  - Local Server publish controls
  - dev deployment controls
  - remote production/Coolify target controls
  - publish target forms
  - command preview
  - deploy preflight modal
  - visit buttons for local, dev, and remote URLs
  - publish target manifest fields
acceptance:
  - Save does not deploy.
  - Preview does not deploy.
  - Local publish does not imply remote publish.
  - Remote publish requires explicit target evidence and acknowledgement.
  - The selected lane is visible before and after publish.
  - A successful publish records a receipt or manifest update for that lane.
  - A failed publish leaves recovery guidance and does not mark the lane verified.
layout_implications:
  - publish actions must be grouped by lane
  - remote production controls require stronger evidence than local preview
  - visit buttons must show which lane they open
```

### Use case 4: hand website changes to Git Tools

A website edit changes repository files. The user should be able to hand those website-scoped changes to Git Tools, select exactly which site files belong in a commit, commit them locally, and push through the existing governed local-Gitea workflow.

```mcel-use-case
id: website-builder.use-case.git-tools-handoff
app: website-builder
status: planned
type: roadmap-use-case
primary_object: WebsiteChangeSet
user_goal: >
  Turn saved website changes into reviewable repository evidence, then use Git
  Tools for file selection, commit, and governed push rather than hiding Git
  mutation inside Website Builder.
current_support:
  - Website Builder has Git history/review controls.
  - Git Tools already has a governed local-Gitea push workflow.
  - website project files live under repository-relative runtime/websites/<site-id>.
planned_support:
  - website-scoped changed-file basket
  - explicit handoff from Website Builder to Git Tools
  - Git Tools commit workflow for selected website files
  - Git Tools governed push receipt linked back to the website project
acceptance:
  - Website Builder does not silently commit or push when saving a site.
  - Website-scoped changes can be filtered to runtime/websites/<site-id>.
  - Selected website files can be handed to Git Tools for add/commit.
  - Git Tools owns governed push evidence.
  - Website Builder may display Git receipts but does not pretend they are page preview state.
layout_implications:
  - Git evidence is secondary to editing and publishing
  - repository mutation belongs in an advanced or delegated workflow region
  - commit/push receipts must be distinct from publish receipts
```

```mcel-app
id: website-builder
title: Website Builder and Websites
status: specified
current_runtime_status: working-app-plus-site-project-model
current_semantic_runtime_scope: none
target_runtime_status: full-application-semantic-runtime
dominant_object: WebsiteProject
primary_user_goal: >
  Edit saved websites, configure optional site runtime layers, preview and
  publish to explicit lanes, and hand repository changes to Git Tools without
  confusing author-owned source, generated runtime evidence, deployment targets,
  or remote sync.
current_sources:
  - main_computer/web/applications/apps/website-builder.html
  - main_computer/web/applications/scripts/website-builder.js
  - main_computer/web/applications/scripts/dom-bindings/websites.js
  - main_computer/web/applications/styles/website-builder.css
  - runtime/websites/hub-site/site.json
  - runtime/websites/hub-site/builder.json
  - runtime/websites/hub-site/index.html
  - runtime/websites/johnrraymond/site.json
  - runtime/websites/johnrraymond/builder.json
  - runtime/websites/johnrraymond/index.html
planned_adapter:
  - main_computer/web/applications/scripts/website-builder-semantic-adapter.js
verification:
  - tests/test_website_builder_app.py
  - tests/test_website_project_manifest.py
  - tests/test_website_publish_targets.py
  - tests/test_generate_websites_compose.py
  - tests/test_website_docker_lifecycle.py
  - tests/test_golden_website_path_smoke.py
  - tests/test_mcel_documentation.py
```

## How saved websites work

A saved website is a repository-scoped project folder, not an opaque CMS record and not a hidden remote deployment.

The working folder shape is:

```text
runtime/websites/<site-id>/
  site.json
  builder.json
  index.html
  style.css
  script.js
  runtime.js
  blog/index.html
  data/content.sqlite
  .main-computer/runtime/app.py
  .main-computer/runtime/runtime.json
  .main-computer/runtime/page-runtime.json
  dist/data/publish-manifest.json
```

The layers have different ownership:

```text
site.json                         website manifest and publish/runtime metadata
builder.json                      builder/editor state
index.html/style.css/script.js    author-owned page source artifacts
runtime.js                        page runtime selected by builder state
blog/index.html                   generated blog page artifact
data/content.sqlite               local content database artifact
.main-computer/runtime/app.py     packaged site server runtime
runtime.json/page-runtime.json    runtime evidence receipts
dist/data/publish-manifest.json   publishable content evidence
Git Tools                         repository commit/push evidence
```

The product law is:

```text
Website Builder owns site editing, runtime setup, preview, and publish planning.
Git Tools owns repository add/commit/push evidence.
Saving, previewing, publishing, committing, and pushing are separate user actions.
```

## Product law

Website Builder is not a raw deployment terminal and not a hidden Git client. It is a governed website workbench.

```mcel-requirement
id: website-builder.source.project-folder-canonical
app: website-builder
status: specified
type: product-law
aspect: source
object: WebsiteProject
requirement: >
  A saved website is represented by a repository-relative project folder under
  runtime/websites/<site-id>, with site.json and builder.json as explicit
  metadata files and page assets as visible source artifacts.
current_state: >
  The snapshot includes hub-site and johnrraymond folders with site.json,
  builder.json, index.html, style.css, script.js, runtime files, blog pages, and
  content publish manifests.
acceptance:
  - Site identity is derived from the selected project folder and manifest.
  - Website Builder never treats a remote deployment as the canonical source.
  - Author-owned page files remain visible as repository files.
```

```mcel-requirement
id: website-builder.layers.separated
app: website-builder
status: specified
type: product-law
aspect: architecture
object: WebsiteProject
requirement: >
  Site source, builder state, page runtime, site server runtime, CMS/database
  content, publish manifests, and Git evidence must remain separate layers.
acceptance:
  - Runtime receipts are not mistaken for author-owned page source.
  - CMS/database choices are not hidden inside page editing.
  - Publish receipts are not mistaken for Git commit receipts.
  - Git receipts are not mistaken for public deployment verification.
```

```mcel-requirement
id: website-builder.save.no-publish
app: website-builder
status: specified
type: product-law
aspect: publishing
object: WebsiteProject
requirement: >
  Saving a website updates selected site source/builder artifacts only. It must
  not publish to local, dev, or remote lanes.
acceptance:
  - Save marks the selected site saved or dirty-cleared.
  - Save does not run Docker, Coolify, SSH, Git push, or remote deployment.
  - Save failures leave dirty state visible.
```

```mcel-requirement
id: website-builder.preview.no-mutation
app: website-builder
status: specified
type: product-law
aspect: preview
object: WebsitePreview
requirement: >
  Previewing a site renders or opens a selected lane without silently changing
  source files, publish targets, Git state, remote services, or CMS storage.
acceptance:
  - Draft preview does not update publish manifests.
  - Local/dev/remote visit actions identify the lane being opened.
  - Preview errors are shown as preview failures, not save or publish success.
```

```mcel-requirement
id: website-builder.publish.explicit-lane
app: website-builder
status: specified
type: product-law
aspect: publishing
object: WebsitePublishPlan
requirement: >
  Publishing is always lane-specific and explicit. Local server, dev deployment,
  and remote production/Coolify targets must be visually and semantically
  separate.
acceptance:
  - Each publish action names its lane.
  - Remote production requires target evidence before deployment.
  - Successful publish records lane-specific receipt or verification.
  - Failed publish does not mark another lane as published.
```

```mcel-requirement
id: website-builder.remote.coolify-governed
app: website-builder
status: specified
type: product-law
aspect: remote-publishing
object: CoolifyPublishTarget
requirement: >
  Remote Coolify publishing requires explicit remote target configuration,
  command preview, preflight evidence, acknowledgement, and post-publish
  verification before the public URL is treated as live.
current_state: >
  The existing remote Coolify publishing runbook documents the operator path and
  the live app has remote production/Coolify target controls.
acceptance:
  - Remote host, domain, project, environment, Directus URL, and compose/service
    details are visible before publish.
  - Required acknowledgement gates risky publish actions.
  - Visit URL is only enabled when a valid target URL is known.
```

```mcel-requirement
id: website-builder.blog-runtime.explicit-dependencies
app: website-builder
status: specified
type: product-law
aspect: runtime
object: WebsiteRuntimeContract
requirement: >
  Blog/runtime setup must expose dependency order, CMS/database choices, API
  routes, local content state, and generated pages before mutating runtime files
  or publishable data.
acceptance:
  - Database, CMS, and blog layers are shown separately.
  - Directus connection choices are explicit.
  - Existing deployed database protection is visible.
  - Generated blog pages are labeled as generated artifacts.
```

```mcel-requirement
id: website-builder.git.delegated-to-git-tools
app: website-builder
status: planned
type: product-law
aspect: git
object: WebsiteChangeSet
requirement: >
  Website Builder may show website-scoped Git evidence, but add/commit/push
  workflows should be delegated to Git Tools so repository mutation follows the
  governed Git Tools receipts and recovery model.
acceptance:
  - Website save does not commit.
  - Website publish does not push to Git.
  - Website-scoped changed files can be handed to Git Tools.
  - Git Tools push receipts can be linked back to the website project.
```

```mcel-requirement
id: website-builder.generated-editor.no-cheats
app: website-builder
status: specified
type: product-law
aspect: ai-editing
object: WebsiteEditProposal
requirement: >
  AI/generated website edits must use the blessed generated-editor path with
  grounding, validation, patch proposal, full-file replacement packaging, and
  new_patch-compatible artifacts. Deterministic cheats must not replace the
  discovery/repair workflow.
current_state: >
  Existing golden-path smoke tests assert that generated website repair uses the
  blessed generated editor path instead of deterministic edit fixtures.
acceptance:
  - Generated edits include grounding evidence.
  - Patch proposal validation runs before materialization.
  - Replacement artifacts are full-file end states.
  - Site-scoped patch commands are visible.
```

```mcel-requirement
id: website-builder.adapter.truth-gated-readiness
app: website-builder
status: planned
type: semantic-runtime-law
aspect: mcel-truth-gate
object: WebsiteBuilderSemanticAdapter
requirement: >
  Website Builder must not be reported as semanticRuntimeReady until a domain
  adapter can derive website state, expose site/edit/runtime/publish intents,
  preflight risky actions, produce receipts, and classify recovery paths.
current_state: >
  Website Builder has a working app and website project model, but no dedicated
  adapter is registered with the MCEL domain-adapter registry.
acceptance:
  - Adapter derives selected site, dirty state, lane state, runtime state, and
    publish target state.
  - Adapter marks read-only, local mutation, remote mutation, and Git handoff
    intents separately.
  - MCEL truth gate does not report fullApplicationSemanticReady until all
    required intents are covered.
```

## Layout regions

The Website Builder layout should be organized around the website project and its separated layers.

```mcel-region
id: website-builder.region.identity
app: website-builder
status: specified
region: website-identity-header
role: identity-header
responsibility: >
  Identify the selected website, current site metadata, dirty/save state, and
  source-vs-saved status across edit, preview, and publish workflows.
owns:
  - selected site name
  - selected site id
  - current site metadata
  - dirty/save status
must_show:
  - whether a site is selected
  - whether source differs from saved state
must_not_contain:
  - hidden Git push
  - hidden remote publish
layout_laws:
  - site identity remains visible while editing, previewing, and publishing
```

```mcel-region
id: website-builder.region.site-selector
app: website-builder
status: specified
region: saved-site-navigation
role: navigation
responsibility: >
  Let the user choose, create, search, and locate saved website projects without
  performing destructive site operations implicitly.
owns:
  - saved site list
  - create/new website action
  - site search or filter
must_show:
  - which saved site is selected
  - which folder backs the selected site
must_not_contain:
  - destructive site delete without danger boundary
```

```mcel-region
id: website-builder.region.design-surface
app: website-builder
status: specified
region: primary-design-surface
role: primary-work-surface
responsibility: >
  Own the author-facing GrapesJS design canvas, page blocks, and draft page
  state during normal website editing.
owns:
  - GrapesJS design canvas
  - page block editing
  - draft page state
must_show:
  - editability
  - draft preview state
  - editor load/fallback state
layout_laws:
  - design surface remains visually primary during normal editing
  - helper panels must not obscure the selected page without explicit mode change
```

```mcel-region
id: website-builder.region.preview-surface
app: website-builder
status: specified
region: website-preview-surface
role: preview-surface
responsibility: >
  Show draft, local, dev, or remote preview lanes and their availability without
  implying that preview equals publish success.
owns:
  - draft preview
  - local preview
  - dev preview
  - remote preview
must_show:
  - active preview lane
  - preview URL or unavailable reason
must_not_contain:
  - publish success receipts
layout_laws:
  - preview lane is not the same as publish lane mutation
```

```mcel-region
id: website-builder.region.source-and-manifest
app: website-builder
status: specified
region: source-manifest-evidence-panel
role: evidence-panel
responsibility: >
  Expose site source, builder metadata, generated artifacts, runtime selection,
  and manifest evidence for the selected website.
owns:
  - site.json evidence
  - builder.json evidence
  - source preview
  - page runtime selection
must_show:
  - source artifact names
  - manifest/runtime status
layout_laws:
  - generated runtime evidence is secondary to author-owned page source
```

```mcel-region
id: website-builder.region.runtime-products
app: website-builder
status: specified
region: website-runtime-inspector
role: runtime-inspector
responsibility: >
  Show blog, route, backend, Directus, SQLite, dependency, and generated-runtime
  evidence separate from page styling controls.
owns:
  - blog product
  - API routes
  - backend runtime summary
  - Directus/SQLite state
must_show:
  - enabled runtime features
  - missing dependencies
  - generated routes
must_not_contain:
  - page styling controls
```

```mcel-region
id: website-builder.region.publish-actions
app: website-builder
status: specified
region: governed-publish-action-panel
role: governed-action-panel
responsibility: >
  Own explicit save, local publish, dev deploy, remote publish, visit, target-
  lane, and acknowledgement actions without hidden Git side effects.
owns:
  - save action
  - local server publish
  - dev deploy
  - remote production publish
  - visit buttons
must_show:
  - target lane
  - command preview or publish plan
  - acknowledgement state
must_not_contain:
  - Git commit/push as a side effect
layout_laws:
  - publish lanes are grouped and labeled distinctly
  - remote production is visually higher risk than local preview
```

```mcel-region
id: website-builder.region.deploy-preflight
app: website-builder
status: specified
region: deployment-preflight-and-receipt
role: confirmation-and-receipt
responsibility: >
  Show target lane, command/controller preview, warnings, acknowledgement,
  execution receipt, and recovery summary for risky deploy actions.
owns:
  - deploy preflight modal
  - command preview
  - warnings
  - acknowledgement
  - recovery summary
must_show:
  - exact target lane
  - exact command or controller action
  - warnings before execution
layout_laws:
  - risky deployment requires explicit acknowledgement
```

```mcel-region
id: website-builder.region.git-evidence
app: website-builder
status: planned
region: repository-evidence-handoff
role: advanced-evidence
responsibility: >
  Show website-scoped repository paths, Git history references, Git Tools
  handoff state, and commit/push evidence as advanced information.
owns:
  - website-scoped changed files
  - Git history references
  - handoff to Git Tools
  - commit/push receipts from Git Tools
must_show:
  - repository-relative paths under runtime/websites/<site-id>
  - whether Git evidence is local history or remote push evidence
must_not_contain:
  - default raw Git plumbing
```

```mcel-region
id: website-builder.region.chat-helper
app: website-builder
status: specified
region: ai-helper-companion
role: helper-companion
responsibility: >
  Host website-editing suggestions and grounding prompts while keeping author-
  owned source and governed publish controls authoritative.
owns:
  - website editing chat
  - generated edit suggestions
  - grounding/repair prompts
must_show:
  - whether suggestions are applied or only proposed
must_not_contain:
  - direct unreviewed writes
  - hidden deployment
layout_laws:
  - AI help is secondary to author-owned source and governed publish controls
```

## Intents

```mcel-intent
id: website-builder.intent.list-sites
app: website-builder
status: specified
intent: listSites
risk: read-only
current_adapter_status: not-registered
target_adapter_status: executable
requires:
  - repository root
produces:
  - site list
  - site identities
  - manifest availability evidence
```

```mcel-intent
id: website-builder.intent.select-site
app: website-builder
status: specified
intent: selectSite
risk: local-state
current_adapter_status: not-registered
target_adapter_status: executable
requires:
  - site id
  - site manifest
produces:
  - selected site state
  - builder state load result
  - preview source state
```

```mcel-intent
id: website-builder.intent.edit-draft
app: website-builder
status: specified
intent: editDraft
risk: local-state
current_adapter_status: not-registered
target_adapter_status: executable
requires:
  - selected site
  - editable design/source surface
produces:
  - dirty draft
  - changed field evidence
```

```mcel-intent
id: website-builder.intent.save-site
app: website-builder
status: specified
intent: saveSite
risk: local-file-mutation
current_adapter_status: not-registered
target_adapter_status: executable
requires:
  - selected site
  - dirty draft
  - intended artifact list
preflight:
  - site path remains under runtime/websites/<site-id>
  - generated runtime facts are not written into author-owned source unless explicitly intended
produces:
  - saved source artifacts
  - dirty state cleared or save error
```

```mcel-intent
id: website-builder.intent.preview-draft
app: website-builder
status: specified
intent: previewDraft
risk: read-only
current_adapter_status: not-registered
target_adapter_status: executable
requires:
  - selected site
  - draft or saved source
produces:
  - preview render status
  - parse/runtime error evidence
```

```mcel-intent
id: website-builder.intent.configure-blog-runtime
app: website-builder
status: specified
intent: configureBlogRuntime
risk: local-file-mutation
current_adapter_status: not-registered
target_adapter_status: executable
confirmation: required
requires:
  - selected site
  - runtime layer contract
  - CMS/database choice
preflight:
  - existing database protection
  - Directus URL validity when required
  - generated route list
produces:
  - runtime setup receipt
  - runtime manifest update
  - activity log
```

```mcel-intent
id: website-builder.intent.publish-local-server
app: website-builder
status: specified
intent: publishLocalServer
risk: local-state
current_adapter_status: not-registered
target_adapter_status: executable
confirmation: required
requires:
  - selected site
  - saved source
  - local platform target
preflight:
  - generated compose path
  - port availability
  - site runtime availability
produces:
  - local publish receipt
  - local visit URL
  - verification status
```

```mcel-intent
id: website-builder.intent.publish-dev
app: website-builder
status: specified
intent: publishDev
risk: remote-mutation
current_adapter_status: not-registered
target_adapter_status: executable
confirmation: required
requires:
  - selected site
  - saved source
  - dev lane target
preflight:
  - selected lane evidence
  - publish manifest plan
produces:
  - dev publish receipt
  - dev visit URL
  - verification status
```

```mcel-intent
id: website-builder.intent.publish-remote-prod
app: website-builder
status: specified
intent: publishRemoteProduction
risk: remote-mutation
current_adapter_status: not-registered
target_adapter_status: executable
confirmation: required
requires:
  - selected site
  - saved source
  - accepted remote target setup
  - Coolify controller or remote publish command
preflight:
  - remote host evidence
  - domain evidence
  - Directus/storage evidence when required
  - command preview
  - explicit acknowledgement
produces:
  - remote publish receipt
  - remote visit URL
  - recovery guidance on failure
```

```mcel-intent
id: website-builder.intent.open-visit-url
app: website-builder
status: specified
intent: openVisitUrl
risk: read-only
current_adapter_status: not-registered
target_adapter_status: executable
requires:
  - selected lane
  - normalized visit URL
produces:
  - navigation receipt or unavailable reason
```

```mcel-intent
id: website-builder.intent.prepare-git-handoff
app: website-builder
status: planned
intent: prepareGitToolsHandoff
risk: read-only
current_adapter_status: not-registered
target_adapter_status: executable
requires:
  - selected site
  - repository root
  - working tree status
produces:
  - website-scoped changed-file basket
  - suggested Git Tools action
```

```mcel-intent
id: website-builder.intent.apply-generated-edit
app: website-builder
status: specified
intent: applyGeneratedWebsiteEdit
risk: local-file-mutation
current_adapter_status: not-registered
target_adapter_status: executable
confirmation: required
requires:
  - selected site
  - grounded edit proposal
  - validation result
  - full-file replacement payload
preflight:
  - patch paths stay under selected site or explicitly approved shared source
  - new_patch-compatible artifact exists
  - user reviews replacement summary
produces:
  - changed site files
  - patch receipt
  - validation evidence
```

## Acceptance

```mcel-acceptance
id: website-builder.acceptance.website-project-model
app: website-builder
status: specified
type: acceptance
requires:
  - hub-site and johnrraymond are discoverable as saved website projects
  - each site exposes site.json and builder.json
  - entry HTML, stylesheet, script, and runtime files are visible
  - selected site identity is shown in the app
```

```mcel-acceptance
id: website-builder.acceptance.save-preview-publish-separated
app: website-builder
status: specified
type: acceptance
requires:
  - save does not publish
  - preview does not publish
  - publish does not commit
  - commit/push is delegated to Git Tools or an explicit advanced Git workflow
  - each action produces its own status or receipt
```

```mcel-acceptance
id: website-builder.acceptance.publish-lanes-separated
app: website-builder
status: specified
type: acceptance
requires:
  - local server, dev, and remote production lanes are separately labeled
  - remote production requires stronger target evidence than local preview
  - visit buttons identify their lane
  - failed publish does not mark the lane verified
```

```mcel-acceptance
id: website-builder.acceptance.blog-runtime-evidence
app: website-builder
status: specified
type: acceptance
requires:
  - blog runtime layers show dependency order
  - Directus and SQLite choices are explicit
  - generated blog page artifacts are labeled
  - API routes are visible before runtime-backed publish
  - existing deployed database protection is visible
```

```mcel-acceptance
id: website-builder.acceptance.semantic-runtime
app: website-builder
status: planned
type: acceptance
requires:
  - Website Builder domain adapter derives selected site state
  - adapter exposes read, draft, save, preview, runtime setup, publish, and Git handoff intents
  - remote publish intents require confirmation and recovery receipts
  - MCEL truth gate should eventually report fullApplicationSemanticReady only after every required intent is covered
```

## MCEL Lab findings this document should generate

```mcel-finding
id: website-builder.finding.no-semantic-adapter
app: website-builder
status: open
aspect: semantic-runtime
severity: medium
problem: >
  Website Builder has a real project/publish model, but no dedicated
  website-builder-semantic-adapter.js is registered with the MCEL truth gate.
desired_behavior: >
  MCEL Lab should compare this requirements document to a Website Builder domain
  adapter that derives selected site state, lane state, runtime state, publish
  target evidence, receipts, and recovery coverage.
```

```mcel-finding
id: website-builder.finding.layout-layer-separation
app: website-builder
status: open
aspect: layout
severity: medium
problem: >
  Website source, builder state, runtime products, publish lanes, and Git
  evidence are all present in the product area, but MCEL has not yet proven that
  these layers are consistently separated in the rendered app.
desired_behavior: >
  MCEL Lab should infer the Website Builder regions and verify that editing,
  preview, runtime configuration, publish, and Git evidence have distinct owners.
```

```mcel-finding
id: website-builder.finding.git-handoff
app: website-builder
status: open
aspect: git-integration
severity: medium
problem: >
  Website edits naturally produce repository file changes, but the requirements
  need a clear handoff from Website Builder to Git Tools for selected-file
  commit and governed push.
desired_behavior: >
  Website Builder should produce a website-scoped changed-file basket that Git
  Tools can use for add/commit/push workflows without hiding Git mutation inside
  save or publish.
```

```mcel-finding
id: website-builder.finding.website-use-cases-drive-layout
app: website-builder
status: open
aspect: layout-language
severity: low
problem: >
  Websites reveal a layout grammar based on layers: source, preview, runtime,
  publish target, and repository evidence. That grammar is not yet formalized
  across MCEL app requirements.
desired_behavior: >
  MCEL requirements language should support layered layout ownership so apps can
  distinguish author-owned source, generated artifacts, runtime evidence,
  remote target evidence, and repository evidence.
```

## Relationship to existing docs

`pretty_docs/website-builder-remote-coolify-publishing.md` remains the operator runbook for remote Coolify publishing. This requirements document is the product/specification contract that tells MCEL what Website Builder and saved websites are supposed to mean.

`pretty_docs/mcel-git-tools-requirements.md` remains the Git Tools contract. Website Builder should not duplicate the governed local-Gitea push workflow; it should hand website-scoped repository changes to Git Tools when commit or push evidence is needed.


## Runtime diagnosis contract

```mcel-runtime-check
id: website-builder.runtime-check.default-primary-preview
app: website-builder
status: specified
mode: default
contract: website-builder.contract.default.app-health
check: primary-surface
severity: critical
primary_surface_id: website-builder.surface.preview
host_selector: ".website-builder-preview"
editor_selector: ".website-builder-preview"
min_width: 420
min_height: 320
observes:
  - ".website-builder-preview"
expects:
  - Website Builder preview/design surface is visible and usable.
  - The selected site surface is not collapsed by inspector or publishing panels.
failure_message: Website Builder default mode must expose a usable preview/design surface.
next_probe: layout.ownerProbe
source_binding: website-builder.binding.builder-runtime
test_binding: website-builder.test.documentation-contract
```

```mcel-runtime-check
id: website-builder.runtime-check.default-required-regions
app: website-builder
status: specified
mode: default
contract: website-builder.contract.default.app-health
check: required-regions-visible
severity: critical
observes:
  - "#website-builder-app"
  - ".website-builder-main"
  - ".website-builder-summary"
  - ".website-builder-preview"
  - ".website-builder-inspector"
required_regions:
  - website-builder.region.root | #website-builder-app | Website Builder app root
  - website-builder.region.main | .website-builder-main | Website Builder shell
  - website-builder.region.summary | .website-builder-summary | Website summary
  - website-builder.region.preview | .website-builder-preview | Preview/design surface
  - website-builder.region.inspector | .website-builder-inspector | Inspector
expects:
  - Root, shell, summary, preview, and inspector remain visible.
failure_message: Website Builder default mode must preserve summary, preview, and inspector.
next_probe: layout.baseline
source_binding: website-builder.binding.builder-runtime
test_binding: website-builder.test.documentation-contract
```

```mcel-runtime-check
id: website-builder.runtime-check.default-overlay-policy
app: website-builder
status: specified
mode: default
contract: website-builder.contract.default.app-health
check: overlay-policy
severity: warning
observes:
  - "#mc-widget-editor-root"
  - "[data-mcel-proof-surface]"
  - ".floating-tab"
  - ".side-tab"
expects:
  - MCEL/widget/proof overlays are not visible while using the default builder surface.
forbids:
  - shared.overlay.widget-editor | #mc-widget-editor-root | Widget editor overlay
  - shared.overlay.proof-surface | [data-mcel-proof-surface] | MCEL proof surface
  - shared.overlay.floating-tab | .floating-tab, .side-tab | Floating diagnostic tab
failure_message: Website Builder default mode should not be covered by diagnostic overlays.
next_probe: overlay.detector
source_binding: website-builder.binding.builder-runtime
test_binding: website-builder.test.documentation-contract
```
