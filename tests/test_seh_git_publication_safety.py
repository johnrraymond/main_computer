"""Real Git tests: publish only the exact SEH build surface, never a guessed HEAD."""
from pathlib import Path
import subprocess

import pytest

from tools.seh_control import git_deployment as seh


def _run(repo, *args):
    proc = subprocess.run(['git', '-C', str(repo), *args], text=True,
                          capture_output=True, check=True)
    return proc.stdout.strip()


@pytest.fixture
def publication_repo(tmp_path, monkeypatch):
    repo = tmp_path / 'checkout'
    repo.mkdir()
    remote = tmp_path / 'origin.git'
    _run(repo, 'init', '-b', 'main')
    _run(repo, 'config', 'user.name', 'Tests')
    _run(repo, 'config', 'user.email', 'unit@test.invalid')
    for name in seh.SOURCES:
        file = repo / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text('source version 1\n')
    _run(repo, 'add', '--', *seh.SOURCES)
    _run(repo, 'commit', '-m', 'SEH baseline')
    subprocess.run(['git', 'init', '--bare', str(remote)], check=True,
                   capture_output=True)
    _run(repo, 'remote', 'add', 'origin', str(remote))
    _run(repo, 'push', 'origin', 'main')
    base = _run(repo, 'rev-parse', 'HEAD')
    # Use a real local bare Git origin for commit ancestry and source diffs,
    # changing ONLY the returned repository origin URL for the policy check.
    original = seh.subprocess.run

    def guarded(argv, **kwargs):
        if argv[3:] == ['remote', 'get-url', 'origin']:
            return subprocess.CompletedProcess(argv, 0,
                'https://github.com/johnrraymond/main_computer.git\n', '')
        return original(argv, **kwargs)

    monkeypatch.setattr(seh.subprocess, 'run', guarded)
    return repo, remote, base


def test_unrelated_local_commits_do_not_require_seh_push(publication_repo):
    repo, _, original = publication_repo
    (repo / 'README.md').write_text('unrelated work\n')
    _run(repo, 'add', '--', 'README.md')
    _run(repo, 'commit', '-m', 'unrelated work')
    assert _run(repo, 'rev-parse', 'HEAD') != original
    source = seh.git_source(repo)
    assert source['revision'] == original  # already published SEH inputs


def test_ahead_with_only_seh_change_has_explicit_verified_target(publication_repo):
    repo, _, _ = publication_repo
    target = repo / 'main_computer/seh/relay.py'
    target.write_text('source version 2\n')
    _run(repo, 'add', '--', 'main_computer/seh/relay.py')
    _run(repo, 'commit', '-m', 'SEH update')
    with pytest.raises(seh.SEHGitPublicationError, match='Fast-forward') as err:
        seh.git_source(repo)
    assert err.value.git_command == 'git push origin HEAD:refs/heads/main'


def test_ahead_with_unrelated_committed_files_emits_isolated_source_only_command(publication_repo):
    repo, _, _ = publication_repo
    (repo / 'main_computer/seh/relay.py').write_text('source version 2\n')
    (repo / 'README.md').write_text('do not publish this\n')
    _run(repo, 'add', '--', 'main_computer/seh/relay.py', 'README.md')
    _run(repo, 'commit', '-m', 'mixed changes')
    with pytest.raises(seh.SEHGitPublicationError, match='isolated') as err:
        seh.git_source(repo)
    command = err.value.git_command
    assert 'git -C $root worktree add --detach' in command
    assert "git -C $temp push origin ('HEAD:' + $ref)" in command
    assert "'main_computer/seh/relay.py'" in command
    assert "'README.md'" not in command
    assert 'git push origin HEAD:refs/heads/main' not in command


