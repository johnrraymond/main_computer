---
spec_id: terminal
app_id: terminal
title: Terminal preview mounts
keywords: terminal, shell, command, powershell, cmd, git, pytest, test, tests, list files, directory, interrupt, active terminal, run
output_kinds: computer_mount
---

# Terminal preview mounts

Use this spec when the user asks the text console to request Terminal, run a
local command, list files, run Git, run tests, interrupt Terminal, or reuse an
active Terminal.

A Terminal preview mount is not execution. It is a request for the UI/backend to
show a Terminal action preview.

## Canonical output forms

Inside a fenced block tagged exactly `computer`, use only these `/act` forms.

Run a new terminal command from the repository root:

```computer
/act terminal run "<command>" --cwd repo-root
```

Run a command in the active terminal from the repository root:

```computer
/act terminal run-in active "<command>" --cwd repo-root
```

Interrupt the active terminal:

```computer
/act terminal interrupt active
```

## Rules

- Do not invent `/act` verbs.
- Do not use `/act list`, `/act open`, `/act execute`, `/act shell`, or
  `/act terminal ls`.
- Do not put absolute Windows paths in `/act` lines.
- Use `repo-root` as the cwd vocabulary for repository-relative commands.
- Put terminal commands inside double quotes.
- Use `terminal run-in active` only when the user explicitly asks to reuse the
  active/current terminal or interrupt an active terminal.
- Do not claim the command executed.

## Examples

User intent: list the files in `main_computer`.

```computer
/act terminal run "dir main_computer" --cwd repo-root
```

User intent: run Git status.

```computer
/act terminal run "git status" --cwd repo-root
```

User intent: reuse the active terminal and run tests.

```computer
/act terminal run-in active "python -m pytest" --cwd repo-root
```

User intent: interrupt the active terminal.

```computer
/act terminal interrupt active
```
