from __future__ import annotations

import hashlib
import json
import shutil
import sys
import zipfile
from pathlib import Path

import pytest

import main_computer.mcel_project_edit_transaction as transaction


ROOT = Path(__file__).resolve().parents[1]


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _make_repo(tmp_path: Path) -> tuple[Path, Path]:
    repo = tmp_path / "main_computer_test"
    project = repo / "apps" / "demo"
    project.mkdir(parents=True)
    (project / "index.html").write_text("<h1>Before</h1>\n", encoding="utf-8")
    (project / "app.js").write_text("console.log('before');\n", encoding="utf-8")
    (project / "untouched.txt").write_text("stable\n", encoding="utf-8")
    shutil.copy2(ROOT / "new_patch.py", repo / "new_patch.py")
    return repo, project


def _two_file_changes() -> list[dict[str, object]]:
    return [
        {
            "operation": "modify",
            "path": "index.html",
            "expected_before_sha256": _sha("<h1>Before</h1>\n"),
            "replacement_text": "<h1>After</h1>\n",
        },
        {
            "operation": "create",
            "path": "styles/app.css",
            "replacement_text": "h1 { font-weight: 700; }\n",
        },
    ]


def test_prepare_stages_validates_packages_and_dry_runs_multi_file_overlay(tmp_path: Path) -> None:
    repo, project = _make_repo(tmp_path)
    output = tmp_path / "output"

    report = transaction.prepare_project_edit_transaction(
        repo_root=repo,
        project_root="apps/demo",
        changes=_two_file_changes(),
        output_dir=output,
        validations=[
            {
                "argv": [
                    sys.executable,
                    "-c",
                    (
                        "from pathlib import Path; "
                        "assert Path('index.html').read_text() == '<h1>After</h1>\\n'; "
                        "assert Path('styles/app.css').is_file()"
                    ),
                ],
                "cwd": ".",
            }
        ],
    )

    assert report["ok"] is True
    assert report["state"] == "prepared"
    assert report["dry_run"]["ok"] is True
    assert report["live_write"] is False
    assert [item["operation"] for item in report["changes"]] == ["modify", "create"]
    assert (project / "index.html").read_text(encoding="utf-8") == "<h1>Before</h1>\n"
    assert not (project / "styles" / "app.css").exists()

    artifact = Path(report["artifact"]["path"])
    assert artifact.is_file()
    with zipfile.ZipFile(artifact) as archive:
        assert sorted(archive.namelist()) == [
            "main_computer_test/apps/demo/index.html",
            "main_computer_test/apps/demo/styles/app.css",
        ]
        assert archive.read("main_computer_test/apps/demo/index.html") == b"<h1>After</h1>\n"
    stored = json.loads((output / "project_edit_transaction.json").read_text(encoding="utf-8"))
    assert stored["transaction_id"] == report["transaction_id"]


def test_prepare_rejects_source_hash_drift_before_staging(tmp_path: Path) -> None:
    repo, _ = _make_repo(tmp_path)

    with pytest.raises(transaction.ProjectEditTransactionError) as caught:
        transaction.prepare_project_edit_transaction(
            repo_root=repo,
            project_root="apps/demo",
            changes=[
                {
                    "operation": "modify",
                    "path": "index.html",
                    "expected_before_sha256": "0" * 64,
                    "replacement_text": "<h1>After</h1>\n",
                }
            ],
            output_dir=tmp_path / "output",
        )

    assert caught.value.stage == "source_verification"
    assert not (tmp_path / "output" / "mcel-project-edit-overlay.zip").exists()


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (
            {
                "operation": "modify",
                "path": "../outside.txt",
                "expected_before_sha256": "0" * 64,
                "replacement_text": "x\n",
            },
            "Parent traversal",
        ),
        (
            {
                "operation": "delete",
                "path": "index.html",
                "expected_before_sha256": _sha("<h1>Before</h1>\n"),
                "replacement_text": "",
            },
            "supports modify and create only",
        ),
        (
            {
                "operation": "create",
                "path": "C:\\escape.txt",
                "replacement_text": "x\n",
            },
            "Windows drive",
        ),
    ],
)
def test_prepare_rejects_unsafe_or_unsupported_changes(
    tmp_path: Path,
    change: dict[str, object],
    message: str,
) -> None:
    repo, _ = _make_repo(tmp_path)
    with pytest.raises(transaction.ProjectEditTransactionError, match=message):
        transaction.prepare_project_edit_transaction(
            repo_root=repo,
            project_root="apps/demo",
            changes=[change],
            output_dir=tmp_path / "output",
        )


