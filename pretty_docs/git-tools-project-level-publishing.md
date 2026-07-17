# Git Tools Project-Level Publishing Requirements

This document captures one next desired Git Tools interaction model: project-level publishing and file triage. It is a product and implementation planning note, not a claim that the current UI already behaves this way. The broader documentation-first MCEL requirements contract for Git Tools lives in `pretty_docs/mcel-git-tools-requirements.md`; that contract records the app-level product laws, semantic intents, runtime status, safety boundaries, receipts, recovery, and acceptance criteria.

## Problem statement

Git Tools currently treats the local Gitea workflow as part of the broader Git/Gitea server control area. That is useful for administration, but it buries actions that users think of as project actions.

A user chooses a project first. After that, the most common publishing actions should be available on that project's card, not hidden inside the server-control workflow.

## Primary rule

The project card is the home for project publishing actions.

Each active project card should expose a small, first-class publishing strip near the project status and inspection summary. At minimum, that strip should include:

- **Push to Local Gitea**: publish the selected project to the configured machine-local Gitea remote.
- **Push Remote Origin**: push the selected project's current HEAD to the `origin` remote, which will usually be GitHub but must not be hard-coded as GitHub.

These actions are project-level operations. They may call existing Git/Gitea backend operations, but the user should not need to open the full server-control panel just to push the current project.

## Local Gitea push behavior

The local Gitea action on the project card should mean:

1. inspect the project path and confirm it is a Git repository;
2. confirm the project has a HEAD commit that can be pushed;
3. derive or show the local Gitea target, normally `local-gitea` at `http://localhost:3000/local/<repo>.git`;
4. create or verify the local Gitea repository when the backend can do that safely;
5. add or repair the `local-gitea` remote when needed;
6. push the current HEAD to `local-gitea`;
7. keep `origin` unchanged by default.

The button label should stay user-facing: **Push to Local Gitea**. The details pane or evidence log can show the underlying command, such as `git push local-gitea HEAD`.

The button should be visible even when disabled. Disabled or warning states should explain the next required step, such as "not a Git repository", "no HEAD commit", "local Gitea is not running", or "Docker is unavailable to the backend process".

## Remote origin push behavior

The remote-origin action on the project card should mean:

1. inspect the project's remotes;
2. confirm `origin` exists;
3. show the current `origin` fetch/push URL;
4. push the current HEAD to `origin`;
5. report authentication or rejected-push failures in the same project-level evidence area.

The button label should be **Push Remote Origin** or **Push to Origin**. The UI may add a small host hint such as "GitHub" only when it can infer that from the URL. The operation itself must remain remote-name based: `origin` is the target, not a GitHub-only concept.

If `origin` is missing, the project card should not send the user to a generic Gitea workflow. It should show a local setup affordance for adding or editing `origin`, with a clear preview of `git remote add origin <url>` or `git remote set-url origin <url>`.

## Git status should be a selectable file workbench

The Git Tools status area should not be treated as a read-only command transcript. It should be a selectable workbench built from the same information shown by `git status --porcelain` and related inspection calls.

A user should be able to click individual changed files or groups of files and choose what happens next. The core use cases are:

- mark selected files as files to ignore;
- generate a narrow `.gitignore` rule for selected files;
- generate a directory-level or pattern-level ignore rule after preview;
- stage only selected files;
- commit only selected files;
- create a new branch for the selected work;
- switch to an existing branch after safety checks;
- push a selected-file commit to `origin` or `local-gitea`.

The file list should make the difference between tracked modifications, untracked files, deleted files, ignored files, and blocked files visible. Selection must never silently include files outside the user's chosen set.

## Ignore-rule workflow from selected files

When the user selects one or more changed files and chooses an ignore action, Git Tools should propose rules instead of blindly editing `.gitignore`.

The proposal should show:

1. the selected paths;
2. whether each path is tracked or untracked;
3. whether a file-level, directory-level, or glob/pattern rule is recommended;
4. the exact `.gitignore` lines that will be added;
5. whether any selected tracked file would require a separate untrack action such as `git rm --cached <path>`.

Adding an ignore rule should not be confused with removing a tracked file from the index. If a selected file is already tracked, Git Tools should explain that `.gitignore` only affects future untracked matches and should offer a separate, explicit "stop tracking but keep local file" action when appropriate.

