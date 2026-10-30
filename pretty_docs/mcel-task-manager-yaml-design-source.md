# Task Manager descriptive design and historical Frob contract

The Task Manager design-intent YAML (`tools/fixtures/mcel/task-manager/design.yaml`) and
its independent experimental acceptance contract
(`tools/fixtures/mcel/task-manager/frob_crosscheck.yaml`) are preserved together.
They originate from the former root-level `mcel_designs/task-manager/` directory.

**Status:** descriptive design / research fixture, **not** a runnable MCEL application,
not runtime authority, and not a production activation. Their sources of truth for
current behavior remain `main_computer/task_manager.py`, the existing Task Manager
HTML/JS, and the MCEL semantic catalog/presentation metadata. The YAML intentionally
separates existing, experimental, proposed, and incomplete features. Its Frob
contract has explicit NOT_IMPLEMENTED and UNVERIFIED cases; no unsupported runtime
capability is certified by relocating the files.

`mcel_apps/` is reserved for complete application packages containing a valid
`mcel.app.json`, requirements, blueprint, contracts, and acceptance tests. Putting
only these two YAML files in `mcel_apps/task-manager/` would make the package catalog
invalid, so do not do that merely to reorganize the files.

The normal repository export already includes the `tools/` directory, so the new
fixture files are exported automatically. If a future full Task Manager MCEL package
is authored, it may reference or migrate appropriate requirements from this design
without treating the historic Frob YAML as package authority.

After applying the patch and verifying tests, the root-level `mcel_designs/` can be
removed manually. The old files are intentionally not deleted by this patch.

Useful checks:

```powershell
python -m pytest -q tests/test_mcel_layout_frob_crosscheck.py tests/test_mcel_application_packages.py
python tools/mcel_layout_frob_crosscheck.py --mode plan
```