def test_validation_failure_blocks_artifact_packaging(tmp_path: Path) -> None:
    repo, _ = _make_repo(tmp_path)
    output = tmp_path / "output"

    with pytest.raises(transaction.ProjectEditTransactionError) as caught:
        transaction.prepare_project_edit_transaction(
            repo_root=repo,
            project_root="apps/demo",
            changes=_two_file_changes(),
            output_dir=output,
            validations=[
                {
                    "argv": [sys.executable, "-c", "raise SystemExit(7)"],
                    "cwd": ".",
                }
            ],
        )

    assert caught.value.stage == "validation"
    assert caught.value.details["validations"][0]["returncode"] == 7
    assert not (output / "mcel-project-edit-overlay.zip").exists()
    assert not (output / "project_edit_transaction.json").exists()


def test_reviewed_apply_rechecks_and_writes_complete_set_with_receipt(tmp_path: Path) -> None:
    repo, project = _make_repo(tmp_path)
    output = tmp_path / "output"
    report = transaction.prepare_project_edit_transaction(
        repo_root=repo,
        project_root="apps/demo",
        changes=_two_file_changes(),
        output_dir=output,
    )

    receipt = transaction.apply_project_edit_transaction(
        repo_root=repo,
        transaction=Path(report["report_path"]),
        reviewed=True,
    )

    assert receipt["ok"] is True
    assert receipt["transaction_id"] == report["transaction_id"]
    assert receipt["crash_atomicity"] is False
    assert (project / "index.html").read_text(encoding="utf-8") == "<h1>After</h1>\n"
    assert (project / "styles" / "app.css").read_text(encoding="utf-8") == "h1 { font-weight: 700; }\n"
    assert [item["operation"] for item in receipt["files"]] == ["modify", "create"]
    assert Path(receipt["receipt_path"]).is_file()


def test_apply_blocks_touched_file_drift_without_partial_writes(tmp_path: Path) -> None:
    repo, project = _make_repo(tmp_path)
    report = transaction.prepare_project_edit_transaction(
        repo_root=repo,
        project_root="apps/demo",
        changes=_two_file_changes(),
        output_dir=tmp_path / "output",
    )
    (project / "index.html").write_text("<h1>Drifted</h1>\n", encoding="utf-8")

    with pytest.raises(transaction.ProjectEditTransactionError) as caught:
        transaction.apply_project_edit_transaction(
            repo_root=repo,
            transaction=report,
            reviewed=True,
        )

    assert caught.value.stage == "apply_preflight"
    assert (project / "index.html").read_text(encoding="utf-8") == "<h1>Drifted</h1>\n"
    assert not (project / "styles" / "app.css").exists()


