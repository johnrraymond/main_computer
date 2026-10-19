from __future__ import annotations

from pathlib import Path, PurePosixPath

from main_computer import mcel_evidence_provenance as provenance


def test_snapshot_fingerprint_ignores_mutable_state_but_tracks_source(tmp_path: Path) -> None:
    source = tmp_path / "main_computer" / "app.py"
    source.parent.mkdir()
    source.write_text("print('one')\n", encoding="utf-8")

    website = tmp_path / "runtime" / "websites" / "hub-site" / "index.html"
    website.parent.mkdir(parents=True)
    website.write_text("<h1>source</h1>\n", encoding="utf-8")

    report = tmp_path / "runtime" / "reports" / "flog" / "report.json"
    report.parent.mkdir(parents=True)
    report.write_text('{"generated": 1}\n', encoding="utf-8")

    state = tmp_path / "runtime" / "state" / "session.sqlite"
    state.parent.mkdir(parents=True)
    state.write_bytes(b"state-one")

    browser_state = tmp_path / "main_computer" / ".main_computer_browser_profile" / "History"
    browser_state.parent.mkdir(parents=True)
    browser_state.write_bytes(b"history-one")

    first = provenance.build_repository_provenance(tmp_path)

    report.write_text('{"generated": 2}\n', encoding="utf-8")
    state.write_bytes(b"state-two")
    browser_state.write_bytes(b"history-two")
    second = provenance.build_repository_provenance(tmp_path)

    assert first == second
    assert first["schema"] == "mcel-repository-provenance-v3"
    assert first["algorithm"] == "sha256-source-path-content-v2"
    assert first["scope"] == "snapshot-mcel-authority-v3"
    assert first["selectionMethod"] == "snapshot-source-roots"
    assert "runtime/websites" in first["sourceRoots"]
    assert first["fileCount"] == 2

    source.write_text("print('two')\n", encoding="utf-8")
    third = provenance.build_repository_provenance(tmp_path)
    assert third["fingerprint"] != first["fingerprint"]

    source.write_text("print('one')\n", encoding="utf-8")
    website.write_text("<h1>changed source</h1>\n", encoding="utf-8")
    fourth = provenance.build_repository_provenance(tmp_path)
    assert fourth["fingerprint"] != first["fingerprint"]


def test_git_selection_mode_uses_selected_source_paths(
    tmp_path: Path,
    monkeypatch,
) -> None:
    tracked = tmp_path / "main_computer" / "tracked.py"
    tracked.parent.mkdir()
    tracked.write_text("tracked\n", encoding="utf-8")
    untracked = tmp_path / "tests" / "test_mcel_new_test.py"
    untracked.parent.mkdir()
    untracked.write_text("new\n", encoding="utf-8")
    ignored_state = tmp_path / "runtime" / "state" / "local.db"
    ignored_state.parent.mkdir(parents=True)
    ignored_state.write_bytes(b"ignored")

    monkeypatch.setattr(
        provenance,
        "_git_selected_relative_paths",
        lambda _root: [
            PurePosixPath("main_computer/tracked.py"),
            PurePosixPath("tests/test_mcel_new_test.py"),
        ],
    )

    first = provenance.build_repository_provenance(tmp_path)
    ignored_state.write_bytes(b"changed")
    second = provenance.build_repository_provenance(tmp_path)

    assert first == second
    assert first["scope"] == "git-tracked-and-unignored-mcel-authority-v3"
    assert first["selectionMethod"] == "git-tracked-and-unignored"
    assert first["sourceRoots"] == []
    assert first["fileCount"] == 2

    untracked.write_text("changed source\n", encoding="utf-8")
    third = provenance.build_repository_provenance(tmp_path)
    assert third["fingerprint"] != first["fingerprint"]


def test_repository_binding_distinguishes_exact_mismatch_unbound_and_legacy(
    tmp_path: Path,
) -> None:
    (tmp_path / "source.txt").write_text("current\n", encoding="utf-8")
    current = provenance.build_repository_provenance(tmp_path)

    exact = provenance.compare_repository_provenance(current, current)
    mismatch = provenance.compare_repository_provenance(
        {**current, "fingerprint": "0" * 64},
        current,
    )
    unbound = provenance.compare_repository_provenance(None, current)
    legacy = provenance.compare_repository_provenance(
        {
            **current,
            "schema": "mcel-repository-provenance-v1",
            "algorithm": "sha256-path-content-v1",
        },
        current,
    )

    assert exact["status"] == "exact"
    assert exact["exact"] is True
    assert exact["currentScope"] == current["scope"]
    assert mismatch["status"] == "mismatch"
    assert unbound["status"] == "unbound"
    assert legacy["status"] == "unsupported"


def test_extract_repository_provenance_supports_flog_envelope() -> None:
    evidence = {
        "schema": "mcel-runtime-flog-report-v2",
        "repositoryProvenance": {
            "schema": "mcel-repository-provenance-v3",
            "algorithm": "sha256-source-path-content-v2",
            "fingerprint": "abc",
            "fileCount": 3,
            "totalBytes": 12,
            "scope": "snapshot-mcel-authority-v3",
            "selectionMethod": "snapshot-source-roots",
            "sourceRoots": ["main_computer", "tests"],
        },
    }

    extracted = provenance.extract_repository_provenance(evidence)
    assert extracted is not None
    assert extracted["fingerprint"] == "abc"
    assert extracted["fileCount"] == 3
    assert extracted["selectionMethod"] == "snapshot-source-roots"
    assert extracted["sourceRoots"] == ["main_computer", "tests"]


