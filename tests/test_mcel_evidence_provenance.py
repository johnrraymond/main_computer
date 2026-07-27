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
    assert first["schema"] == "mcel-repository-provenance-v2"
    assert first["algorithm"] == "sha256-source-path-content-v2"
    assert first["scope"] == "snapshot-source-roots-v2"
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
    untracked = tmp_path / "tests" / "new_test.py"
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
            PurePosixPath("tests/new_test.py"),
        ],
    )

    first = provenance.build_repository_provenance(tmp_path)
    ignored_state.write_bytes(b"changed")
    second = provenance.build_repository_provenance(tmp_path)

    assert first == second
    assert first["scope"] == "git-tracked-and-unignored-source-v2"
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
            "schema": "mcel-repository-provenance-v2",
            "algorithm": "sha256-source-path-content-v2",
            "fingerprint": "abc",
            "fileCount": 3,
            "totalBytes": 12,
            "scope": "snapshot-source-roots-v2",
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
