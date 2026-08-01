# Conductor Script Hook and Surface — Pre-Specification

## Status

Experimental design document.

This document records the current direction for connecting repository scripts and tools to the Conductor application. It is intentionally less formal than an MCEL requirements document and should not yet be treated as a stable interface contract.

The immediate goal is to preserve the product idea, provide enough guidance for a small implementation experiment, and avoid committing to details that may become unnecessary once the Conductor surface and related MCEL features mature.

## Problem

The repository contains many scripts and command-line tools, but Conductor does not currently know which calls are useful to a person.

A script may support:

- several subcommands;
- required positional arguments;
- optional flags;
- developer-only controls;
- diagnostic calls;
- mutating calls;
- multiple reasonable workflows.

Automatically discovering that a file is executable does not tell Conductor how a user would want to use it.

Displaying a raw command box also does not provide much value over the Terminal application.

Conductor needs a small amount of help from each participating script:

> Which calls represent meaningful user operations?

The script hook provides that help without requiring a large manifest or duplicating the script’s existing argument parser.

## Direction

A Conductor-aware script declares the useful ways it should be called.

Conductor then uses those declarations to build a guided interface.

The working principle is:

> Scripts declare normal calling conventions. Conductor derives supporting controls and advanced options where practical.

The annotation should remain:

- small;
- close to the script;
- readable by machines;
- easy to add during normal script development;
- easy to backfill;
- independent of the script language where possible;
- incomplete by design.

It should not become a second implementation of the script’s help system.

## Proposed script hook

### Simple script

```python
# conductor: v1
# run: python {script} :: Check whether Docker is installed and responding.
```

This tells Conductor:

- the script participates in Conductor;
- it offers one user-facing call;
- the call has no user-supplied inputs;
- the text after `::` explains the purpose of the call.

### Script with several useful calls

```python
# conductor: v1; args=argparse
# run: python {script} publish {site_id} --lane {lane} :: Publish a website lane.
# run: python {script} verify {site_id} --lane {lane} :: Verify a website lane.
# run: python {script} logs {site_id} --lane {lane} --tail {tail} :: Show recent logs.
# vars: {"site_id":"Website project ID","lane":"Deployment lane","tail":"Number of log lines"}
```

This tells Conductor:

- the script offers several separate operations;
- placeholders represent values the user must supply;
- the same placeholder may be reused across calls;
- the script uses `argparse`, which may provide additional argument information;
- `vars:` gives short contextual meanings where the placeholder names are not sufficient.

## Current interpretation

### `conductor: v1`

Marks the script as Conductor-aware and identifies the annotation version.

Additional compact settings may appear on this line when a concrete need exists.

Example:

```python
# conductor: v1; args=argparse
```

The annotation should not accumulate optional fields merely because they might be useful someday.

### `run:`

Each `run:` line declares one normal user-facing calling convention.

```python
# run: python {script} verify {site_id} --lane {lane} :: Verify a website lane.
```

The command before `::` is the invocation template.

The text after `::` describes the user goal or the situation in which the call is useful.

Multiple `run:` lines create multiple operations in Conductor.

Conductor should expose these calls individually rather than presenting the script as one undifferentiated tool.

### `{script}`

Resolves to the script’s repository-relative path using an invocation appropriate for the current environment.

The user does not edit this value.

### Other placeholders

Values such as `{site_id}`, `{lane}`, and `{tail}` become user controls.

Conductor may infer a basic label by converting the placeholder name:

```text
site_id → Site ID
```

The optional `vars:` dictionary provides better contextual text where needed.

### `vars:`

Provides concise meanings for placeholders.

```python
# vars: {"site_id":"Website project ID","lane":"Deployment lane"}
```

This line is optional.

It should not duplicate detailed parser help, choices, defaults, or validation rules that Conductor can obtain elsewhere.

### Argument parser hint

A setting such as:

```python
# conductor: v1; args=argparse
```

indicates that Conductor may inspect the script’s parser.

For each declared call, Conductor may use the parser to discover:

- argument types;
- defaults;
- choices;
- required arguments;
- boolean flags;
- help text;
- options not included in the normal call template.

Arguments written directly in the `run:` line form the normal interface.

Other arguments supported by the relevant parser branch may appear as advanced controls.

## High-level Conductor surface