def test_stream_writer_is_a_required_relay_source(publication_repo):
    repo, _, _ = publication_repo
    target = repo / 'main_computer/seh/stream_writer.py'
    target.write_text('modified stream writer\n')
    with pytest.raises(seh.SEHGitPublicationError, match='uncommitted') as err:
        seh.git_source(repo)
    assert "'main_computer/seh/stream_writer.py'" in err.value.git_command
    assert 'git commit --only' in err.value.git_command
    assert 'HEAD:refs/heads/main' in err.value.git_command


def test_behind_branch_with_identical_seh_source_needs_no_push(publication_repo):
    repo, remote, _ = publication_repo
    other = repo.parent / 'other'
    subprocess.run(['git', 'clone', '-b', 'main', str(remote), str(other)],
                   check=True, capture_output=True)
    _run(other, 'config', 'user.name', 'Tests')
    _run(other, 'config', 'user.email', 'unit@test.invalid')
    (other / 'README.md').write_text('new upstream\n')
    _run(other, 'add', '--', 'README.md')
    _run(other, 'commit', '-m', 'new upstream')
    _run(other, 'push', 'origin', 'main')
    _run(repo, 'fetch', 'origin', 'main')
    remote_tip = _run(repo, 'ls-remote', 'origin', 'refs/heads/main').split()[0]
    assert seh.git_source(repo)['revision'] == remote_tip


def test_diverged_branch_emits_isolated_source_only_command(publication_repo):
    repo, remote, _ = publication_repo
    other = repo.parent / 'other'
    subprocess.run(['git', 'clone', '-b', 'main', str(remote), str(other)],
                   check=True, capture_output=True)
    _run(other, 'config', 'user.name', 'Tests')
    _run(other, 'config', 'user.email', 'unit@test.invalid')
    (other / 'README.md').write_text('upstream\n')
    _run(other, 'add', '--', 'README.md')
    _run(other, 'commit', '-m', 'upstream')
    _run(other, 'push', 'origin', 'main')
    (repo / 'main_computer/seh/relay.py').write_text('local SEH update\n')
    _run(repo, 'add', '--', 'main_computer/seh/relay.py')
    _run(repo, 'commit', '-m', 'local work')
    _run(repo, 'fetch', 'origin', 'main')
    with pytest.raises(seh.SEHGitPublicationError, match='isolated') as err:
        seh.git_source(repo)
    assert 'worktree add --detach' in err.value.git_command


def test_unfetched_remote_tip_refuses_any_push(publication_repo):
    repo, remote, _ = publication_repo
    other = repo.parent / 'other'
    subprocess.run(['git', 'clone', '-b', 'main', str(remote), str(other)],
                   check=True, capture_output=True)
    _run(other, 'config', 'user.name', 'Tests')
    _run(other, 'config', 'user.email', 'unit@test.invalid')
    (other / 'README.md').write_text('new upstream\n')
    _run(other, 'add', '--', 'README.md')
    _run(other, 'commit', '-m', 'new upstream')
    _run(other, 'push', 'origin', 'main')
    with pytest.raises(seh.SEHGitPublicationError, match='git fetch --no-tags origin main') as err:
        seh.git_source(repo)
    assert err.value.git_command == ''


def test_reverted_unrelated_commit_still_blocks_push(publication_repo):
    repo, _, _ = publication_repo
    (repo / 'README.md').write_text('temporary unrelated\n')
    _run(repo, 'add', '--', 'README.md')
    _run(repo, 'commit', '-m', 'unrelated added')
    (repo / 'README.md').unlink()
    _run(repo, 'add', '--', 'README.md')
    _run(repo, 'commit', '-m', 'unrelated reverted')
    (repo / 'main_computer/seh/relay.py').write_text('source version 2\n')
    _run(repo, 'add', '--', 'main_computer/seh/relay.py')
    _run(repo, 'commit', '-m', 'SEH update')
    with pytest.raises(seh.SEHGitPublicationError, match='isolated') as err:
        seh.git_source(repo)
    assert 'worktree add --detach' in err.value.git_command


