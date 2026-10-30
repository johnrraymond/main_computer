"""Regression: SEH and Hub Git guidance must never commit an unrelated staged file.

The SEH operator previously received a command which staged four specific files
but committed 146 already-staged files.  Git's --only mode plus an explicit
pathspec prevents that, even if the index already contains unrelated changes.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tools.seh_control.git_deployment import _git_commit_push_command
from tools.hub_control.common.deployment import _render_git_commit_push_command


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(['git', '-C', str(repo), *args], check=True,
                            capture_output=True, text=True)
    return result.stdout.strip()


@pytest.mark.parametrize('kind,source,render', [
    ('seh', 'main_computer/seh/relay.py',
     lambda path: _git_commit_push_command([path])),
    ('hub', 'main_computer/hub.py',
     lambda path: _render_git_commit_push_command([{'status': 'M', 'path': path}])),
])
@pytest.mark.parametrize('new_file', [False, True])
def test_guidance_commits_only_selected_paths_even_with_unrelated_staged(tmp_path, kind, source, render, new_file):
    repo = tmp_path / 'repo'
    repo.mkdir()
    _git(repo, 'init', '-b', 'main')
    _git(repo, 'config', 'user.name', 'Lab')
    _git(repo, 'config', 'user.email', 'lab@example.test')
    deploy_source = repo / source
    deploy_source.parent.mkdir(parents=True)
    if not new_file:
        deploy_source.write_text('before\n', encoding='utf-8')
    unrelated = repo / 'unrelated.py'
    unrelated.write_text('original\n', encoding='utf-8')
    _git(repo, 'add', '--', *(['unrelated.py'] if new_file else [source, 'unrelated.py']))
    _git(repo, 'commit', '-m', 'base')

    # This is the operator's original condition: unrelated work was already staged.
    unrelated.write_text('changed and already staged\n', encoding='utf-8')
    _git(repo, 'add', '--', 'unrelated.py')
    deploy_source.write_text('SEH/Hub update\n', encoding='utf-8')

    command = render(source)
    assert command.startswith(f"git add -- '{source}'; ")
    expected_title = 'Update SEH deployment source' if kind == 'seh' else 'Update Hub deployment source'
    assert f"git commit --only -m '{expected_title}' -- '{source}'" in command
    assert 'git commit -m' not in command
    assert 'unrelated.py' not in command

    # Execute the exact Git subcommands the PowerShell text asks for.
    _git(repo, 'add', '--', source)
    _git(repo, 'commit', '--only', '-m', expected_title, '--', source)
    assert _git(repo, 'show', '--pretty=', '--name-only', 'HEAD').splitlines() == [source]
    assert _git(repo, 'diff', '--cached', '--name-only') == 'unrelated.py'
    assert 'changed and already staged' in _git(repo, 'show', ':unrelated.py')
    assert _git(repo, 'show', 'HEAD:unrelated.py') == 'original'
