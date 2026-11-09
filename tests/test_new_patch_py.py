from __future__ import annotations

import json
import subprocess
import sys
import zipfile
from pathlib import Path


def _copy_script(repo: Path) -> Path:
    source = Path(__file__).resolve().parents[1] / "new_patch.py"
    destination = repo / "new_patch.py"
    destination.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    return destination


def _zip_snapshot(zip_path: Path, files: dict[str, str]) -> None:
    with zipfile.ZipFile(zip_path, "w") as archive:
        for relative, content in files.items():
            windows_relative = relative.replace("/", "\\")
            archive.writestr(windows_relative, content)


def _zip_bundle(zip_path: Path, files: dict[str, str], reference_patch: str | None = None) -> None:
    manifest = {
        "format": 1,
        "changes": [
            {"path": path, "operation": "modify", "sha256": None}
            for path in sorted(files)
        ],
    }
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.writestr("manifest.json", json.dumps(manifest, indent=2, sort_keys=True) + "\n")
        if reference_patch is not None:
            archive.writestr("reference.patch", reference_patch)
        for relative, content in files.items():
            archive.writestr(f"files/{relative}", content)


def _reference_patch(path: str, old_text: str, new_text: str) -> str:
    import difflib

    return "".join(
        difflib.unified_diff(
            old_text.splitlines(keepends=True),
            new_text.splitlines(keepends=True),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
            lineterm="\n",
        )
    )