def test_mixed_source_publication_preserves_local_history_and_is_accepted(publication_repo, tmp_path):
    repo, remote, base = publication_repo
    (repo / 'main_computer/seh/relay.py').write_text('source version 2\n')
    (repo / 'README.md').write_text('unrelated and private work\n')
    _run(repo, 'add', '--', 'main_computer/seh/relay.py', 'README.md')
    _run(repo, 'commit', '-m', 'mixed local work')
    local_head = _run(repo, 'rev-parse', 'HEAD')
    with pytest.raises(seh.SEHGitPublicationError) as err:
        seh.git_source(repo)
    command = err.value.git_command
    assert 'worktree add --detach' in command
    assert f"$expected = '{base}'" in command
    assert 'worktree remove --force' in command
    assert 'Copy-Item -LiteralPath $source' in command
    assert 'git -C $temp add -- $paths' in command
    assert 'git -C $temp commit' in command

    # Exercise the actual Git operations described by the generated PowerShell
    # sequence against a local bare remote.  PowerShell itself is unavailable
    # in this Linux test environment, so do not claim Windows execution.
    staging = tmp_path / 'seh-commit-worktree'
    _run(repo, 'worktree', 'add', '--detach', str(staging), base)
    try:
        for path in seh.SOURCES:
            dst = staging / path
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes((repo / path).read_bytes())
        _run(staging, 'add', '--', *seh.SOURCES)
        changed = _run(staging, 'diff', '--cached', '--name-only').splitlines()
        assert changed == ['main_computer/seh/relay.py']
        _run(staging, 'commit', '-m', 'Publish SEH deployment source')
        _run(staging, 'push', 'origin', 'HEAD:refs/heads/main')
    finally:
        _run(repo, 'worktree', 'remove', '--force', str(staging))
    published = _run(repo, 'ls-remote', 'origin', 'refs/heads/main').split()[0]
    assert published != base
    assert _run(repo, 'rev-parse', 'HEAD') == local_head
    assert _run(repo, 'show', 'HEAD:README.md') == 'unrelated and private work'
    assert _run(repo, 'show', f'{published}:main_computer/seh/relay.py') == 'source version 2'
    assert _run(repo, 'ls-tree', '--name-only', published, 'README.md') == ''
    # The newly published branch and local history now diverge, correctly.
    # That must NOT trigger another Git publication request when the SEH
    # runtime inputs are identical in both revisions.
    assert seh.git_source(repo)['revision'] == published


def test_dirty_source_with_unrelated_commits_gets_isolated_command(publication_repo):
    repo, _, original = publication_repo
    (repo / 'README.md').write_text('other committed work\n')
    _run(repo, 'add', '--', 'README.md')
    _run(repo, 'commit', '-m', 'other work')
    (repo / 'main_computer/seh/stream_writer.py').write_text('dirty source\n')
    with pytest.raises(seh.SEHGitPublicationError) as err:
        seh.git_source(repo)
    assert f"$expected = '{original}'" in err.value.git_command
    assert "'main_computer/seh/stream_writer.py'" in err.value.git_command
    assert 'worktree add --detach' in err.value.git_command


def test_behind_branch_with_changed_seh_source_refuses_rollback(publication_repo):
    repo, remote, _ = publication_repo
    other = repo.parent / 'other'
    subprocess.run(['git', 'clone', '-b', 'main', str(remote), str(other)],
                   check=True, capture_output=True)
    _run(other, 'config', 'user.name', 'Tests')
    _run(other, 'config', 'user.email', 'unit@test.invalid')
    (other / 'main_computer/seh/relay.py').write_text('new upstream protocol\n')
    _run(other, 'add', '--', 'main_computer/seh/relay.py')
    _run(other, 'commit', '-m', 'newer upstream protocol')
    _run(other, 'push', 'origin', 'main')
    _run(repo, 'fetch', 'origin', 'main')
    with pytest.raises(seh.SEHGitPublicationError, match='behind origin/main') as err:
        seh.git_source(repo)
    assert not err.value.git_command
