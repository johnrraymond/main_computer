"""A DSL package needs no application-specific profile to compile its meaning."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from main_computer import mcel_app_compile as compiler
from main_computer.mcel_app_authoring_profiles import (
    AppAuthoringProfileError,
    get_app_authoring_profile,
)

ROOT = Path(__file__).resolve().parents[1]


def test_code_editor_compiles_without_a_registered_authoring_profile() -> None:
    with pytest.raises(AppAuthoringProfileError):
        get_app_authoring_profile("code-editor")

    result = compiler.compile_application(app_id="code-editor", repo_root=ROOT)
    report = result.to_dict()
    assert result.valid and result.status == "pass"
    assert report["sourceAuthority"] == "mcel.dsl.v1"
    assert report["semanticFingerprint"].startswith("sha256:")
    assert report["source"] == "mcel_apps/code-editor/application.js"
    assert report["profileFreeCompilation"] is True
    assert report["projectionProfileRegistered"] is False
    # This patch does not claim generic projection or application proof.
    assert report["applicationProfile"] is None


def test_existing_calculator_profile_is_preserved() -> None:
    result = compiler.compile_application(app_id="calculator", repo_root=ROOT)
    report = result.to_dict()
    assert result.valid
    assert report["profileFreeCompilation"] is False
    assert report["projectionProfileRegistered"] is True
    assert report["semanticFingerprint"].startswith("sha256:")


def test_profile_free_compile_refuses_non_authoritative_source(tmp_path, monkeypatch) -> None:
    package = tmp_path / "mcel_apps" / "unprofiled"
    package.mkdir(parents=True)
    (package / "mcel.app.json").write_text(json.dumps({
        "authoring": {"status": "dsl-shadow", "source": "application.js"}
    }), encoding="utf-8")
    monkeypatch.setattr(compiler, "build_application_package_catalog", lambda repo: SimpleNamespace(
        packages=[SimpleNamespace(
            app_id="unprofiled", manifest="mcel_apps/unprofiled/mcel.app.json",
            package_root="mcel_apps/unprofiled"
        )]
    ))
    with pytest.raises(compiler.AppCompileError, match="not DSL-authoritative"):
        compiler.compile_application(app_id="unprofiled", repo_root=tmp_path)


def test_profile_free_compile_refuses_source_escape(tmp_path, monkeypatch) -> None:
    package = tmp_path / "mcel_apps" / "unprofiled"
    package.mkdir(parents=True)
    (package / "mcel.app.json").write_text(json.dumps({
        "authoring": {"status": "dsl-authoritative", "source": "../../outside.js"}
    }), encoding="utf-8")
    monkeypatch.setattr(compiler, "build_application_package_catalog", lambda repo: SimpleNamespace(
        packages=[SimpleNamespace(
            app_id="unprofiled", manifest="mcel_apps/unprofiled/mcel.app.json",
            package_root="mcel_apps/unprofiled"
        )]
    ))
    with pytest.raises(compiler.AppCompileError, match="escapes its package root"):
        compiler.compile_application(app_id="unprofiled", repo_root=tmp_path)