The Conductor interface should be organized around useful calls, not script files.

A script with three `run:` lines should normally produce three selectable operations.

For example:

```text
Website Docker

Publish a website lane
Verify a website lane
Show recent logs
```

The script path may appear as technical context, but it should not be the primary thing the user selects.

## Main screen

The default Conductor screen should display available operations as a compact catalog.

Each operation should show:

- its purpose;
- the script or tool that provides it;
- the inputs required for the normal call;
- whether advanced options are available;
- whether the operation is currently usable.

An example card might look conceptually like:

```text
Verify a website lane

Check whether a registered website lane is healthy.

Provided by:
tools/local-platform/website-docker.py

Inputs:
Site ID
Lane

Advanced options available

[Open]
```

For the simple Docker diagnostic:

```text
Check whether Docker is installed and responding

No inputs

[Run]
```

The exact visual style can follow the existing MCEL application conventions later. This document defines the information hierarchy, not final layout details.

## Operation screen

Opening an operation should show a generated form based on the declared call.

For:

```python
# run: python {script} verify {site_id} --lane {lane} :: Verify a website lane.
```

the normal form could contain:

```text
Verify a website lane

Site ID
[________________]

Lane
[local ▼]

[Advanced options]

[Run]
```

The user should not be shown a general command editor.

Conductor may display the resolved command in a technical preview, but the command should be generated from the selected operation and the supplied values.

Example:

```text
Command preview

python tools/local-platform/website-docker.py verify hub-site --lane local
```

This preview is informational rather than editable.

## Normal and advanced controls

The interface should distinguish between the intended everyday call and the script’s deeper configuration.

### Normal controls

Normal controls come from placeholders and flags explicitly present in the selected `run:` line.

They should be visible immediately.

For example:

```python
# run: python {script} publish {site_id} --lane {lane} :: Publish a website lane.
```

produces normal controls for:

- `site_id`;
- `lane`.

### Advanced controls

Advanced controls come from the declared argument parser but are not part of the normal calling convention.

For example, the parser may also support:

- `--repo-root`;
- `--compose-scope`;
- `--no-verify`.

Those options may appear under a collapsed Advanced section.

```text
Advanced options

Repository root
[default]

Compose scope
[default]

Skip verification
[ ]
```

The purpose of the split is not to hide real script capabilities. It is to keep the normal operation focused while preserving access to legitimate parser-defined options.

Conductor should not expose arbitrary additional arguments outside the selected parser.

## Running an operation

When the user runs an operation, Conductor should:

1. Resolve the selected `run:` template.
2. Substitute `{script}`.
3. Validate and substitute user placeholders.
4. Add selected advanced parser options.
5. Display or record the resolved invocation.
6. Execute the script through the existing Conductor runner.
7. Capture the exit code, stdout, and stderr.
8. Present the result.

The first version does not require a new result protocol.

Existing script behavior may remain authoritative:

- exit code `0` indicates successful execution;
- a nonzero exit code indicates failure;
- stdout contains normal output;
- stderr contains diagnostic output.

Structured result support may be added later when real operations demonstrate that exit codes and text are insufficient.

## Result view

The result view should prioritize what happened rather than raw process details.

A simple success result might appear as:

```text
Completed

Docker is installed and responding.

Exit code: 0
```

A failure might appear as:

```text
Failed

Docker is installed but is not responding.

Exit code: 1

[Show technical output]
```

Raw stdout, stderr, command details, and timing should remain available in a technical section.

Conductor should not claim that an outcome was independently verified unless the script itself actually performs that verification.

For the initial version, “completed” means the declared call returned successfully.

## Script grouping

Multiple calls from the same script should remain visibly related.

For example:

```text
Website Docker

- Register a website
- Start a website lane
- Stop a website lane
- Publish a website lane
- Verify a website lane
- Show recent logs
```

Conductor may group these operations by script, directory, or inferred tool name.

The grouping approach should remain flexible until the operation catalog contains enough real examples to reveal the most useful organization.

## Scripts without annotations

Scripts without a Conductor annotation may continue to appear in the existing discovered-script catalog.

They should not be treated as fully Conductor-aware.

A useful distinction is:

```text
Conductor-aware
The script declares supported user-facing calls.

Discovered
The repository scan found an executable file, but no supported calls were declared.
```