## Selected-file commit workflow

The commit workbench should be able to operate on a selected file set, not only on all pending changes.

For a selected-file commit, Git Tools should:

1. show the selected files and their status;
2. run the Gitignore gate and Secrets / Filter gate against the selected set;
3. stage only the selected files;
4. verify the index contains only the intended paths before committing;
5. create the commit with the user's message;
6. show the resulting commit hash and the exact staged path list.

The UI should make it obvious when other dirty files still exist after the commit. Those files are not errors; they are simply outside the chosen commit set.

## Branch workflow for selected files

Users often need to put only some local changes onto a branch. Git Tools should support that explicitly.

For selected files, the branch workflow should support:

- create branch from the current HEAD, stage selected files, commit, and push;
- use an existing branch after previewing the checkout/switch impact;
- keep unrelated dirty files safe by refusing branch switches that would overwrite or mix unselected changes;
- optionally stash or shelve unselected changes only when the user chooses that path.

The evidence area should show the branch name, base commit, selected files, commit hash, and push target. Pushing selected work should mean pushing the commit created from the selected files, not pushing every dirty file in the working tree.

## Unified local secrets model

Git Tools already has a Secrets / Filter workflow with several rule-based checks for values that might be local secrets, private paths, credentials, or other sensitive material. That scanner should be unified with the repository's local secret denylist and commit hook model.

The canonical local secret denylist file for this repository is `local.secrets`. The repo already ignores that file in `.gitignore`, and the local pre-commit hook at `.githooks/pre-commit` reads `local.secrets` to block staged files that contain protected values. Users may say "local secret file" or "local.secret", but implementation should use one canonical repo file name unless a deliberate migration is planned.

Git Tools should understand these local-secret components as one system:

| Component | Desired Git Tools behavior |
| --- | --- |
| `local.secrets` | Detect whether it exists, keep it untracked, show its protection status, and offer safe append/update actions for sensitive local values. |
| `.gitignore` | Verify that `local.secrets` is ignored, and propose the exact rule when missing. |
| `.githooks/pre-commit` | Detect whether the hook exists and whether the repository is configured to run it. |
| Secrets / Filter scanner | Use scanner findings and selected-file findings as interactive review evidence before staging or committing. |
| Commit workbench | Treat unresolved local-secret findings, missing denylist coverage, or an inactive hook as visible commit gates. |

The local secret denylist must not be displayed in full in normal UI logs. Git Tools may show counts, fingerprints, rule names, file names, and whether a candidate value is already protected, but it should avoid echoing raw secrets.

## Local secrets and commit-hook acceptance states

The project card and commit workbench should expose local-secret readiness as a small set of states:

| State | Behavior |
| --- | --- |
| `local.secrets` missing | Show setup action to create it and confirm `.gitignore` coverage. |
| `local.secrets` present but not ignored | Block commit setup until an ignore rule is previewed and added. |
| `.githooks/pre-commit` missing | Warn that selected-file commits rely only on UI/backend scans unless the hook is installed. |
| hook present but not active | Offer an explicit setup action such as configuring the repo's hook path to `.githooks`. |
| scanner unavailable | Allow deliberate override only with visible evidence that hook/denylist protection is still active. |
| scanner findings unresolved | Keep commit disabled for selected files until the user removes the value, ignores the file, or records a safe local-secret rule. |
| clean selected set | Allow staging and commit of only the selected files. |

The commit hook is the last local safety net. The UI scanner should improve review and explain findings before the commit, but it should not replace the hook or make the hook invisible.


## What stays in the Git/Gitea server area

The full Git/Gitea server controls still belong in the support/server area. That area remains the place for:

- starting, stopping, restarting, and viewing logs for the local Gitea service;
- changing server-level owner/repository defaults;
- advanced local Gitea remote configuration;
- external mirror setup;
- diagnostic and recovery actions;
- command previews that are not specific to a single project card.

The server area is support infrastructure. It should not be the only path to the everyday project publishing actions.

## Project card states

A project card should compute and display enough state for safe publishing:

