"""ONLYOFFICE candidate revision fence; activation requires hardened ingress.

This is storage integrity protection, not a route authenticator. A caller must authenticate and bind document + installation
before supplying the server-issued expected content SHA-256. Client-provided
expected hashes must NOT be treated as authorization.

The fence enforces one successful commit per issued base revision. A running
editor must obtain a fresh document configuration after a successful commit;
several callbacks using the same document key cannot all be accepted. That
conservative protocol needs real-editor testing before any production design.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import io
import os
from pathlib import Path
import re
import tempfile
import time
import zipfile


class RevisionConflict(Exception):
    """The workbook changed after this editing grant was issued."""


class RevisionInvalid(Exception):
    """A path or workbook payload is not acceptable."""


class RevisionLockTimeout(Exception):
    """Cross-process compare-and-swap could not acquire its lock in time."""


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class ShadowRevisionFence:
    def __init__(self, storage_root: Path, *, max_bytes: int = 50 * 1024 * 1024,
                 lock_timeout: float = 20.0):
        self.root = Path(storage_root).resolve(strict=True)
        if not self.root.is_dir():
            raise RevisionInvalid('storage root must be a directory')
        self.max_bytes = max_bytes
        self.lock_timeout = lock_timeout
        self.lock_root = self.root / '.shadow-document-locks'
        self.lock_root.mkdir(mode=0o700, exist_ok=True)
        if self.lock_root.is_symlink() or self.lock_root.resolve() != self.lock_root:
            raise RevisionInvalid('lock directory cannot be a symlink')

    def _relative(self, path: Path) -> str:
        requested = Path(path)
        if requested.is_symlink():
            raise RevisionInvalid('symlink workbook is not allowed')
        resolved = requested.resolve(strict=True)
        try:
            relative = resolved.relative_to(self.root)
        except ValueError as exc:
            raise RevisionInvalid('document outside authorized storage root') from exc
        if relative.suffix.lower() != '.xlsx' or not relative.parts or any(
                part.startswith('.') for part in relative.parts):
            raise RevisionInvalid('document must be a non-hidden XLSX path')
        return relative.as_posix()

    def snapshot(self, path: Path) -> str:
        self._relative(path)
        return sha256_bytes(Path(path).read_bytes())

    @staticmethod
    def _validate_workbook(data: bytes, max_bytes: int) -> None:
        if not isinstance(data, bytes) or len(data) > max_bytes or len(data) < 8:
            raise RevisionInvalid('invalid workbook length')
        if not data.startswith(b'PK'):
            raise RevisionInvalid('not a ZIP workbook')
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                infos = archive.infolist()
                if len(infos) > 10000 or sum(x.file_size for x in infos) > max_bytes * 5:
                    raise RevisionInvalid('workbook decompressed-size limit exceeded')
                names = {x.filename for x in infos}
                if not {'[Content_Types].xml', 'xl/workbook.xml'} <= names:
                    raise RevisionInvalid('not an XLSX package')
                if any(x.filename.startswith('/') or '..' in x.filename.split('/') for x in infos):
                    raise RevisionInvalid('unsafe ZIP entry')
        except (zipfile.BadZipFile, EOFError) as exc:
            raise RevisionInvalid('invalid workbook archive') from exc

    @contextmanager
    def _document_lock(self, relative: str):
        digest = hashlib.sha256(relative.encode('utf-8')).hexdigest()
        lock_path = self.lock_root / (digest + '.lock')
        if lock_path.is_symlink():
            raise RevisionInvalid('symlink lock file is not allowed')
        # The lock file is persistent (not a leaked temp workbook). Every
        # independent process opens its own file descriptor for OS-level locks.
        with lock_path.open('a+b') as file:
            file.seek(0, os.SEEK_END)
            if file.tell() == 0:
                file.write(b'\x00')
                file.flush()
            deadline = time.monotonic() + self.lock_timeout
            if os.name == 'nt':
                import msvcrt
                file.seek(0)
                while True:
                    try:
                        msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
                        break
                    except OSError as exc:
                        if time.monotonic() >= deadline:
                            raise RevisionLockTimeout('Windows workbook lock deadline exceeded') from exc
                        time.sleep(.025)
                try:
                    yield
                finally:
                    file.seek(0)
                    msvcrt.locking(file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                while True:
                    try:
                        fcntl.flock(file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                        break
                    except BlockingIOError as exc:
                        if time.monotonic() >= deadline:
                            raise RevisionLockTimeout('workbook lock deadline exceeded') from exc
                        time.sleep(.025)
                try:
                    yield
                finally:
                    fcntl.flock(file.fileno(), fcntl.LOCK_UN)

    def commit(self, path: Path, expected_sha256: str, data: bytes) -> str:
        """Commit new bytes only if the authorized base revision is unchanged.

        ``expected_sha256`` must originate from the server-issued, verified
        editing grant, not from the callback request body or a query parameter.
        The comparison and replacement occur under the same process-shared lock.
        """
        if not isinstance(expected_sha256, str) or not re.fullmatch(r'[a-f0-9]{64}', expected_sha256):
            raise RevisionInvalid('missing or invalid server-issued base revision')
        self._validate_workbook(data, self.max_bytes)
        relative = self._relative(path)
        with self._document_lock(relative):
            # Resolve and re-check while under lock. This protects against
            # symlink substitution by an independent actor at the filesystem.
            if self._relative(path) != relative:
                raise RevisionInvalid('document identity changed')
            current = sha256_bytes(Path(path).read_bytes())
            if current != expected_sha256:
                raise RevisionConflict('stale editor revision; reload document before saving')
            new_hash = sha256_bytes(data)
            if new_hash == current:
                raise RevisionConflict('duplicate or unchanged callback cannot reuse the grant')
            temp_name = None
            try:
                with tempfile.NamedTemporaryFile('wb', delete=False, dir=Path(path).parent,
                                                 prefix=f'.{Path(path).name}.', suffix='.tmp') as handle:
                    temp_name = handle.name
                    handle.write(data)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temp_name, path)
                temp_name = None
            finally:
                if temp_name is not None:
                    try:
                        os.unlink(temp_name)
                    except FileNotFoundError:
                        pass
            return new_hash
