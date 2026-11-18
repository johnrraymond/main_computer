"""The launcher oracle must not depend on a fixed number of applications."""
from pathlib import Path
import re
import shutil

import pytest

from main_computer import mcel_applications_shell_host as host
from main_computer.mcel_phase2c_candidate_host import CandidateHostError


@pytest.fixture
def oracle_repo(tmp_path: Path) -> Path:
    for relative in (host.LEGACY_HTML, host.LEGACY_NAVIGATION, host.LEGACY_GLYPHS, host.LEGACY_BASE):
        destination = tmp_path / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(host.ROOT / relative, destination)
    return tmp_path


def _first_card(html: str) -> tuple[str, str]:
    match = re.search(r'<a class="app-card[^"]*" href="[^"]+" data-app="([^"]+)"[^>]*>.*?</a>', html, re.S)
    assert match, "expected at least one launcher card"
    return match.group(0), match.group(1)


def test_launcher_count_is_not_hardcoded(oracle_repo: Path):
    path = oracle_repo / host.LEGACY_HTML
    original = path.read_text(encoding="utf-8")
    card, slug = _first_card(original)
    baseline_count = len(host.legacy_desktop_reference(oracle_repo)["launcher"])
    extra = card.replace('data-app="' + slug + '"', 'data-app="mcel-fixture-extra"', 1)
    extra = re.sub(r'href="[^"]+"', 'href="/applications/mcel-fixture-extra"', extra, count=1)
    variants = ((baseline_count - 1, original.replace(card, "", 1)),
                (baseline_count, original),
                (baseline_count + 1, original.replace(card, card + "\n" + extra, 1)))
    for count, html in variants:
        path.write_text(html, encoding="utf-8")
        observed = host.legacy_desktop_reference(oracle_repo)["launcher"]
        assert len(observed) == count
        assert len({entry["id"] for entry in observed}) == count


def test_duplicate_launcher_id_is_still_rejected(oracle_repo: Path):
    path = oracle_repo / host.LEGACY_HTML
    original = path.read_text(encoding="utf-8")
    card, _ = _first_card(original)
    path.write_text(original.replace(card, card * 2, 1), encoding="utf-8")
    with pytest.raises(CandidateHostError, match="duplicate IDs"):
        host.legacy_desktop_reference(oracle_repo)