| State | Project card behavior |
| --- | --- |
| Not inspected | Show publishing buttons in a pending or refresh-required state. |
| Not a Git repository | Disable push actions and offer the appropriate setup or inspect action. |
| No HEAD commit | Disable push actions and explain that an initial commit is required. |
| Dirty working tree | Allow push of HEAD, but warn that uncommitted changes are not included. |
| Missing `local-gitea` remote | Keep **Push to Local Gitea** visible and route through local setup before pushing. |
| Missing `origin` remote | Keep **Push Remote Origin** visible but disabled until an origin URL is configured. |
| Local Gitea unavailable | Keep **Push to Local Gitea** visible and explain whether the blocker is Docker, service status, repository creation, or remote configuration. |
| Dirty files selected | Show selected-file actions for ignore, stage, commit, branch, and push, while making unselected dirty files explicit. |
| `local.secrets` not ready | Show whether the denylist is missing, not ignored, stale, or disconnected from the commit hook. |
| Commit hook inactive | Warn before selected-file commits and offer a setup action for `.githooks/pre-commit`. |
| Authentication failure | Keep the button visible and report the host's auth failure in project evidence. |

## Evidence and safety requirements

Both project-level push buttons must produce visible operation evidence. The evidence should live close to the selected project or open the shared operation evidence panel focused on the project that initiated the action.

Before running a state-changing operation, the UI should show:

- the project path;
- the target remote name;
- the target URL when available;
- the command family being run;
- the selected file set for status, ignore, branch, stage, and commit actions;
- whether the Gitignore gate, Secrets / Filter gate, `local.secrets`, and commit hook are ready;
- whether `origin` will be preserved or changed.

The local Gitea flow must preserve `origin` by default. Switching `origin` to local Gitea is an advanced action and should not be hidden behind the normal **Push to Local Gitea** button.

## First implementation target

The first implementation should change the project list/card surface before it changes the deep server controls.

Likely implementation targets are:

```text
main_computer/web/applications/scripts/git-tools-project-panel.js
main_computer/web/applications/scripts/git-tools-project-card-subscreen.js
main_computer/web/applications/scripts/git-tools-project-workflow.js
main_computer/web/applications/scripts/git-tools-file-basket.js
main_computer/web/applications/scripts/git-tools-gitignore-workbench.js
main_computer/web/applications/scripts/git-tools-secrets-filter-workbench.js
main_computer/web/applications/scripts/git-tools-commit-workbench.js
main_computer/web/applications/scripts/git-tools-status-api.js
main_computer/web/applications/scripts/git-tools-server-panel.js
main_computer/web/applications/apps/git-tools.html
main_computer/viewport_routes_git.py
main_computer/git_tools.py
git_dirty.py
.githooks/pre-commit
.gitignore
tools/sync_private_state.py
tests/test_git_tools_project_panel.py
tests/test_git_page_wizard_workflow.py
tests/test_git_tools_server_panel.py
```

The first pass can reuse existing backend routes for local Gitea setup and push. If a dedicated project-level route is added later, it should be a thin project-context wrapper around the same guarded backend operation rather than a separate unverified command path.

## Acceptance criteria

The documentation target for the redesign is met when the product behavior satisfies these checks:

1. A user can select or inspect a project and see **Push to Local Gitea** on the project card.
2. A user can see **Push Remote Origin** or **Push to Origin** on the same project card.
3. The ordinary local Gitea push path does not require opening the Git/Gitea server-control panel.
4. The ordinary origin push path does not require opening the Git/Gitea server-control panel.
5. Missing preconditions are explained on the project card, not only in a remote or server panel.
6. `origin` is preserved during local Gitea publishing unless the user explicitly chooses an advanced switch-origin action.
7. Every project-level push leaves visible operation evidence tied to the project that initiated it.
8. A user can select files from the status list and commit only those files.
9. A user can select files from the status list and generate previewed `.gitignore` rules without accidentally untracking files.
10. A user can create or choose a branch for a selected-file commit and push that branch to `origin` or `local-gitea`.
11. The commit workbench understands `local.secrets`, verifies that it is ignored, and reports whether `.githooks/pre-commit` is active.
12. Secrets / Filter findings, local-secret denylist coverage, and commit-hook readiness appear as one coherent commit gate instead of separate unrelated tools.
