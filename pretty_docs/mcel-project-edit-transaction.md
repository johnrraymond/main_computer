# MCEL Project Edit Transaction

The MCEL project edit transaction is the repository's shared mutation boundary for an already reviewed set of complete replacement files.

It closes the mechanical gap between:

```text
semantic or human edit plan
    -> complete replacement files
    -> staged project validation
    -> changed-files overlay
    -> new_patch.py --dry-run
    -> explicit reviewed apply
```

It does not choose an edit, infer intent, explore an application, or authorize a model response. It accepts a bounded replacement set after those decisions have already been made.

## Current implementation

The implementation is:

```text
main_computer/mcel_project_edit_transaction.py
```

Transaction format:

```text
mcel-project-edit-transaction-v1
```

Apply receipt format:

```text
mcel-project-edit-apply-receipt-v1
```

Version one supports:

- multiple files in one transaction;
- complete UTF-8 replacement files;
- `modify` with an exact required before hash;
- `create` only when the destination does not exist;
- a bounded repository-relative project root;
- isolated project staging;
- validation commands executed without a shell;
- direct `main_computer_test/` changed-files overlay packaging;
- `new_patch.py --dry-run`;
- artifact hash and member verification;
- explicit `reviewed=True` apply authorization;
- touched-file drift checks immediately before apply;
- optional strict rejection of unrelated project drift;
- per-file atomic replacement;
- best-effort rollback when an ordinary write fails;
- machine-readable preparation and apply receipts.

Version one deliberately does not support:

- delete;
- rename;
- binary replacement files;
- symlink-backed project trees or transaction paths;
- autonomous edit selection;
- direct MCEL Lab repair application;
- semantic-node-to-source ownership or autonomous edit planning;
- arbitrary browser-supplied validation commands;
- filesystem-level crash atomicity.

Deletion and rename remain unsupported because the normal changed-files overlay cannot express them. Process or machine failure can interrupt a multi-file apply even though each individual replacement uses an atomic temporary-file rename. The receipt therefore reports `crash_atomicity: false`.

## Python API

Prepare a transaction without changing the live repository:

```python
from pathlib import Path

from main_computer.mcel_project_edit_transaction import (
    prepare_project_edit_transaction,
    sha256_file,
)

repo = Path("C:/path/to/main_computer_test")
project = repo / "main_computer" / "web" / "applications"

report = prepare_project_edit_transaction(
    repo_root=repo,
    project_root="main_computer/web/applications",
    changes=[
        {
            "operation": "modify",
            "path": "apps/example.html",
            "expected_before_sha256": sha256_file(project / "apps" / "example.html"),
            "replacement_file": Path("reviewed/example.html"),
        },
        {
            "operation": "create",
            "path": "scripts/example-controller.js",
            "replacement_file": Path("reviewed/example-controller.js"),
        },
    ],
    validations=[
        {
            "argv": [
                "python",
                "-m",
                "pytest",
                "-q",
                "tests/test_example_app.py",
            ],
            "cwd": ".",
            "timeout_seconds": 120,
        }
    ],
    output_dir=Path("C:/outside-the-project/mcel-edit-output"),
)
```

The output directory receives:

```text
mcel-project-edit-overlay.zip
project_edit_transaction.json
```

The overlay contains only the complete created or modified files:

```text
mcel-project-edit-overlay.zip
└── main_computer_test/
    └── <project files at repository-relative paths>
```

Apply only after review:

```python
from main_computer.mcel_project_edit_transaction import (
    apply_project_edit_transaction,
)

receipt = apply_project_edit_transaction(
    repo_root=repo,
    transaction=Path(report["report_path"]),
    reviewed=True,
)
```

The apply writes:

```text
project_edit_apply_receipt.json
```

## Code Editor HTTP bridge

Code Editor now exposes the transaction through four local application endpoints:

```text
POST /api/applications/editor/project/manifest
POST /api/applications/editor/project/file/save
POST /api/applications/editor/project/transaction/prepare
POST /api/applications/editor/project/transaction/apply
```

The browser bridge provides `MainComputerCodeStudio.inspectWorkspace`, `saveFile`,
`prepareProjectEditTransaction`, and `applyReviewedPatch`.

The endpoint boundary adds several restrictions beyond the Python API:

- transaction reports are stored outside the edited project;
- apply accepts only opaque transaction handles issued by the local server;
- callers cannot submit filesystem paths to transaction reports or artifacts;
- explicit file save requires source freshness evidence and an author-owned-source write policy;
- reviewed apply requires `reviewed=true` plus `approved=true` or `confirmed=true`;
- browser-provided validation command arrays are rejected;
- the only exposed validation profiles are `none` and the server-defined `python-compileall`.

`project/manifest` returns the same file hashes and project-manifest hash used by the
transaction service. `project/file/save` prepares, dry-runs, and immediately applies one
hash-guarded modification. The two transaction endpoints preserve the separate prepare and
reviewed-apply phases for multi-file `modify` and `create` sets.

This bridge makes the mutation methods callable. It does not supply a visual review UI,
derive source ownership from MCEL semantic nodes, or generate a multi-file edit plan.

## Authority checks

Preparation fails unless all of the following hold:

1. The repository and project roots exist.
2. The output directory is outside the edited project root.
3. Every path is relative, normalized, unique, and contained by the project.
4. No project or touched path uses a symlink.
5. Every modified file exists and matches its declared before hash.
6. Every created file is absent.
7. Every replacement is complete UTF-8 text and is not a no-op.
8. Every staged validation command succeeds.
9. The overlay contains exactly the declared replacement files.
10. `new_patch.py --dry-run` accepts the overlay.

Apply repeats the artifact, path, payload, and live before-hash checks. A successful preparation report is not permission to ignore later source drift.

## Project drift policy

The transaction records a source project manifest. At apply time:

- drift in a touched file always blocks;
- a create destination that appeared always blocks;
- unrelated project drift is reported;
- `require_project_manifest=True` converts unrelated drift into a blocking error.

The default permits unrelated changes because an exact touched-file guard is often safer and less disruptive than requiring the entire application tree to remain frozen.

## Validation commands

Validation commands are supplied as argument arrays and run with `shell=False` inside the isolated staged project. The environment includes:

```text
MCEL_PROJECT_EDIT_STAGED_ROOT
```

The caller remains responsible for selecting bounded and trustworthy commands. The transaction proves that the commands returned success; it does not convert a narrow test into full repository proof.

## Relationship to existing MCEL components

The transaction reuses the replacement-file and `new_patch.py` safety model already proven by Website Builder, but it is project-neutral and supports multiple files.

It is now connected to `MainComputerCodeStudio.saveFile` and
`MainComputerCodeStudio.applyReviewedPatch` through the guarded local HTTP bridge described
above.

It is not yet connected to:

- a complete visual transaction review surface;
- MCEL Lab annotation export;
- semantic-node-to-source ownership;
- model-generated multi-file planning.

The callable bridge removes the missing mutation binding, but repository-bound runtime and
acceptance evidence are still required before Code Editor can be claimed as operationally
proven for full-application editing.
