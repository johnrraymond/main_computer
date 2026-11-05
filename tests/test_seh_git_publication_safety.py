"""Real Git tests: published image checks and exact Stable Hub Git guidance."""
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
        file.write_bytes((Path(__file__).resolve().parents[1] / name).read_bytes())
    _run(repo, 'add', '--', *seh.SOURCES)
    _run(repo, 'commit', '-m', 'SEH baseline')
    subprocess.run(['git', 'init', '--bare', str(remote)], check=True,
                   capture_output=True)
    _run(repo, 'remote', 'add', 'origin', str(remote))
    _run(repo, 'push', 'origin', 'main')
    base = _run(repo, 'rev-parse', 'HEAD')
    original = seh.subprocess.run

    def guarded(argv, **kwargs):
        if argv[3:] == ['remote', 'get-url', 'origin']:
            return subprocess.CompletedProcess(argv, 0,
                'https://github.com/johnrraymond/main_computer.git\n', '')
        return original(argv, **kwargs)

    monkeypatch.setattr(seh.subprocess, 'run', guarded)
    return repo, remote, base


def _assert_mismatch(repo, name):
    with pytest.raises(seh.SEHGitPublicationError, match='build inputs differ') as error:
        seh.git_source(repo)
    assert name in str(error.value)
    assert 'git' in (error.value.git_command or str(error.value))


def test_published_source_passes_with_unrelated_local_commits(publication_repo):
    repo, _, revision = publication_repo
    (repo / 'README.md').write_text('unrelated local work\n')
    _run(repo, 'add', '--', 'README.md')
    _run(repo, 'commit', '-m', 'unrelated change')
    assert _run(repo, 'rev-parse', 'HEAD') != revision
    assert seh.git_source(repo)['revision'] == revision


def test_unpublished_committed_source_uses_hub_unpushed_head_message(publication_repo):
    repo, _, _ = publication_repo
    path = repo / 'main_computer/seh/relay.py'
    path.write_text('SEH_TEST = 1\n')
    _run(repo, 'add', '--', 'main_computer/seh/relay.py')
    _run(repo, 'commit', '-m', 'source not published')
    _assert_mismatch(repo, 'main_computer/seh/relay.py')
    with pytest.raises(seh.SEHGitPublicationError) as exc:
        seh.git_source(repo)
    assert exc.value.git_command == ''
    assert 'If HEAD is the intended revision, run: git push origin HEAD' in str(exc.value)
    assert 'resolve any remote divergence before retrying' in str(exc.value)
    assert 'git fetch' not in str(exc.value)


def test_dirty_source_is_refused_with_scoped_hub_style_commit_push(publication_repo):
    repo, _, _ = publication_repo
    (repo / 'main_computer/seh/stream_writer.py').write_text('dirty stream writer\n')
    _assert_mismatch(repo, 'main_computer/seh/stream_writer.py')
    with pytest.raises(seh.SEHGitPublicationError) as exc:
        seh.git_source(repo)
    command = exc.value.git_command
    assert "git add -- 'main_computer/seh/stream_writer.py'" in command
    from tools.hub_control.common.deployment import _render_git_commit_push_command
    assert command == _render_git_commit_push_command([
        {'status': ' M', 'path': 'main_computer/seh/stream_writer.py'}
    ])
    assert "git commit --only -m 'Update Hub deployment source'" in command
    assert 'git push origin HEAD' in command
    assert 'worktree' not in command
    assert 'force' not in command

def test_missing_local_image_input_does_not_suggest_deletion_commit(publication_repo):
    repo, _, _ = publication_repo
    (repo / '.dockerignore').unlink()
    with pytest.raises(seh.SEHGitPublicationError, match='missing locally') as exc:
        seh.git_source(repo)
    assert exc.value.git_command == ''
    assert 'Restore the required SEH source' in str(exc.value)


def test_only_hub_renderer_supplies_git_commit_commands(monkeypatch):
    expected = 'HUB_RENDERED_COMMAND'
    monkeypatch.setattr(seh, '_render_git_commit_push_command', lambda changes: (
        expected if changes == [{'status': ' M', 'path': 'main_computer/seh/relay.py'}] else ''
    ))
    assert seh._seh_git_operator_command(['main_computer/seh/relay.py']) == expected


def test_remote_unrelated_update_does_not_block_clean_seh(publication_repo):
    repo, remote, _ = publication_repo
    other = repo.parent / 'other'
    subprocess.run(['git', 'clone', '-b', 'main', str(remote), str(other)],
                   check=True, capture_output=True)
    _run(other, 'config', 'user.name', 'Tests')
    _run(other, 'config', 'user.email', 'unit@test.invalid')
    (other / 'README.md').write_text('upstream-only update\n')
    _run(other, 'add', '--', 'README.md')
    _run(other, 'commit', '-m', 'upstream')
    _run(other, 'push', 'origin', 'main')
    _run(repo, 'fetch', 'origin', 'main')
    assert seh.git_source(repo)['revision'] == _run(repo, 'ls-remote', 'origin', 'refs/heads/main').split()[0]


def test_diverged_branch_with_different_source_reports_files(publication_repo):
    repo, remote, _ = publication_repo
    other = repo.parent / 'other'
    subprocess.run(['git', 'clone', '-b', 'main', str(remote), str(other)],
                   check=True, capture_output=True)
    _run(other, 'config', 'user.name', 'Tests')
    _run(other, 'config', 'user.email', 'unit@test.invalid')
    (other / 'README.md').write_text('upstream change\n')
    _run(other, 'add', '--', 'README.md')
    _run(other, 'commit', '-m', 'upstream')
    _run(other, 'push', 'origin', 'main')
    (repo / 'main_computer/seh/relay.py').write_text('LOCAL_ONLY = 1\n')
    _run(repo, 'add', '--', 'main_computer/seh/relay.py')
    _run(repo, 'commit', '-m', 'unpublished local source')
    _run(repo, 'fetch', 'origin', 'main')
    _assert_mismatch(repo, 'main_computer/seh/relay.py')
    with pytest.raises(seh.SEHGitPublicationError) as exc:
        seh.git_source(repo)
    assert exc.value.git_command == ''
    assert 'If HEAD is the intended revision, run: git push origin HEAD' in str(exc.value)
    assert 'git fetch' not in str(exc.value)


