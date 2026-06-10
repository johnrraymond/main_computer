---
spec_id: repo_edit
app_id: repo_edit
title: Repo-root edit handoff
keywords: edit, update, modify, change, create, add, remove, refactor, patch, file, README, test, implementation
output_kinds: repo_edit_handoff
---

# Repo-root edit handoff

Use this spec when the user asks the text console to prepare, request, or plan a
code/documentation edit inside the current repository root.

A repo-root edit handoff is not an applied edit. It is a bounded request for a
future editor pipeline. The target root is the text-console repository root by
default.

## Runtime prompt

Use repo_edit when the user asks to prepare, request, or plan a repository file
change from the current text-console repo root.

A repo-edit handoff is not an applied edit. It is a bounded request for a future
editor pipeline.

Canonical form: include one fenced block tagged exactly `repo-edit` containing
one JSON object:

```repo-edit
{
  "mode": "repo_root_edit_request",
  "target_root": "repo-root",
  "request_for_editor": "<natural-language edit request bounded to the current repository>",
  "requires_confirmation": true,
  "blocked_reasons": []
}
```

Rules:
- Use `target_root: "repo-root"`.
- Keep `request_for_editor` repository-relative and natural-language.
- Do not include absolute Windows paths.
- Do not claim files changed, tests ran, commits happened, or patches applied.
- If a terminal action is also requested, include both a `computer` block and a `repo-edit` block.

Examples:
- update README.md -> request_for_editor mentions README.md and the requested doc change.
- make smoke summary concise -> request_for_editor mentions the smoke summary and concise output.

## Canonical output form

When a repo edit is requested, include one fenced block tagged exactly
`repo-edit` containing exactly one JSON object:

```repo-edit
{
  "mode": "repo_root_edit_request",
  "target_root": "repo-root",
  "request_for_editor": "<natural-language edit request bounded to the current repository>",
  "requires_confirmation": true,
  "blocked_reasons": []
}
```

## Rules

- Do not claim files were edited, patched, committed, or tested.
- Do not include full replacement files in this handoff.
- Do not target absolute Windows paths.
- Do not target parent directories outside `repo-root`.
- Keep `request_for_editor` connected to the user's runtime request.
- Use `blocked_reasons` only when the request is unsafe or outside repo-root.
- This spec can be combined with Terminal preview mounts when the user asks to
  inspect, run tests, or execute a command as part of the same request.

## Examples

User intent: update README.md to mention text-console mount previews.

```repo-edit
{
  "mode": "repo_root_edit_request",
  "target_root": "repo-root",
  "request_for_editor": "Update README.md in the current repository to mention text-console preview-only computer mount requests.",
  "requires_confirmation": true,
  "blocked_reasons": []
}
```

User intent: make a smoke summary concise and run tests.

```repo-edit
{
  "mode": "repo_root_edit_request",
  "target_root": "repo-root",
  "request_for_editor": "Update the smoke summary code in the current repository so the default output is concise and useful.",
  "requires_confirmation": true,
  "blocked_reasons": []
}
```