def test_apply_rolls_back_prior_file_when_later_write_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, project = _make_repo(tmp_path)
    report = transaction.prepare_project_edit_transaction(
        repo_root=repo,
        project_root="apps/demo",
        changes=[
            {
                "operation": "modify",
                "path": "app.js",
                "expected_before_sha256": _sha("console.log('before');\n"),
                "replacement_text": "console.log('after');\n",
            },
            {
                "operation": "modify",
                "path": "index.html",
                "expected_before_sha256": _sha("<h1>Before</h1>\n"),
                "replacement_text": "<h1>After</h1>\n",
            },
        ],
        output_dir=tmp_path / "output",
    )

    real_write = transaction._atomic_write_bytes

    def fail_second(path: Path, payload: bytes) -> None:
        if path.name == "index.html" and payload == b"<h1>After</h1>\n":
            raise OSError("simulated second write failure")
        real_write(path, payload)

    monkeypatch.setattr(transaction, "_atomic_write_bytes", fail_second)

    with pytest.raises(transaction.ProjectEditTransactionError) as caught:
        transaction.apply_project_edit_transaction(
            repo_root=repo,
            transaction=report,
            reviewed=True,
        )

    assert caught.value.stage == "apply_write"
    assert caught.value.details["rollback_ok"] is True
    assert (project / "app.js").read_text(encoding="utf-8") == "console.log('before');\n"
    assert (project / "index.html").read_text(encoding="utf-8") == "<h1>Before</h1>\n"


def test_apply_rejects_tampered_artifact(tmp_path: Path) -> None:
    repo, project = _make_repo(tmp_path)
    report = transaction.prepare_project_edit_transaction(
        repo_root=repo,
        project_root="apps/demo",
        changes=_two_file_changes(),
        output_dir=tmp_path / "output",
    )
    artifact = Path(report["artifact"]["path"])
    with zipfile.ZipFile(artifact, "a") as archive:
        archive.writestr("main_computer_test/apps/demo/extra.txt", "tampered\n")

    with pytest.raises(transaction.ProjectEditTransactionError, match="artifact hash mismatch"):
        transaction.apply_project_edit_transaction(
            repo_root=repo,
            transaction=report,
            reviewed=True,
        )

    assert (project / "index.html").read_text(encoding="utf-8") == "<h1>Before</h1>\n"
    assert not (project / "styles" / "app.css").exists()


def test_unrelated_project_drift_is_reported_or_can_be_strictly_blocked(tmp_path: Path) -> None:
    repo, project = _make_repo(tmp_path)
    report = transaction.prepare_project_edit_transaction(
        repo_root=repo,
        project_root="apps/demo",
        changes=_two_file_changes(),
        output_dir=tmp_path / "output",
    )
    (project / "untouched.txt").write_text("changed elsewhere\n", encoding="utf-8")

    with pytest.raises(transaction.ProjectEditTransactionError) as caught:
        transaction.apply_project_edit_transaction(
            repo_root=repo,
            transaction=report,
            reviewed=True,
            require_project_manifest=True,
        )
    assert caught.value.stage == "apply_preflight"
    assert caught.value.details["unrelated_drift"][0]["path"] == "untouched.txt"

    receipt = transaction.apply_project_edit_transaction(
        repo_root=repo,
        transaction=report,
        reviewed=True,
        require_project_manifest=False,
    )
    assert receipt["unrelated_project_drift"][0]["path"] == "untouched.txt"



def test_prepare_rejects_output_directory_inside_project(tmp_path: Path) -> None:
    repo, project = _make_repo(tmp_path)
    with pytest.raises(transaction.ProjectEditTransactionError, match="outside the edited project root"):
        transaction.prepare_project_edit_transaction(
            repo_root=repo,
            project_root="apps/demo",
            changes=_two_file_changes(),
            output_dir=project / "transaction-output",
        )

def test_apply_requires_explicit_review_authorization(tmp_path: Path) -> None:
    repo, _ = _make_repo(tmp_path)
    report = transaction.prepare_project_edit_transaction(
        repo_root=repo,
        project_root="apps/demo",
        changes=_two_file_changes(),
        output_dir=tmp_path / "output",
    )

    with pytest.raises(transaction.ProjectEditTransactionError) as caught:
        transaction.apply_project_edit_transaction(
            repo_root=repo,
            transaction=report,
            reviewed=False,
        )
    assert caught.value.stage == "apply_authorization"