def test_new_patch_dry_run_shows_actual_diff_and_builds_undo_from_snapshot_zip(tmp_path: Path) -> None:
    repo = tmp_path / "main_computer_test"
    repo.mkdir()
    (repo / "tools" / "patching" / "reports").mkdir(parents=True)
    (repo / "TODO.md").write_text("# TODO\n\n- old item\n", encoding="utf-8")
    script = _copy_script(repo)

    snapshot_zip = tmp_path / "snapshot.zip"
    _zip_snapshot(
        snapshot_zip,
        {
            "TODO.md": "# TODO\n\n- old item\n- new item\n",
            "README.md": "# Readme\n",
        },
    )

    result = subprocess.run(
        [sys.executable, str(script), str(snapshot_zip), "--dry-run"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
    assert "verification: no_reference" in result.stdout
    assert "actual diff:" in result.stdout
    assert "+- new item" in result.stdout
    assert "undo command:" in result.stdout
    assert "dry-run only" in result.stdout
    assert (repo / "TODO.md").read_text(encoding="utf-8") == "# TODO\n\n- old item\n"

    runs = sorted((repo / "tools" / "patching" / "reports" / "new_patch_runs").glob("*"))
    assert runs
    latest = runs[-1]
    assert (latest / "actual.patch").exists()
    assert (latest / "undo.patch").exists()
    assert (latest / "undo_bundle.zip").exists()
    assert not (latest / "bundle" / "reference.patch").exists()
    assert (latest / "bundle" / "files" / "TODO.md").exists()
    manifest = json.loads((latest / "bundle" / "manifest.json").read_text(encoding="utf-8"))
    assert [item["path"] for item in manifest["changes"]] == ["README.md", "TODO.md"]


def test_new_patch_applies_snapshot_zip_and_undo_bundle_reverts_it(tmp_path: Path) -> None:
    repo = tmp_path / "main_computer_test"
    repo.mkdir()
    (repo / "tools" / "patching" / "reports").mkdir(parents=True)
    original_text = "# TODO\n\n- old item\n"
    applied_text = "# TODO\n\n- old item\n- applied item\n"
    (repo / "TODO.md").write_text(original_text, encoding="utf-8")
    script = _copy_script(repo)

    snapshot_zip = tmp_path / "snapshot.zip"
    _zip_snapshot(snapshot_zip, {"TODO.md": applied_text})

    result = subprocess.run(
        [sys.executable, str(script), str(snapshot_zip)],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
    assert "verification: no_reference" in result.stdout
    assert "undo command:" in result.stdout
    assert (repo / "TODO.md").read_text(encoding="utf-8") == applied_text

    latest = sorted((repo / "tools" / "patching" / "reports" / "new_patch_runs").glob("*"))[-1]
    undo_zip = latest / "undo_bundle.zip"
    undo = subprocess.run(
        [sys.executable, str(script), str(undo_zip)],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    assert undo.returncode == 0, undo.stdout + "\n" + undo.stderr
    assert "verification: exact" in undo.stdout
    assert (repo / "TODO.md").read_text(encoding="utf-8") == original_text


def test_new_patch_blocks_fuzz_only_when_reference_patch_exists(tmp_path: Path) -> None:
    repo = tmp_path / "main_computer_test"
    repo.mkdir()
    (repo / "tools" / "patching" / "reports").mkdir(parents=True)
    (repo / "TODO.md").write_text("# TODO\n\n- old item\n", encoding="utf-8")
    script = _copy_script(repo)

    bundle_without_reference = tmp_path / "bundle_without_reference.zip"
    _zip_bundle(bundle_without_reference, {"TODO.md": "# TODO\n\n- no reference item\n"})

    no_reference = subprocess.run(
        [sys.executable, str(script), str(bundle_without_reference), "--dry-run"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    assert no_reference.returncode == 0, no_reference.stdout + "\n" + no_reference.stderr
    assert "verification: no_reference" in no_reference.stdout
    assert "fuzzy" not in no_reference.stdout

    bundle_zip = tmp_path / "bundle.zip"
    reference_patch = _reference_patch("TODO.md", "# TODO\n\n- old item\n", "# TODO\n\n- new item\n")
    _zip_bundle(bundle_zip, {"TODO.md": "# TODO\n\n- new item\n"}, reference_patch)

    first = subprocess.run(
        [sys.executable, str(script), str(bundle_zip)],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    assert first.returncode == 0, first.stdout + "\n" + first.stderr
    assert "verification: exact" in first.stdout
    assert (repo / "TODO.md").read_text(encoding="utf-8") == "# TODO\n\n- new item\n"

    (repo / "TODO.md").write_text("# TODO\n\n- drifted item\n", encoding="utf-8")
    blocked = subprocess.run(
        [sys.executable, str(script), str(bundle_zip)],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    assert blocked.returncode == 1, blocked.stdout + "\n" + blocked.stderr
    assert "verification: fuzzy" in blocked.stdout
    assert "rerun with --allowfuzz" in blocked.stdout
    assert (repo / "TODO.md").read_text(encoding="utf-8") == "# TODO\n\n- drifted item\n"

    allowed = subprocess.run(
        [sys.executable, str(script), str(bundle_zip), "--allowfuzz"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    assert allowed.returncode == 0, allowed.stdout + "\n" + allowed.stderr
    assert "verification: fuzzy" in allowed.stdout
    assert "overwrote target files" in allowed.stdout
    assert (repo / "TODO.md").read_text(encoding="utf-8") == "# TODO\n\n- new item\n"


def test_single_tests_directory_is_never_stripped(tmp_path: Path) -> None:
    """Regression: preserve tests/, even if a stale root-level file matches."""
    repo = tmp_path / "main_computer_test"
    (repo / "tests").mkdir(parents=True)
    (repo / "tests" / "test_route.py").write_text("old\n", encoding="utf-8")
    (repo / "test_route.py").write_text("new\n", encoding="utf-8")
    script = _copy_script(repo)
    archive = tmp_path / "deep_link_fix.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("tests/test_route.py", "new\n")

    result = subprocess.run([sys.executable, str(script), str(archive)], cwd=repo, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "changed_files: 1" in result.stdout
    assert (repo / "tests" / "test_route.py").read_text(encoding="utf-8") == "new\n"
    latest = sorted((repo / "tools/patching/reports/new_patch_runs").iterdir())[-1]
    manifest = json.loads((latest / "bundle/manifest.json").read_text(encoding="utf-8"))
    assert [c["path"] for c in manifest["changes"]] == ["tests/test_route.py"]


def test_arbitrary_top_level_directory_is_preserved_even_when_wrapper_like(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / "tests").mkdir(parents=True)
    (repo / "tests/test_route.py").write_text("old\n", encoding="utf-8")
    script = _copy_script(repo)
    archive = tmp_path / "wrapped.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("some_wrapper/tests/test_route.py", "new\n")

    result = subprocess.run([sys.executable, str(script), str(archive)], cwd=repo, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (repo / "some_wrapper/tests/test_route.py").read_text(encoding="utf-8") == "new\n"
    assert (repo / "tests/test_route.py").read_text(encoding="utf-8") == "old\n"


def test_main_computer_test_wrapper_rejected_before_copy(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "README.md").write_text("original\n", encoding="utf-8")
    script = _copy_script(repo)
    archive = tmp_path / "patch.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("main_computer_test/README.md", "replacement\n")
        z.writestr("README.md", "replacement\n")

    result = subprocess.run([sys.executable, str(script), str(archive)], cwd=repo, capture_output=True, text=True)
    assert result.returncode != 0
    assert "Forbidden main_computer_test" in result.stderr
    assert (repo / "README.md").read_text(encoding="utf-8") == "original\n"
    assert not (repo / "main_computer_test").exists()


def test_main_computer_test_named_export_rejected_even_without_wrapper(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    script = _copy_script(repo)
    archive = tmp_path / "main_computer_test-20261108.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("tests/test_route.py", "new\n")

    result = subprocess.run([sys.executable, str(script), str(archive)], cwd=repo, capture_output=True, text=True)
    assert result.returncode != 0
    assert "Forbidden main_computer_test" in result.stderr
    assert not (repo / "tests").exists()


def test_wrapped_verified_bundle_is_rejected_not_copied_as_files(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "README.md").write_text("original\n", encoding="utf-8")
    script = _copy_script(repo)
    archive = tmp_path / "bundle.zip"
    manifest = {"format": 1, "changes": [{"path": "README.md", "operation": "modify", "sha256": None}]}
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("bundle/manifest.json", json.dumps(manifest))
        z.writestr("bundle/files/README.md", "replacement\n")

    result = subprocess.run([sys.executable, str(script), str(archive)], cwd=repo, capture_output=True, text=True)
    assert result.returncode != 0
    assert "Wrapped verified bundle rejected" in result.stderr
    assert (repo / "README.md").read_text(encoding="utf-8") == "original\n"
    assert not (repo / "bundle").exists()


def test_no_changes_does_not_claim_copy(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / "tests").mkdir(parents=True)
    (repo / "tests/test_route.py").write_text("same\n", encoding="utf-8")
    script = _copy_script(repo)
    archive = tmp_path / "patch.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("tests/test_route.py", "same\n")

    result = subprocess.run([sys.executable, str(script), str(archive)], cwd=repo, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "changed_files: 0" in result.stdout
    assert "no changes:" in result.stdout
    assert "applied: copied" not in result.stdout
    latest = sorted((repo / "tools/patching/reports/new_patch_runs").iterdir())[-1]
    assert json.loads((latest / "run.json").read_text(encoding="utf-8"))["status"] == "no_changes"