The older discovered catalog can remain useful for maintainers and backfill work.

The main user surface should increasingly favor Conductor-aware operations.

## Invalid or incomplete annotations

A malformed annotation should not make the entire Conductor application fail.

Conductor should:

- report which script could not be parsed;
- explain the local annotation error;
- omit invalid calls from the normal operation surface;
- retain the script in a diagnostic or discovered view.

Examples of invalid states include:

- a `run:` line without a command;
- a missing `{script}` placeholder where one is required;
- invalid JSON in `vars:`;
- a requested parser type that Conductor does not support.

The parser should remain forgiving about omitted optional information and strict about ambiguous execution templates.

## Initial test cases

### Simple case

```text
tools/local-platform/diagnose-docker.py
```

Proposed annotation:

```python
# conductor: v1
# run: python {script} :: Check whether Docker is installed and responding.
```

This tests:

- one script;
- one operation;
- no user inputs;
- no parser inspection;
- a fixed invocation;
- normal exit-code and text handling.

Expected surface:

```text
Check whether Docker is installed and responding

No inputs

[Run]
```

### Complex case

```text
tools/local-platform/website-docker.py
```

Proposed annotation:

```python
# conductor: v1; args=argparse
# run: python {script} install {site_id} :: Register a website for local Docker use.
# run: python {script} start {site_id} --lane {lane} :: Start a website lane.
# run: python {script} stop {site_id} --lane {lane} :: Stop a website lane.
# run: python {script} publish {site_id} --lane {lane} :: Publish and verify a website lane.
# run: python {script} verify {site_id} --lane {lane} :: Check whether a website lane is healthy.
# run: python {script} logs {site_id} --lane {lane} --tail {tail} :: Show recent website lane logs.
# vars: {"site_id":"Website project ID","lane":"Deployment lane","tail":"Number of recent log lines"}
```

This tests:

- multiple operations from one script;
- positional placeholders;
- option placeholders;
- shared variables;
- different parser branches;
- normal versus advanced controls;
- selective presentation of a larger CLI.

## New scripts

New script and tool templates should eventually include a small commented example:

```python
# conductor: v1
# run: python {script} :: Describe the useful call.
```

This should happen only after the parser and initial surface prove useful.

The marker should not be mandatory for every script. Some scripts are internal helpers and do not represent meaningful user operations.

New scripts should either:

- declare useful Conductor calls; or
- remain ordinary scripts with no annotation.

## Backfill direction

Existing scripts should not receive automatically invented calling conventions.

A backfill process may identify likely candidates and suggest annotations, but a maintainer should confirm that each `run:` line represents a meaningful and supported user call.

The first backfill should concentrate on scripts that are:

- directly invoked by people;
- already documented;
- non-interactive;
- reasonably bounded;
- able to report success through an exit code.

Broad backfill should wait until the simple and complex test cases reveal whether the annotation format is sufficient.

## Relationship to MCEL

This document is intentionally pre-MCEL.

It does not yet define:

- formal Conductor requirements;
- semantic source bindings;
- acceptance evidence;
- operation safety contracts;
- generated MCEL surfaces;
- stable operation identifiers.

Later, MCEL may describe the user capability:

```text
The user can verify whether a website lane is healthy.
```

The Conductor hook can identify the script call that provides that capability:

```text
python website-docker.py verify {site_id} --lane {lane}
```

That relationship should be formalized only after the script hook and the Conductor surface have been exercised with real operations.

## Deferred questions

The initial experiment should not prematurely settle:

- risk and confirmation metadata;
- secret inputs;
- scheduling;
- remote execution;
- environment selection;
- structured result schemas;
- streaming output;
- cancellation behavior;
- sidecar annotations;
- parser support beyond the first needed implementations;
- formal MCEL operation identities;
- automatic safety analysis.

These concerns are real, but they should enter the design when an actual selected operation requires them.

## Initial success condition

The experiment succeeds when Conductor can use the two annotated scripts to provide:

1. One simple no-input operation.
2. Several operations from one complex script.
3. Generated controls for declared placeholders.
4. Advanced controls derived from `argparse`.
5. Fixed, non-editable command construction.
6. Understandable execution results.
7. Useful errors for malformed annotations.

At that point, the annotation and UI behavior can be evaluated before being promoted into formal MCEL requirements or broader repository conventions.
