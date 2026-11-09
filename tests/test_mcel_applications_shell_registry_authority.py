"""Portable source-oracle identity and single-authority launcher regression.

The reference HTML is the sole source of launcher display text and ordering;
registry.json only declares app identity, canonical route and mount kind.
"""
from copy import deepcopy
from pathlib import Path, PureWindowsPath
from unittest.mock import patch

import pytest

from main_computer import mcel_applications_shell_host as host
from main_computer.mcel_phase2c_candidate_host import CandidateHostError
from tools.mcel_applications_shell_oracle import PINNED_SOURCE_SHA256


def test_hash_source_paths_stay_portable_on_windows_and_posix():
    observed = host.legacy_desktop_reference()["source_sha256"]
    assert observed == PINNED_SOURCE_SHA256
    assert len(observed) == 4
    for name in PINNED_SOURCE_SHA256:
        assert host._portable_repo_key(Path(name)) == name
        assert host._portable_repo_key(PureWindowsPath(name)) == name
        assert host._portable_repo_key(name.replace("/", "\\")) == name


def test_legacy_launcher_owns_display_labels_and_order_not_registry_copy():
    original = host._read_registry(host.ROOT)
    reference = host.legacy_desktop_reference()["launcher"]
    assert len(original["apps"]) == len(reference) == 21
    # A registry's stale/non-authoritative display labels must not fail the
    # shell or overwrite legacy launcher labels, but all routes still match.
    changed = deepcopy(original)
    for entry in changed["apps"]:
        entry["title"] = "STALE REGISTRY TITLE"
        entry["description"] = "STALE REGISTRY DESCRIPTION"
    with patch.object(host, "_read_registry", return_value=changed):
        _, synchronized = host.compile_applications_shell()
    assert [x["id"] for x in synchronized["apps"]] == [x["id"] for x in reference]
    assert [(x["title"], x["description"]) for x in synchronized["apps"]] == [
        (x["title"], x["description"]) for x in reference
    ]
    assert all(x["title"] != "STALE REGISTRY TITLE" for x in synchronized["apps"])


def test_registry_cannot_redirect_a_canonical_application_route():
    changed = deepcopy(host._read_registry(host.ROOT))
    changed["apps"][0]["route"] = "/applications/other-app"
    with patch.object(host, "_read_registry", return_value=changed):
        with pytest.raises(CandidateHostError, match="route mismatch"):
            host.compile_applications_shell()