def test_remote_tip_not_fetched_fails_closed(publication_repo):
    repo, remote, _ = publication_repo
    other = repo.parent / 'other'
    subprocess.run(['git', 'clone', '-b', 'main', str(remote), str(other)],
                   check=True, capture_output=True)
    _run(other, 'config', 'user.name', 'Tests')
    _run(other, 'config', 'user.email', 'unit@test.invalid')
    (other / 'README.md').write_text('advance remote\n')
    _run(other, 'add', '--', 'README.md')
    _run(other, 'commit', '-m', 'advance')
    _run(other, 'push', 'origin', 'main')
    with pytest.raises(seh.SEHGitPublicationError, match='not available locally') as e:
        seh.git_source(repo)
    assert e.value.git_command == ''
    assert 'Git state must be reconciled' in str(e.value)


def test_isolated_published_commit_matches_actual_source_without_moving_local_head(publication_repo, tmp_path):
    """Reproduce the exact failure: remote publication advances, local HEAD does not."""
    repo, _, base = publication_repo
    target = repo / 'main_computer/seh/stream_writer.py'
    target.write_text(target.read_text() + '\n# published independently\n')
    (repo / 'README.md').write_text('unrelated local work\n')
    _run(repo, 'add', '--', 'README.md')
    _run(repo, 'commit', '-m', 'unrelated local commit')
    local_head = _run(repo, 'rev-parse', 'HEAD')
    _assert_mismatch(repo, 'main_computer/seh/stream_writer.py')
    detached = tmp_path / 'published'
    _run(repo, 'worktree', 'add', '--detach', str(detached), base)
    try:
        path = detached / 'main_computer/seh/stream_writer.py'
        path.write_bytes(target.read_bytes())
        _run(detached, 'add', '--', 'main_computer/seh/stream_writer.py')
        _run(detached, 'commit', '-m', 'published source only')
        _run(detached, 'push', 'origin', 'HEAD:refs/heads/main')
    finally:
        _run(repo, 'worktree', 'remove', '--force', str(detached))
    published = _run(repo, 'ls-remote', 'origin', 'refs/heads/main').split()[0]
    assert _run(repo, 'rev-parse', 'HEAD') == local_head
    assert published != base
    # Despite local stream_writer.py remaining dirty relative to local HEAD,
    # its bytes are on the exact published commit; the guard must accept it.
    assert _run(repo, 'status', '--porcelain', '--', 'main_computer/seh/stream_writer.py')
    assert seh.git_source(repo)['revision'] == published
    assert seh.git_source(repo)['revision'] == published  # no repeated command


@pytest.mark.parametrize('missing', ['writer-copy', 'wget'])
def test_published_broken_image_is_refused(publication_repo, missing):
    repo, _, _ = publication_repo
    dockerfile = repo / 'deploy/seh/Dockerfile'
    source = dockerfile.read_text()
    if missing == 'writer-copy':
        source = source.replace('COPY main_computer/seh/stream_writer.py main_computer/seh/stream_writer.py\n', '')
        expected = 'does not COPY required module: main_computer/seh/stream_writer.py'
    else:
        source = source.replace('curl wget \\', 'curl \\')
        expected = 'does not install Coolify healthcheck wget'
    dockerfile.write_text(source)
    _run(repo, 'add', '--', 'deploy/seh/Dockerfile')
    _run(repo, 'commit', '-m', 'broken published image')
    _run(repo, 'push', 'origin', 'main')
    with pytest.raises(seh.SEHGitPublicationError, match=expected):
        seh.git_source(repo)


def test_new_import_is_discovered_and_requires_published_docker_copy(publication_repo):
    repo, _, _ = publication_repo
    path = repo / 'main_computer/seh/extra_runtime.py'
    path.write_text('EXTRA_RUNTIME = True\n')
    relay = repo / 'main_computer/seh/relay.py'
    relay.write_text(relay.read_text() + '\nfrom .extra_runtime import EXTRA_RUNTIME\n')
    assert 'main_computer/seh/extra_runtime.py' in seh._seh_git_source_paths(repo)
    _assert_mismatch(repo, 'main_computer/seh/extra_runtime.py')
    _run(repo, 'add', '--', 'main_computer/seh/relay.py', 'main_computer/seh/extra_runtime.py')
    _run(repo, 'commit', '-m', 'new module')
    _run(repo, 'push', 'origin', 'main')
    with pytest.raises(seh.SEHGitPublicationError, match='does not COPY required module: main_computer/seh/extra_runtime.py'):
        seh.git_source(repo)
    dockerfile = repo / 'deploy/seh/Dockerfile'
    dockerfile.write_text(dockerfile.read_text().replace(
        'USER seh', 'COPY main_computer/seh/extra_runtime.py main_computer/seh/extra_runtime.py\nUSER seh'))
    _run(repo, 'add', '--', 'deploy/seh/Dockerfile')
    _run(repo, 'commit', '-m', 'include module in published image')
    _run(repo, 'push', 'origin', 'main')
    assert seh.git_source(repo)['revision'] == _run(repo, 'rev-parse', 'HEAD')
