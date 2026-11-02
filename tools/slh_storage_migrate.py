"""Offline, non-destructive migration to this installation's runtime/state/slh.

Stop all SLH processes before migrating. The selected legacy source must be the
only existing older SLH authority; this tool refuses to merge or overwrite keys.

  python -m tools.slh_storage_migrate plan
  python -m tools.slh_storage_migrate slh --execute
  python -m tools.slh_storage_migrate identity --execute
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil

from main_computer.slh.storage import (
    slh_state_dir, legacy_slh_state_dir, previous_machine_slh_state_dir,
    has_legacy_authority, user_identity_dir, legacy_authenticator_dir,
    secure_machine_state_dir,
)


def _files(root: Path) -> list[Path]:
    if root.is_symlink():
        raise ValueError('Symbolic-link state roots are refused')
    files = []
    for directory, subdirs, filenames in os.walk(root, followlinks=False):
        base = Path(directory)
        if any((base / name).is_symlink() for name in [*subdirs, *filenames]):
            raise ValueError('Symbolic links in identity/state directories are refused')
        files.extend(base / name for name in filenames)
    return files


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _slh_source(explicit: Path | None = None) -> Path:
    if explicit is not None:
        return Path(explicit)
    sources = [root for root in (legacy_slh_state_dir(), previous_machine_slh_state_dir())
               if has_legacy_authority(root)]
    if len(sources) > 1:
        raise ValueError('Multiple prior SLH authorities exist; specify --source only after '
                         'verifying the correct organization identity offline')
    return sources[0] if sources else legacy_slh_state_dir()


def migrate(src: Path, dest: Path, *, execute: bool, machine: bool) -> dict:
    if not src.exists() or not src.is_dir():
        return {'status': 'SOURCE_ABSENT', 'source': str(src), 'destination': str(dest), 'files': 0}
    source_files = _files(src)
    if dest.exists() and any(dest.iterdir()):
        raise ValueError('Destination is not empty; refusing to overwrite or merge identity state')
    if src.resolve() == dest.resolve():
        raise ValueError('Source and destination are identical')
    report = {'source': str(src), 'destination': str(dest), 'files': len(source_files),
              'status': 'MIGRATION_PLANNED' if not execute else 'MIGRATED_SOURCE_PRESERVED'}
    if not execute:
        return report
    if machine:
        secure_machine_state_dir(dest)
    else:
        dest.mkdir(parents=True, exist_ok=True)
        if os.name == 'posix':
            dest.chmod(0o700)
    for item in source_files:
        relative = item.relative_to(src)
        target = dest / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if os.name == 'posix':
            target.parent.chmod(0o700)
        # Exclusive create: never silently overwrite a key or bootstrap state.
        with target.open('xb') as f:
            f.write(item.read_bytes())
        if os.name == 'posix':
            target.chmod(0o600)
        if _digest(item) != _digest(target):
            raise IOError('Migration copy verification failed: ' + str(relative))
    return report


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('command', choices=('plan', 'slh', 'identity'))
    p.add_argument('--execute', action='store_true')
    p.add_argument('--source', type=Path, help='Explicit older SLH source for verified recovery')
    args = p.parse_args(argv)
    if args.command == 'plan':
        values = [migrate(_slh_source(args.source), slh_state_dir(), execute=False, machine=True),
                  migrate(legacy_authenticator_dir(), user_identity_dir(), execute=False, machine=False)]
    elif args.command == 'slh':
        values = [migrate(_slh_source(args.source), slh_state_dir(),
                          execute=args.execute, machine=True)]
    else:
        values = [migrate(args.source or legacy_authenticator_dir(), user_identity_dir(),
                          execute=args.execute, machine=False)]
    print(json.dumps({'results': values}, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