def test_mcel_authority_scope_ignores_unrelated_mutable_subsystems(tmp_path: Path) -> None:
    """Mother and NanoJev work must not invalidate an MCEL release candidate."""
    main = tmp_path / "main_computer" / "mcel_truth_audit.py"
    main.parent.mkdir(parents=True)
    main.write_text("authority = 1\n", encoding="utf-8")
    app = tmp_path / "mcel_apps" / "calculator" / "application.json"
    app.parent.mkdir(parents=True)
    app.write_text('{"version": 1}', encoding="utf-8")
    mother = tmp_path / "mother_mutate_harness.py"
    mother.write_text("mother = 1\n", encoding="utf-8")
    mother_test = tmp_path / "tests" / "test_mother_final_node_cleanup.py"
    mother_test.parent.mkdir(parents=True)
    mother_test.write_text("def test_mother(): pass\n", encoding="utf-8")
    nanojev = tmp_path / "docs" / "nanojev-training.md"
    nanojev.parent.mkdir(parents=True)
    nanojev.write_text("# training\n", encoding="utf-8")
    start = provenance.build_repository_provenance(tmp_path)
    mother.write_text("mother = 2\n", encoding="utf-8")
    mother_test.write_text("def test_mother(): assert True\n", encoding="utf-8")
    nanojev.write_text("# revised\n", encoding="utf-8")
    (tmp_path / "tests" / "test_mother_new.py").write_text("pass\n", encoding="utf-8")
    assert provenance.build_repository_provenance(tmp_path) == start

    # Both substantive app changes and newly introduced MCEL authority files
    # MUST invalidate previous evidence.
    app.write_text('{"version": 2}', encoding="utf-8")
    assert provenance.build_repository_provenance(tmp_path)["fingerprint"] != start["fingerprint"]
    app.write_text('{"version": 1}', encoding="utf-8")
    assert provenance.build_repository_provenance(tmp_path)["fingerprint"] == start["fingerprint"]
    (tmp_path / "main_computer" / "mcel_new_verifier.py").write_text("x = 1\n", encoding="utf-8")
    assert provenance.build_repository_provenance(tmp_path)["fingerprint"] != start["fingerprint"]


def test_mcel_authority_scope_covers_runtime_host_broker_and_fixtures() -> None:
    included = (
        "main_computer/mcel_truth_audit.py",
        "main_computer/web/applications/scripts/calculator-core.js",
        "mcel_apps/calculator/application.js",
        "deploy/local-platform/site-runtimes/mcel-runtime.js",
        "runtime/websites/home/index.html",
        "game_projects/webgl-demo/game.json",
        "tools/mcel_app_promote.py",
        "tools/patching/reviewed_patch.py",
        "new_patch.py",
        "tests/test_mcel_portable_promotion.py",
        "pretty_docs/mcel-repository-truth-audit.md",
    )
    excluded = (
        "mother_mutate_harness.py",
        "tools/mother/common/deployment_node_remove.py",
        "tests/test_mother_final_node_cleanup.py",
        "docs/nanojev-training.md",
        "README_NANOJEV_QWEN_CONTINUOUS_V1.md",
    )
    for name in included:
        assert provenance.is_mcel_authority_source(PurePosixPath(name)), name
    for name in excluded:
        assert not provenance.is_mcel_authority_source(PurePosixPath(name)), name


def test_version2_provenance_is_refused_after_scope_change(tmp_path: Path) -> None:
    (tmp_path / "main_computer").mkdir()
    (tmp_path / "main_computer" / "mcel.py").write_text("x=1\n", encoding="utf-8")
    current = provenance.build_repository_provenance(tmp_path)
    assert provenance.compare_repository_provenance({**current, "schema":
        "mcel-repository-provenance-v2"}, current)["status"] == "unsupported"


def test_real_git_mcelscope_excludes_mother_but_includes_untracked_mcel(
    tmp_path: Path,
) -> None:
    import shutil
    import subprocess

    if shutil.which("git") is None:
        import pytest
        pytest.skip("Git not installed")
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    app = tmp_path / "main_computer" / "mcel_portable_certification.py"
    app.parent.mkdir(parents=True)
    app.write_text("pass\n", encoding="utf-8")
    unrelated = tmp_path / "mother_mutate_harness.py"
    unrelated.write_text("pass\n", encoding="utf-8")
    test_mother = tmp_path / "tests" / "test_mother_clean.py"
    test_mother.parent.mkdir(parents=True)
    test_mother.write_text("pass\n", encoding="utf-8")
    start = provenance.build_repository_provenance(tmp_path)
    assert start["scope"] == provenance.GIT_SCOPE
    assert start["fileCount"] == 1
    unrelated.write_text("print(2)\n", encoding="utf-8")
    test_mother.write_text("print(3)\n", encoding="utf-8")
    assert provenance.build_repository_provenance(tmp_path)["fingerprint"] == start["fingerprint"]
    new_mcel_test = tmp_path / "tests" / "test_mcel_new.py"
    new_mcel_test.write_text("pass\n", encoding="utf-8")
    assert provenance.build_repository_provenance(tmp_path)["fingerprint"] != start["fingerprint"]
