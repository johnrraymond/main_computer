"""Verified private-state storage and recovery closure operations."""

from __future__ import annotations

from dataclasses import dataclass
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
from typing import Any

import yaml

from . import atomic_files
from .canonical import canonical_json, canonical_yaml
from .errors import MotherError
from .hashing import ordered_root, sha256
from .models import ContentHash, OperationIdentity, PrivateStateBinding, PrivateStatePaths


_MODULE_ID = "MOTHER-OFM-STATE-004"
_PRIVATE_STATE_KIND = "main_computer.mother.private_state.v1"
_PRIVATE_METADATA_KIND = "main_computer.mother.private_state_metadata.v1"
_PRIVATE_MANIFEST_VERSION = "main_computer.mother.private_recovery_manifest.v1"
_STABLE_READ_ATTEMPTS = 3

_ACCESS_ALLOWED_ACE_TYPE = 0
_FILE_ATTRIBUTE_DIRECTORY = 0x10
_FILE_ATTRIBUTE_REPARSE_POINT = 0x400
_FILE_ALL_ACCESS = 0x001F01FF
_SYSTEM_SID = "S-1-5-18"


def _operation(value: OperationIdentity) -> OperationIdentity:
    if not isinstance(value, OperationIdentity):
        raise TypeError("operation must be an OperationIdentity")
    return value


def _error(
    operation: OperationIdentity,
    code: str,
    message: str,
    *,
    retry_class: str = "never",
    cause: BaseException | None = None,
) -> MotherError:
    return MotherError(
        code=code,
        message=message,
        operation_id=operation.operation_id,
        module_id=_MODULE_ID,
        retry_class=retry_class,
        authority_effect="none",
        durable_effect_refs=(),
        evidence_refs=(),
        cause_class="" if cause is None else type(cause).__name__,
    )


def _positive_int(value: object, name: str) -> int:
    if type(value) is not int or value <= 0:
        raise TypeError(f"{name} must be a positive integer")
    return value


def _nonnegative_int(value: object, name: str) -> int:
    if type(value) is not int or value < 0:
        raise TypeError(f"{name} must be a non-negative integer")
    return value


def _text(value: object, name: str) -> str:
    if type(value) is not str or not value:
        raise TypeError(f"{name} must be a non-empty string")
    return value


def _identifier(value: object, name: str) -> str:
    text = _text(value, name)
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")
    if text in {".", ".."} or any(ch not in allowed for ch in text):
        raise ValueError(f"invalid {name}")
    return text


def _relative_path(value: object) -> str:
    text = _text(value, "relative_path")
    if "\\" in text or "\x00" in text:
        raise ValueError("invalid relative_path")
    path = PurePosixPath(text)
    if path.is_absolute() or text.startswith("/") or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("invalid relative_path")
    normalized = path.as_posix()
    if normalized != text:
        raise ValueError("relative_path is not canonical")
    return text


def _content_hash(value: object) -> ContentHash:
    if not isinstance(value, dict) or set(value) != {"algorithm", "digest", "schema_version"}:
        raise TypeError("invalid content hash")
    if value.get("schema_version") != 1:
        raise ValueError("invalid content hash schema")
    return ContentHash(algorithm=value["algorithm"], digest=value["digest"])


def _hash_wire(value: ContentHash) -> dict[str, object]:
    return {"algorithm": value.algorithm, "digest": value.digest, "schema_version": 1}


def _binding_wire(value: PrivateStateBinding) -> dict[str, object]:
    return {
        "content_hash": _hash_wire(value.content_hash),
        "generation": value.generation,
        "private_state_kind": value.private_state_kind,
        "recovery_manifest_hash": _hash_wire(value.recovery_manifest_hash),
    }


def _redacted_repr(name: str, **public: object) -> str:
    pieces = ", ".join(f"{key}={value!r}" for key, value in public.items())
    return f"{name}({pieces}, redacted=True)"


@dataclass(frozen=True, slots=True)
class PrivateStateMetadata:
    kind: str
    private_state_kind: str
    generation: int
    content_hash: ContentHash
    previous_content_hash: ContentHash | None
    recovery_manifest_hash: ContentHash
    updated_at: str
    updated_by_action_id: str

    def __post_init__(self) -> None:
        if self.kind != _PRIVATE_METADATA_KIND:
            raise ValueError("unknown private-state metadata kind")
        if self.private_state_kind != _PRIVATE_STATE_KIND:
            raise ValueError("unknown private-state kind")
        _positive_int(self.generation, "generation")
        _text(self.updated_at, "updated_at")
        _text(self.updated_by_action_id, "updated_by_action_id")
        if self.generation == 1 and self.previous_content_hash is not None:
            raise ValueError("generation one must have no predecessor")
        if self.generation > 1 and self.previous_content_hash is None:
            raise ValueError("later generation requires predecessor")


@dataclass(frozen=True, slots=True)
class PrivateRecoveryManifestEntry:
    relative_path: str
    generation: int
    content_hash: ContentHash
    byte_length: int

    def __post_init__(self) -> None:
        _relative_path(self.relative_path)
        _positive_int(self.generation, "generation")
        _nonnegative_int(self.byte_length, "byte_length")


@dataclass(frozen=True, slots=True)
class PrivateRecoveryManifest:
    manifest_version: str
    private_state_generation: int
    entries: tuple[PrivateRecoveryManifestEntry, ...]

    def __post_init__(self) -> None:
        if self.manifest_version != _PRIVATE_MANIFEST_VERSION:
            raise ValueError("unknown private recovery manifest version")
        _positive_int(self.private_state_generation, "private_state_generation")
        if type(self.entries) is not tuple:
            raise TypeError("entries must be a tuple")
        names = tuple(entry.relative_path for entry in self.entries)
        if names != tuple(sorted(names, key=lambda value: value.encode("utf-8"))):
            raise ValueError("manifest entries are not canonically ordered")
        if len(set(names)) != len(names):
            raise ValueError("duplicate manifest entry")
        if any(entry.generation != self.private_state_generation for entry in self.entries):
            raise ValueError("manifest generation mismatch")


@dataclass(frozen=True, slots=True, repr=False)
class PrivateRecoveryObject:
    relative_path: str
    generation: int
    content_hash: ContentHash
    payload: bytes

    def __post_init__(self) -> None:
        _relative_path(self.relative_path)
        _positive_int(self.generation, "generation")
        if type(self.payload) is not bytes:
            raise TypeError("payload must be exact bytes")

    def __repr__(self) -> str:
        return _redacted_repr(
            type(self).__name__,
            relative_path=self.relative_path,
            generation=self.generation,
            content_hash=self.content_hash,
            byte_length=len(self.payload),
        )


@dataclass(frozen=True, slots=True, repr=False)
class PrivateStateReadResult:
    paths: PrivateStatePaths
    document_bytes: bytes
    canonical_object_bytes: bytes
    metadata: PrivateStateMetadata
    recovery_manifest: PrivateRecoveryManifest
    recovery_objects: tuple[PrivateRecoveryObject, ...]
    binding: PrivateStateBinding

    def __post_init__(self) -> None:
        if type(self.document_bytes) is not bytes or type(self.canonical_object_bytes) is not bytes:
            raise TypeError("private state bytes must be exact bytes")
        if type(self.recovery_objects) is not tuple:
            raise TypeError("recovery_objects must be a tuple")

    def __repr__(self) -> str:
        return _redacted_repr(
            type(self).__name__,
            paths=self.paths,
            metadata=self.metadata,
            recovery_manifest=self.recovery_manifest,
            binding=self.binding,
        )


@dataclass(frozen=True, slots=True, repr=False)
class ResolvedValidatorIdentity:
    validator_ref: str
    address: str
    private_key: bytes

    def __post_init__(self) -> None:
        _text(self.validator_ref, "validator_ref")
        if type(self.address) is not str:
            raise TypeError("address must be a string")
        if type(self.private_key) is not bytes:
            raise TypeError("private_key must be exact bytes")

    def __repr__(self) -> str:
        return _redacted_repr(
            type(self).__name__, validator_ref=self.validator_ref, address=self.address
        )


@dataclass(frozen=True, slots=True, repr=False)
class PrivateRecoveryClosure:
    source_paths: PrivateStatePaths
    document_bytes: bytes
    metadata_bytes: bytes
    recovery_manifest_bytes: bytes
    recovery_objects: tuple[PrivateRecoveryObject, ...]
    binding: PrivateStateBinding
    closure_hash: ContentHash

    def __post_init__(self) -> None:
        for name in ("document_bytes", "metadata_bytes", "recovery_manifest_bytes"):
            if type(getattr(self, name)) is not bytes:
                raise TypeError(f"{name} must be exact bytes")
        if type(self.recovery_objects) is not tuple:
            raise TypeError("recovery_objects must be a tuple")

    def __repr__(self) -> str:
        return _redacted_repr(
            type(self).__name__,
            source_paths=self.source_paths,
            binding=self.binding,
            closure_hash=self.closure_hash,
            recovery_object_count=len(self.recovery_objects),
        )


@dataclass(frozen=True, slots=True)
class PrivateStateInstallResult:
    installed: bool
    binding: PrivateStateBinding
    commit_manifest_hash: ContentHash

    def __post_init__(self) -> None:
        if type(self.installed) is not bool:
            raise TypeError("installed must be a boolean")


def _validate_paths(paths: PrivateStatePaths, operation: OperationIdentity) -> None:
    if not isinstance(paths, PrivateStatePaths):
        raise TypeError("paths must be PrivateStatePaths")
    root = paths.root
    expected = PrivateStatePaths(
        root=root,
        identity_file=root / "identity.private.yaml",
        metadata_file=root / "identity.private.meta.json",
        recovery_objects_root=root / "private-recovery" / "objects",
        recovery_manifest=root / "private-recovery" / "manifest.json",
    )
    if paths != expected:
        raise _error(operation, "MOTHER_STATE_MALFORMED_PRIVATE_STATE", "private-state paths are not canonically paired")


@dataclass(frozen=True, slots=True)
class _WindowsAce:
    sid: str
    access_mask: int
    ace_type: int
    inherited: bool


@dataclass(frozen=True, slots=True)
class _WindowsSecuritySnapshot:
    is_directory: bool
    is_reparse_point: bool
    owner_sid: str
    dacl_protected: bool
    aces: tuple[_WindowsAce, ...]


def _is_windows() -> bool:
    return os.name == "nt"


def _windows_error(code: int | None = None) -> OSError:
    return ctypes.WinError(ctypes.get_last_error() if code is None else code)


def _windows_sid_string(sid: int) -> str:
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    convert = advapi32.ConvertSidToStringSidW
    convert.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_wchar_p)]
    convert.restype = wintypes.BOOL
    local_free = kernel32.LocalFree
    local_free.argtypes = [wintypes.HLOCAL]
    local_free.restype = wintypes.HLOCAL
    rendered = ctypes.c_wchar_p()
    if not convert(ctypes.c_void_p(sid), ctypes.byref(rendered)):
        raise _windows_error()
    try:
        if rendered.value is None:
            raise OSError("Windows returned an empty SID")
        return rendered.value
    finally:
        local_free(ctypes.cast(rendered, wintypes.HLOCAL))


def _windows_current_user_sid() -> str:
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    token_query = 0x0008
    token_user = 1
    error_insufficient_buffer = 122

    get_current_process = kernel32.GetCurrentProcess
    get_current_process.argtypes = []
    get_current_process.restype = wintypes.HANDLE
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]
    close_handle.restype = wintypes.BOOL
    open_process_token = advapi32.OpenProcessToken
    open_process_token.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    open_process_token.restype = wintypes.BOOL
    get_token_information = advapi32.GetTokenInformation
    get_token_information.argtypes = [
        wintypes.HANDLE,
        ctypes.c_uint,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    get_token_information.restype = wintypes.BOOL

    token = wintypes.HANDLE()
    if not open_process_token(get_current_process(), token_query, ctypes.byref(token)):
        raise _windows_error()
    try:
        required = wintypes.DWORD()
        get_token_information(token, token_user, None, 0, ctypes.byref(required))
        error = ctypes.get_last_error()
        if error != error_insufficient_buffer or required.value == 0:
            raise _windows_error(error)
        buffer = ctypes.create_string_buffer(required.value)
        if not get_token_information(
            token,
            token_user,
            buffer,
            required,
            ctypes.byref(required),
        ):
            raise _windows_error()
        sid_pointer = ctypes.c_void_p.from_buffer(buffer).value
        if sid_pointer is None:
            raise OSError("Windows returned an empty process-token SID")
        return _windows_sid_string(sid_pointer)
    finally:
        close_handle(token)


def _windows_security_snapshot(path: Path) -> _WindowsSecuritySnapshot:
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    invalid_file_attributes = 0xFFFFFFFF
    se_file_object = 1
    owner_security_information = 0x00000001
    dacl_security_information = 0x00000004
    se_dacl_protected = 0x1000
    inherited_ace = 0x10

    class _Acl(ctypes.Structure):
        _fields_ = [
            ("AclRevision", ctypes.c_ubyte),
            ("Sbz1", ctypes.c_ubyte),
            ("AclSize", ctypes.c_ushort),
            ("AceCount", ctypes.c_ushort),
            ("Sbz2", ctypes.c_ushort),
        ]

    class _AceHeader(ctypes.Structure):
        _fields_ = [
            ("AceType", ctypes.c_ubyte),
            ("AceFlags", ctypes.c_ubyte),
            ("AceSize", ctypes.c_ushort),
        ]

    class _AccessAce(ctypes.Structure):
        _fields_ = [
            ("Header", _AceHeader),
            ("Mask", wintypes.DWORD),
            ("SidStart", wintypes.DWORD),
        ]

    get_attributes = kernel32.GetFileAttributesW
    get_attributes.argtypes = [wintypes.LPCWSTR]
    get_attributes.restype = wintypes.DWORD
    attributes = get_attributes(str(path))
    if attributes == invalid_file_attributes:
        error = ctypes.get_last_error()
        if error in {2, 3}:
            raise FileNotFoundError(str(path))
        raise _windows_error(error)

    get_security = advapi32.GetNamedSecurityInfoW
    get_security.argtypes = [
        wintypes.LPWSTR,
        ctypes.c_int,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
    ]
    get_security.restype = wintypes.DWORD
    get_control = advapi32.GetSecurityDescriptorControl
    get_control.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_ushort),
        ctypes.POINTER(wintypes.DWORD),
    ]
    get_control.restype = wintypes.BOOL
    get_ace = advapi32.GetAce
    get_ace.argtypes = [ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p)]
    get_ace.restype = wintypes.BOOL
    local_free = kernel32.LocalFree
    local_free.argtypes = [wintypes.HLOCAL]
    local_free.restype = wintypes.HLOCAL

    owner = ctypes.c_void_p()
    dacl = ctypes.c_void_p()
    descriptor = ctypes.c_void_p()
    status = get_security(
        str(path),
        se_file_object,
        owner_security_information | dacl_security_information,
        ctypes.byref(owner),
        None,
        ctypes.byref(dacl),
        None,
        ctypes.byref(descriptor),
    )
    if status != 0:
        raise _windows_error(status)
    try:
        if owner.value is None or dacl.value is None:
            raise OSError("private-state owner or DACL is absent")
        control = ctypes.c_ushort()
        revision = wintypes.DWORD()
        if not get_control(descriptor, ctypes.byref(control), ctypes.byref(revision)):
            raise _windows_error()
        acl = ctypes.cast(dacl, ctypes.POINTER(_Acl)).contents
        aces: list[_WindowsAce] = []
        for index in range(acl.AceCount):
            ace_pointer = ctypes.c_void_p()
            if not get_ace(dacl, index, ctypes.byref(ace_pointer)):
                raise _windows_error()
            header = ctypes.cast(ace_pointer, ctypes.POINTER(_AceHeader)).contents
            if header.AceType != _ACCESS_ALLOWED_ACE_TYPE:
                aces.append(
                    _WindowsAce(
                        sid="",
                        access_mask=0,
                        ace_type=header.AceType,
                        inherited=bool(header.AceFlags & inherited_ace),
                    )
                )
                continue
            ace = ctypes.cast(ace_pointer, ctypes.POINTER(_AccessAce)).contents
            sid_address = ace_pointer.value + _AccessAce.SidStart.offset
            aces.append(
                _WindowsAce(
                    sid=_windows_sid_string(sid_address),
                    access_mask=int(ace.Mask),
                    ace_type=header.AceType,
                    inherited=bool(header.AceFlags & inherited_ace),
                )
            )
        return _WindowsSecuritySnapshot(
            is_directory=bool(attributes & _FILE_ATTRIBUTE_DIRECTORY),
            is_reparse_point=bool(attributes & _FILE_ATTRIBUTE_REPARSE_POINT),
            owner_sid=_windows_sid_string(owner.value),
            dacl_protected=bool(control.value & se_dacl_protected),
            aces=tuple(aces),
        )
    finally:
        local_free(ctypes.cast(descriptor, wintypes.HLOCAL))


def _set_windows_private_security(path: Path, *, is_directory: bool) -> None:
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    sddl_revision_1 = 1
    se_file_object = 1
    dacl_security_information = 0x00000004
    protected_dacl_security_information = 0x80000000

    service_sid = _windows_current_user_sid()
    allowed_sids = tuple(dict.fromkeys((service_sid, _SYSTEM_SID)))
    ace_flags = "OICI" if is_directory else ""
    dacl = "".join(f"(A;{ace_flags};FA;;;{sid})" for sid in allowed_sids)
    sddl = f"D:P{dacl}"

    convert = advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW
    convert.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(wintypes.ULONG),
    ]
    convert.restype = wintypes.BOOL
    get_dacl = advapi32.GetSecurityDescriptorDacl
    get_dacl.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(wintypes.BOOL),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(wintypes.BOOL),
    ]
    get_dacl.restype = wintypes.BOOL
    set_security = advapi32.SetNamedSecurityInfoW
    set_security.argtypes = [
        wintypes.LPWSTR,
        ctypes.c_int,
        wintypes.DWORD,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
    ]
    set_security.restype = wintypes.DWORD
    local_free = kernel32.LocalFree
    local_free.argtypes = [wintypes.HLOCAL]
    local_free.restype = wintypes.HLOCAL

    descriptor = ctypes.c_void_p()
    descriptor_size = wintypes.ULONG()
    if not convert(
        sddl,
        sddl_revision_1,
        ctypes.byref(descriptor),
        ctypes.byref(descriptor_size),
    ):
        raise _windows_error()
    try:
        dacl_present = wintypes.BOOL()
        dacl_pointer = ctypes.c_void_p()
        dacl_defaulted = wintypes.BOOL()
        if not get_dacl(
            descriptor,
            ctypes.byref(dacl_present),
            ctypes.byref(dacl_pointer),
            ctypes.byref(dacl_defaulted),
        ):
            raise _windows_error()
        if not dacl_present or dacl_pointer.value is None:
            raise OSError("generated private-state DACL is absent")
        status = set_security(
            str(path),
            se_file_object,
            dacl_security_information | protected_dacl_security_information,
            None,
            None,
            dacl_pointer,
            None,
        )
        if status != 0:
            raise _windows_error(status)
    finally:
        local_free(ctypes.cast(descriptor, wintypes.HLOCAL))


def _validate_windows_snapshot(
    snapshot: _WindowsSecuritySnapshot,
    *,
    expected_directory: bool | None,
) -> None:
    if snapshot.is_reparse_point:
        raise ValueError("private-state reparse points are forbidden")
    if expected_directory is not None and snapshot.is_directory != expected_directory:
        raise ValueError("private-state member has an unsafe filesystem type")
    service_sid = _windows_current_user_sid()
    allowed_sids = tuple(dict.fromkeys((service_sid, _SYSTEM_SID)))
    if snapshot.owner_sid != service_sid or not snapshot.dacl_protected:
        raise ValueError("private-state owner or DACL protection is invalid")
    if len(snapshot.aces) != len(allowed_sids):
        raise ValueError("private-state DACL has unexpected access entries")
    if {ace.sid for ace in snapshot.aces} != set(allowed_sids):
        raise ValueError("private-state DACL grants an unexpected identity")
    for ace in snapshot.aces:
        if ace.ace_type != _ACCESS_ALLOWED_ACE_TYPE:
            raise ValueError("private-state DACL contains a non-allow entry")
        if ace.access_mask != _FILE_ALL_ACCESS:
            raise ValueError("private-state DACL grants noncanonical access")
        if ace.inherited:
            raise ValueError("private-state DACL contains inherited access")


def _validate_existing_path(
    path: Path,
    operation: OperationIdentity,
    *,
    expected_directory: bool | None,
) -> bool | None:
    if _is_windows():
        try:
            snapshot = _windows_security_snapshot(path)
            _validate_windows_snapshot(
                snapshot,
                expected_directory=expected_directory,
            )
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as exc:
            raise _error(
                operation,
                "MOTHER_STATE_PRIVATE_STATE_PERMISSION",
                "private-state Windows security could not be verified",
                cause=exc,
            ) from exc
        return snapshot.is_directory

    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise _error(
            operation,
            "MOTHER_STATE_PRIVATE_STATE_PERMISSION",
            "private-state metadata could not be verified",
            cause=exc,
        ) from exc
    is_directory = stat.S_ISDIR(metadata.st_mode)
    is_file = stat.S_ISREG(metadata.st_mode)
    if stat.S_ISLNK(metadata.st_mode) or not (is_directory or is_file):
        raise _error(
            operation,
            "MOTHER_STATE_PRIVATE_STATE_PERMISSION",
            "private-state links and special files are forbidden",
        )
    if expected_directory is not None and is_directory != expected_directory:
        raise _error(
            operation,
            "MOTHER_STATE_PRIVATE_STATE_PERMISSION",
            "private-state member has an unsafe filesystem type",
        )
    if metadata.st_uid != os.geteuid():
        raise _error(
            operation,
            "MOTHER_STATE_PRIVATE_STATE_PERMISSION",
            "private-state owner is unexpected",
        )
    expected_mode = 0o700 if is_directory else 0o600
    if stat.S_IMODE(metadata.st_mode) != expected_mode:
        raise _error(
            operation,
            "MOTHER_STATE_PRIVATE_STATE_PERMISSION",
            "private-state permissions are too broad",
        )
    return is_directory


def _validate_security(paths: PrivateStatePaths, operation: OperationIdentity) -> None:
    directories = (
        paths.root,
        paths.recovery_objects_root.parent,
        paths.recovery_objects_root,
    )
    files = (
        paths.identity_file,
        paths.metadata_file,
        paths.recovery_manifest,
    )
    for path in directories:
        _validate_existing_path(path, operation, expected_directory=True)
    for path in files:
        _validate_existing_path(path, operation, expected_directory=False)

    if _validate_existing_path(
        paths.recovery_objects_root,
        operation,
        expected_directory=True,
    ) is None:
        return
    pending = [paths.recovery_objects_root]
    while pending:
        current = pending.pop()
        try:
            with os.scandir(current) as entries:
                children = tuple(entries)
        except OSError as exc:
            raise _error(
                operation,
                "MOTHER_STATE_PRIVATE_STATE_PERMISSION",
                "private-state recovery tree could not be enumerated",
                cause=exc,
            ) from exc
        for entry in children:
            child = Path(entry.path)
            is_directory = _validate_existing_path(
                child,
                operation,
                expected_directory=None,
            )
            if is_directory:
                pending.append(child)


def _json_object(data: bytes, operation: OperationIdentity, *, malformed_code: str) -> dict[str, Any]:
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _error(operation, malformed_code, "durable JSON is malformed", cause=exc) from exc
    if type(value) is not dict:
        raise _error(operation, malformed_code, "durable JSON must be an object")
    try:
        if canonical_json(value) != data:
            raise _error(operation, malformed_code, "durable JSON is not canonical")
    except (TypeError, ValueError) as exc:
        if isinstance(exc, MotherError):
            raise
        raise _error(operation, malformed_code, "durable JSON is not canonical", cause=exc) from exc
    return value


def _parse_document(data: bytes, operation: OperationIdentity) -> tuple[dict[str, Any], bytes]:
    try:
        value = yaml.safe_load(data.decode("utf-8"))
    except (UnicodeDecodeError, yaml.YAMLError) as exc:
        raise _error(operation, "MOTHER_STATE_MALFORMED_PRIVATE_STATE", "private-state document is malformed", cause=exc) from exc
    if type(value) is not dict:
        raise _error(operation, "MOTHER_STATE_MALFORMED_PRIVATE_STATE", "private-state document must be an object")
    try:
        if canonical_yaml(value) != data:
            raise _error(operation, "MOTHER_STATE_MALFORMED_PRIVATE_STATE", "private-state YAML is not canonical")
        canonical_object = canonical_json(value)
    except (TypeError, ValueError) as exc:
        if isinstance(exc, MotherError):
            raise
        raise _error(operation, "MOTHER_STATE_MALFORMED_PRIVATE_STATE", "private-state document is not canonical", cause=exc) from exc
    if set(value) != {"kind", "networks", "schema_version"}:
        raise _error(operation, "MOTHER_STATE_MALFORMED_PRIVATE_STATE", "private-state document has unknown fields")
    if value.get("kind") != _PRIVATE_STATE_KIND or value.get("schema_version") != 1 or type(value.get("networks")) is not dict:
        raise _error(operation, "MOTHER_STATE_MALFORMED_PRIVATE_STATE", "private-state document schema is invalid")
    return value, canonical_object


def _parse_metadata(data: bytes, operation: OperationIdentity) -> PrivateStateMetadata:
    wire = _json_object(data, operation, malformed_code="MOTHER_STATE_MALFORMED_PRIVATE_STATE")
    required = {
        "content_hash", "generation", "kind", "previous_content_hash", "private_state_kind",
        "recovery_manifest_hash", "updated_at", "updated_by_action_id",
    }
    if set(wire) != required:
        raise _error(operation, "MOTHER_STATE_MALFORMED_PRIVATE_STATE", "private-state metadata fields are invalid")
    try:
        return PrivateStateMetadata(
            kind=wire["kind"],
            private_state_kind=wire["private_state_kind"],
            generation=wire["generation"],
            content_hash=_content_hash(wire["content_hash"]),
            previous_content_hash=None if wire["previous_content_hash"] is None else _content_hash(wire["previous_content_hash"]),
            recovery_manifest_hash=_content_hash(wire["recovery_manifest_hash"]),
            updated_at=wire["updated_at"],
            updated_by_action_id=wire["updated_by_action_id"],
        )
    except (TypeError, ValueError) as exc:
        raise _error(operation, "MOTHER_STATE_MALFORMED_PRIVATE_STATE", "private-state metadata is invalid", cause=exc) from exc


def _entry_wire(entry: PrivateRecoveryManifestEntry) -> dict[str, object]:
    return {
        "byte_length": entry.byte_length,
        "content_hash": _hash_wire(entry.content_hash),
        "generation": entry.generation,
        "relative_path": entry.relative_path,
    }


def _parse_manifest(data: bytes, operation: OperationIdentity) -> PrivateRecoveryManifest:
    wire = _json_object(data, operation, malformed_code="MOTHER_STATE_MALFORMED_PRIVATE_STATE")
    if set(wire) != {"entries", "manifest_version", "private_state_generation"} or type(wire.get("entries")) is not list:
        raise _error(operation, "MOTHER_STATE_MALFORMED_PRIVATE_STATE", "private recovery manifest fields are invalid")
    try:
        entries = tuple(
            PrivateRecoveryManifestEntry(
                relative_path=item["relative_path"],
                generation=item["generation"],
                content_hash=_content_hash(item["content_hash"]),
                byte_length=item["byte_length"],
            )
            for item in wire["entries"]
            if type(item) is dict and set(item) == {"byte_length", "content_hash", "generation", "relative_path"}
        )
        if len(entries) != len(wire["entries"]):
            raise ValueError("manifest entry shape is invalid")
        manifest = PrivateRecoveryManifest(
            manifest_version=wire["manifest_version"],
            private_state_generation=wire["private_state_generation"],
            entries=entries,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise _error(operation, "MOTHER_STATE_MALFORMED_PRIVATE_STATE", "private recovery manifest is invalid", cause=exc) from exc
    return manifest


def _manifest_bytes(manifest: PrivateRecoveryManifest) -> bytes:
    return canonical_json({
        "entries": [_entry_wire(entry) for entry in manifest.entries],
        "manifest_version": manifest.manifest_version,
        "private_state_generation": manifest.private_state_generation,
    })


def _metadata_bytes(metadata: PrivateStateMetadata) -> bytes:
    return canonical_json({
        "content_hash": _hash_wire(metadata.content_hash),
        "generation": metadata.generation,
        "kind": metadata.kind,
        "previous_content_hash": None if metadata.previous_content_hash is None else _hash_wire(metadata.previous_content_hash),
        "private_state_kind": metadata.private_state_kind,
        "recovery_manifest_hash": _hash_wire(metadata.recovery_manifest_hash),
        "updated_at": metadata.updated_at,
        "updated_by_action_id": metadata.updated_by_action_id,
    })


def _read_once(paths: PrivateStatePaths, operation: OperationIdentity, manifest_bytes: bytes) -> PrivateStateReadResult:
    required = (paths.recovery_manifest, paths.identity_file, paths.metadata_file)
    if any(not path.is_file() for path in required):
        raise _error(operation, "MOTHER_STATE_PRIVATE_STATE_MISSING", "committed private state is incomplete", retry_class="after-reobserve")
    _validate_security(paths, operation)
    try:
        document_bytes = paths.identity_file.read_bytes()
        metadata_bytes = paths.metadata_file.read_bytes()
    except FileNotFoundError as exc:
        raise _error(operation, "MOTHER_STATE_PRIVATE_STATE_MISSING", "committed private state is incomplete", retry_class="after-reobserve", cause=exc) from exc
    except OSError as exc:
        raise _error(operation, "MOTHER_STATE_PRIVATE_STATE_MISSING", "committed private state could not be read", retry_class="after-reobserve", cause=exc) from exc

    document, canonical_object = _parse_document(document_bytes, operation)
    del document
    metadata = _parse_metadata(metadata_bytes, operation)
    manifest = _parse_manifest(manifest_bytes, operation)

    if metadata.content_hash != sha256(document_bytes) or metadata.recovery_manifest_hash != sha256(manifest_bytes):
        raise _error(operation, "MOTHER_STATE_PRIVATE_STATE_REFERENCE_MISMATCH", "private-state durable references do not match")
    if manifest.private_state_generation != metadata.generation:
        raise _error(operation, "MOTHER_STATE_MALFORMED_PRIVATE_STATE", "private-state generations do not match")

    objects: list[PrivateRecoveryObject] = []
    for entry in manifest.entries:
        target = paths.recovery_objects_root / PurePosixPath(entry.relative_path)
        try:
            if not target.is_file():
                raise FileNotFoundError(target)
            payload = target.read_bytes()
        except FileNotFoundError as exc:
            raise _error(operation, "MOTHER_STATE_PRIVATE_STATE_MISSING", "committed private recovery object is missing", retry_class="after-reobserve", cause=exc) from exc
        except OSError as exc:
            raise _error(operation, "MOTHER_STATE_PRIVATE_STATE_MISSING", "private recovery object could not be read", retry_class="after-reobserve", cause=exc) from exc
        if len(payload) != entry.byte_length or sha256(payload) != entry.content_hash:
            raise _error(operation, "MOTHER_STATE_PRIVATE_STATE_REFERENCE_MISMATCH", "private recovery object reference does not match")
        objects.append(PrivateRecoveryObject(entry.relative_path, entry.generation, entry.content_hash, payload))

    binding = PrivateStateBinding(
        private_state_kind=metadata.private_state_kind,
        generation=metadata.generation,
        content_hash=metadata.content_hash,
        recovery_manifest_hash=metadata.recovery_manifest_hash,
    )
    return PrivateStateReadResult(
        paths=paths,
        document_bytes=document_bytes,
        canonical_object_bytes=canonical_object,
        metadata=metadata,
        recovery_manifest=manifest,
        recovery_objects=tuple(objects),
        binding=binding,
    )


def read_private_state(
    paths: PrivateStatePaths,
    *,
    operation: OperationIdentity,
) -> PrivateStateReadResult:
    operation = _operation(operation)
    _validate_paths(paths, operation)
    last_mismatch = False
    for _ in range(_STABLE_READ_ATTEMPTS):
        # Manifest bytes are the commit determinant and must bracket the read.
        if not paths.recovery_manifest.is_file():
            raise _error(operation, "MOTHER_STATE_PRIVATE_STATE_MISSING", "private-state commit manifest is absent", retry_class="after-reobserve")
        _validate_security(paths, operation)
        try:
            before = paths.recovery_manifest.read_bytes()
            result = _read_once(paths, operation, before)
            after = paths.recovery_manifest.read_bytes()
        except MotherError:
            raise
        except OSError as exc:
            raise _error(operation, "MOTHER_STATE_PRIVATE_STATE_MISSING", "private-state commit manifest could not be read", retry_class="after-reobserve", cause=exc) from exc
        if before == after:
            return result
        last_mismatch = True
    if last_mismatch:
        raise _error(operation, "MOTHER_STATE_UNSTABLE_PRIVATE_STATE", "private-state commit manifest changed during verification", retry_class="after-reobserve")
    raise AssertionError("unreachable")


def resolve_validator_ref(
    private_state: PrivateStateReadResult,
    network: str,
    node_id: str,
    *,
    operation: OperationIdentity,
) -> ResolvedValidatorIdentity:
    operation = _operation(operation)
    if not isinstance(private_state, PrivateStateReadResult):
        raise TypeError("private_state must be PrivateStateReadResult")
    try:
        network_id = _identifier(network, "network")
        node = _identifier(node_id, "node_id")
        document = json.loads(private_state.canonical_object_bytes.decode("utf-8"))
        networks = document["networks"]
        if network_id not in networks or node not in networks[network_id]["nodes"]:
            raise KeyError("node")
        node_wire = networks[network_id]["nodes"][node]
        if type(node_wire) is not dict or set(node_wire) != {"validator_ref"}:
            raise ValueError("node shape")
        ref = node_wire["validator_ref"]
        if type(ref) is not str:
            raise ValueError("reference type")
        parts = ref.split(".")
        if len(parts) != 4 or parts[0] != "networks" or parts[2] != "validators":
            raise ValueError("reference shape")
        if parts[1] != network_id:
            raise LookupError("cross-network")
        validator_id = _identifier(parts[3], "validator_id")
        validators = networks[network_id]["validators"]
        if validator_id not in validators:
            raise KeyError("validator")
        validator = validators[validator_id]
        if type(validator) is not dict or set(validator) != {"address", "private_key"}:
            raise ValueError("validator shape")
        address = validator["address"]
        key = validator["private_key"]
        if type(address) is not str or len(address) != 42 or not address.startswith("0x") or address.lower() != address:
            raise ValueError("address")
        if type(key) is not str or len(key) != 66 or not key.startswith("0x") or key.lower() != key:
            raise ValueError("private key")
        int(address[2:], 16)
        private_key = bytes.fromhex(key[2:])
    except LookupError as exc:
        raise _error(operation, "MOTHER_STATE_PRIVATE_STATE_REFERENCE_MISMATCH", "validator reference crosses the requested network", cause=exc) from exc
    except KeyError as exc:
        raise _error(operation, "MOTHER_STATE_PRIVATE_STATE_REFERENCE_MISMATCH", "validator reference does not resolve", cause=exc) from exc
    except (TypeError, ValueError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise _error(operation, "MOTHER_STATE_MALFORMED_PRIVATE_STATE", "validator reference is malformed", cause=exc) from exc
    return ResolvedValidatorIdentity(ref, address, private_key)


def _verify_result(private_state: PrivateStateReadResult, operation: OperationIdentity) -> tuple[bytes, bytes]:
    metadata_bytes = _metadata_bytes(private_state.metadata)
    manifest_bytes = _manifest_bytes(private_state.recovery_manifest)
    if private_state.document_bytes != canonical_yaml(json.loads(private_state.canonical_object_bytes.decode("utf-8"))):
        raise _error(operation, "MOTHER_STATE_PRIVATE_STATE_REFERENCE_MISMATCH", "private-state document bytes were altered")
    if private_state.binding != PrivateStateBinding(
        private_state_kind=private_state.metadata.private_state_kind,
        generation=private_state.metadata.generation,
        content_hash=private_state.metadata.content_hash,
        recovery_manifest_hash=private_state.metadata.recovery_manifest_hash,
    ):
        raise _error(operation, "MOTHER_STATE_PRIVATE_STATE_REFERENCE_MISMATCH", "private-state binding was altered")
    if sha256(private_state.document_bytes) != private_state.binding.content_hash or sha256(manifest_bytes) != private_state.binding.recovery_manifest_hash:
        raise _error(operation, "MOTHER_STATE_PRIVATE_STATE_REFERENCE_MISMATCH", "private-state binding hashes do not match")
    if tuple(obj.relative_path for obj in private_state.recovery_objects) != tuple(entry.relative_path for entry in private_state.recovery_manifest.entries):
        raise _error(operation, "MOTHER_STATE_PRIVATE_STATE_REFERENCE_MISMATCH", "private recovery object order does not match")
    for entry, obj in zip(private_state.recovery_manifest.entries, private_state.recovery_objects):
        if (obj.relative_path, obj.generation, obj.content_hash, len(obj.payload), sha256(obj.payload)) != (
            entry.relative_path, entry.generation, entry.content_hash, entry.byte_length, entry.content_hash
        ):
            raise _error(operation, "MOTHER_STATE_PRIVATE_STATE_REFERENCE_MISMATCH", "private recovery object was altered")
    return metadata_bytes, manifest_bytes


def build_recovery_closure(
    private_state: PrivateStateReadResult,
    *,
    operation: OperationIdentity,
) -> PrivateRecoveryClosure:
    operation = _operation(operation)
    if not isinstance(private_state, PrivateStateReadResult):
        raise TypeError("private_state must be PrivateStateReadResult")
    metadata_bytes, manifest_bytes = _verify_result(private_state, operation)
    members = [sha256(private_state.document_bytes), sha256(metadata_bytes), sha256(manifest_bytes)]
    members.extend(sha256(canonical_json(_entry_wire(entry))) for entry in private_state.recovery_manifest.entries)
    return PrivateRecoveryClosure(
        source_paths=private_state.paths,
        document_bytes=private_state.document_bytes,
        metadata_bytes=metadata_bytes,
        recovery_manifest_bytes=manifest_bytes,
        recovery_objects=private_state.recovery_objects,
        binding=private_state.binding,
        closure_hash=ordered_root(members),
    )


def _verify_closure(closure: PrivateRecoveryClosure, operation: OperationIdentity) -> None:
    if not isinstance(closure, PrivateRecoveryClosure):
        raise TypeError("closure must be PrivateRecoveryClosure")
    try:
        document, canonical_object = _parse_document(closure.document_bytes, operation)
        del document, canonical_object
        metadata = _parse_metadata(closure.metadata_bytes, operation)
        manifest = _parse_manifest(closure.recovery_manifest_bytes, operation)
    except MotherError as exc:
        raise _error(operation, "MOTHER_STATE_PRIVATE_STATE_REFERENCE_MISMATCH", "private recovery closure is malformed", cause=exc) from exc
    expected_binding = PrivateStateBinding(metadata.private_state_kind, metadata.generation, metadata.content_hash, metadata.recovery_manifest_hash)
    if closure.binding != expected_binding or sha256(closure.document_bytes) != metadata.content_hash or sha256(closure.recovery_manifest_bytes) != metadata.recovery_manifest_hash:
        raise _error(operation, "MOTHER_STATE_PRIVATE_STATE_REFERENCE_MISMATCH", "private recovery closure binding does not match")
    if tuple(obj.relative_path for obj in closure.recovery_objects) != tuple(entry.relative_path for entry in manifest.entries):
        raise _error(operation, "MOTHER_STATE_PRIVATE_STATE_REFERENCE_MISMATCH", "private recovery closure members do not match")
    for entry, obj in zip(manifest.entries, closure.recovery_objects):
        if obj.generation != entry.generation or obj.content_hash != entry.content_hash or len(obj.payload) != entry.byte_length or sha256(obj.payload) != entry.content_hash:
            raise _error(operation, "MOTHER_STATE_PRIVATE_STATE_REFERENCE_MISMATCH", "private recovery closure object does not match")
    members = [sha256(closure.document_bytes), sha256(closure.metadata_bytes), sha256(closure.recovery_manifest_bytes)]
    members.extend(sha256(canonical_json(_entry_wire(entry))) for entry in manifest.entries)
    if ordered_root(members) != closure.closure_hash:
        raise _error(operation, "MOTHER_STATE_PRIVATE_STATE_REFERENCE_MISMATCH", "private recovery closure root does not match")


def _secure_private_path(
    path: Path,
    *,
    is_directory: bool,
    operation: OperationIdentity,
) -> None:
    if _is_windows():
        try:
            _set_windows_private_security(path, is_directory=is_directory)
            snapshot = _windows_security_snapshot(path)
            _validate_windows_snapshot(
                snapshot,
                expected_directory=is_directory,
            )
        except (OSError, ValueError) as exc:
            raise _error(
                operation,
                "MOTHER_STATE_PRIVATE_STATE_PERMISSION",
                "private-state Windows security could not be established",
                cause=exc,
            ) from exc
        return

    try:
        metadata = path.lstat()
        expected_type = stat.S_ISDIR if is_directory else stat.S_ISREG
        if stat.S_ISLNK(metadata.st_mode) or not expected_type(metadata.st_mode):
            raise ValueError("unsafe private-state filesystem type")
        if metadata.st_uid != os.geteuid():
            raise ValueError("unexpected private-state owner")
        path.chmod(0o700 if is_directory else 0o600)
        secured = path.lstat()
        if secured.st_uid != os.geteuid():
            raise ValueError("unexpected private-state owner")
        expected_mode = 0o700 if is_directory else 0o600
        if stat.S_IMODE(secured.st_mode) != expected_mode:
            raise ValueError("private-state mode could not be established")
    except (OSError, ValueError) as exc:
        raise _error(
            operation,
            "MOTHER_STATE_PRIVATE_STATE_PERMISSION",
            "private-state POSIX security could not be established",
            cause=exc,
        ) from exc


def _secure_private_directories(
    paths: PrivateStatePaths,
    target_parent: Path,
    *,
    operation: OperationIdentity,
) -> None:
    directories = [
        paths.root,
        paths.recovery_objects_root.parent,
        paths.recovery_objects_root,
    ]
    try:
        relative = target_parent.relative_to(paths.recovery_objects_root)
    except ValueError as exc:
        raise _error(
            operation,
            "MOTHER_STATE_MALFORMED_PRIVATE_STATE",
            "private recovery target escapes the recovery root",
            cause=exc,
        ) from exc
    current = paths.recovery_objects_root
    for part in relative.parts:
        current = current / part
        directories.append(current)
    for directory in dict.fromkeys(directories):
        if directory.exists():
            _secure_private_path(
                directory,
                is_directory=True,
                operation=operation,
            )



def prepare_private_state_bootstrap(
    paths: PrivateStatePaths,
    document: dict[str, Any],
    *,
    updated_at: str,
    updated_by_action_id: str,
    operation: OperationIdentity,
) -> PrivateRecoveryClosure:
    """Build a verified generation-one closure from an operator source document.

    This function performs no filesystem writes.  It canonicalizes the supplied
    document, creates an empty recovery manifest, binds both objects into exact
    generation-one metadata, and returns a closure suitable for
    :func:`install_verified_private_state`.
    """

    operation = _operation(operation)
    _validate_paths(paths, operation)
    if type(document) is not dict:
        raise TypeError("document must be an exact dictionary")
    _text(updated_at, "updated_at")
    _text(updated_by_action_id, "updated_by_action_id")

    try:
        document_bytes = canonical_yaml(document)
    except (TypeError, ValueError) as exc:
        raise _error(
            operation,
            "MOTHER_STATE_MALFORMED_PRIVATE_STATE",
            "bootstrap private-state document is not canonicalizable",
            cause=exc,
        ) from exc

    # Reuse the production parser so bootstrap and steady-state reads enforce
    # exactly the same schema and canonical representation.
    _parse_document(document_bytes, operation)

    manifest = PrivateRecoveryManifest(
        manifest_version=_PRIVATE_MANIFEST_VERSION,
        private_state_generation=1,
        entries=(),
    )
    manifest_bytes = _manifest_bytes(manifest)
    content_hash = sha256(document_bytes)
    manifest_hash = sha256(manifest_bytes)
    metadata = PrivateStateMetadata(
        kind=_PRIVATE_METADATA_KIND,
        private_state_kind=_PRIVATE_STATE_KIND,
        generation=1,
        content_hash=content_hash,
        previous_content_hash=None,
        recovery_manifest_hash=manifest_hash,
        updated_at=updated_at,
        updated_by_action_id=updated_by_action_id,
    )
    metadata_bytes = _metadata_bytes(metadata)
    binding = PrivateStateBinding(
        private_state_kind=_PRIVATE_STATE_KIND,
        generation=1,
        content_hash=content_hash,
        recovery_manifest_hash=manifest_hash,
    )
    closure = PrivateRecoveryClosure(
        source_paths=paths,
        document_bytes=document_bytes,
        metadata_bytes=metadata_bytes,
        recovery_manifest_bytes=manifest_bytes,
        recovery_objects=(),
        binding=binding,
        closure_hash=ordered_root(
            (sha256(document_bytes), sha256(metadata_bytes), sha256(manifest_bytes))
        ),
    )
    _verify_closure(closure, operation)
    return closure



def prepare_private_state_successor(
    current: PrivateStateReadResult,
    document: dict[str, Any],
    *,
    updated_at: str,
    updated_by_action_id: str,
    operation: OperationIdentity,
) -> PrivateRecoveryClosure:
    """Build the next exact private-state generation without performing I/O.

    The predecessor bundle is embedded as private recovery material so a starter
    identity rotation remains locally recoverable after publication.
    """

    operation = _operation(operation)
    if not isinstance(current, PrivateStateReadResult):
        raise TypeError("current must be a PrivateStateReadResult")
    if type(document) is not dict:
        raise TypeError("document must be an exact dictionary")
    _text(updated_at, "updated_at")
    _text(updated_by_action_id, "updated_by_action_id")
    _verify_result(current, operation)

    try:
        document_bytes = canonical_yaml(document)
    except (TypeError, ValueError) as exc:
        raise _error(
            operation,
            "MOTHER_STATE_MALFORMED_PRIVATE_STATE",
            "successor private-state document is not canonicalizable",
            cause=exc,
        ) from exc
    _parse_document(document_bytes, operation)

    generation = current.binding.generation + 1
    prefix = f"predecessor/generation-{current.binding.generation:08d}"
    predecessor_objects: list[PrivateRecoveryObject] = [
        PrivateRecoveryObject(
            relative_path=f"{prefix}/identity.private.yaml",
            generation=generation,
            content_hash=sha256(current.document_bytes),
            payload=current.document_bytes,
        ),
        PrivateRecoveryObject(
            relative_path=f"{prefix}/identity.private.meta.json",
            generation=generation,
            content_hash=sha256(_metadata_bytes(current.metadata)),
            payload=_metadata_bytes(current.metadata),
        ),
        PrivateRecoveryObject(
            relative_path=f"{prefix}/private-recovery/manifest.json",
            generation=generation,
            content_hash=sha256(_manifest_bytes(current.recovery_manifest)),
            payload=_manifest_bytes(current.recovery_manifest),
        ),
    ]
    for item in current.recovery_objects:
        predecessor_objects.append(
            PrivateRecoveryObject(
                relative_path=f"{prefix}/private-recovery/objects/{item.relative_path}",
                generation=generation,
                content_hash=sha256(item.payload),
                payload=item.payload,
            )
        )
    recovery_objects = tuple(
        sorted(predecessor_objects, key=lambda item: item.relative_path.encode("utf-8"))
    )
    entries = tuple(
        PrivateRecoveryManifestEntry(
            relative_path=item.relative_path,
            generation=generation,
            content_hash=item.content_hash,
            byte_length=len(item.payload),
        )
        for item in recovery_objects
    )
    manifest = PrivateRecoveryManifest(
        manifest_version=_PRIVATE_MANIFEST_VERSION,
        private_state_generation=generation,
        entries=entries,
    )
    manifest_bytes = _manifest_bytes(manifest)
    content_hash = sha256(document_bytes)
    manifest_hash = sha256(manifest_bytes)
    metadata = PrivateStateMetadata(
        kind=_PRIVATE_METADATA_KIND,
        private_state_kind=_PRIVATE_STATE_KIND,
        generation=generation,
        content_hash=content_hash,
        previous_content_hash=current.binding.content_hash,
        recovery_manifest_hash=manifest_hash,
        updated_at=updated_at,
        updated_by_action_id=updated_by_action_id,
    )
    metadata_bytes = _metadata_bytes(metadata)
    binding = PrivateStateBinding(
        private_state_kind=_PRIVATE_STATE_KIND,
        generation=generation,
        content_hash=content_hash,
        recovery_manifest_hash=manifest_hash,
    )
    members = [sha256(document_bytes), sha256(metadata_bytes), sha256(manifest_bytes)]
    members.extend(sha256(canonical_json(_entry_wire(entry))) for entry in entries)
    closure = PrivateRecoveryClosure(
        source_paths=current.paths,
        document_bytes=document_bytes,
        metadata_bytes=metadata_bytes,
        recovery_manifest_bytes=manifest_bytes,
        recovery_objects=recovery_objects,
        binding=binding,
        closure_hash=ordered_root(members),
    )
    _verify_closure(closure, operation)
    return closure


def _starter_bundle_file_set(current: PrivateStateReadResult) -> set[Path]:
    expected = {
        current.paths.identity_file,
        current.paths.metadata_file,
        current.paths.recovery_manifest,
    }
    expected.update(
        current.paths.recovery_objects_root / PurePosixPath(item.relative_path)
        for item in current.recovery_objects
    )
    return expected


def replace_verified_starter_private_state(
    paths: PrivateStatePaths,
    closure: PrivateRecoveryClosure,
    expected_binding: PrivateStateBinding,
    *,
    operation: OperationIdentity,
) -> PrivateStateInstallResult:
    """Atomically replace a clean starter bundle with its exact successor.

    This deliberately refuses a Mother root containing any durable state beyond
    the committed private-state bundle. It is an initialization-time transition,
    not the general distributed private-state rotation protocol.
    """

    operation = _operation(operation)
    _validate_paths(paths, operation)
    if not isinstance(expected_binding, PrivateStateBinding):
        raise TypeError("expected_binding must be a PrivateStateBinding")
    _verify_closure(closure, operation)
    current = read_private_state(paths, operation=operation)
    if current.binding != expected_binding:
        raise _error(
            operation,
            "MOTHER_STATE_PRIVATE_STATE_CONFLICT",
            "observed private state does not match expected starter binding",
            retry_class="operator-decision",
        )
    if closure.binding.generation != current.binding.generation + 1:
        raise _error(
            operation,
            "MOTHER_STATE_PRIVATE_STATE_CONFLICT",
            "starter successor generation is not contiguous",
            retry_class="operator-decision",
        )
    successor_metadata = _parse_metadata(closure.metadata_bytes, operation)
    if successor_metadata.previous_content_hash != current.binding.content_hash:
        raise _error(
            operation,
            "MOTHER_STATE_PRIVATE_STATE_CONFLICT",
            "starter successor does not bind the installed predecessor",
            retry_class="operator-decision",
        )

    actual_files = {path for path in paths.root.rglob("*") if path.is_file()}
    if actual_files != _starter_bundle_file_set(current):
        raise _error(
            operation,
            "MOTHER_STATE_PRIVATE_STATE_CONFLICT",
            "Mother root contains non-starter durable state; starter replacement is forbidden",
            retry_class="operator-decision",
        )

    token = sha256(operation.operation_id.encode("utf-8")).digest[:16]
    stage_root = paths.root.parent / f".{paths.root.name}.starter-stage-{token}"
    backup_root = paths.root.parent / f".{paths.root.name}.starter-backup-{token}"
    if stage_root.exists() or backup_root.exists():
        raise _error(
            operation,
            "MOTHER_STATE_PRIVATE_STATE_CONFLICT",
            "starter rotation staging path already exists",
            retry_class="operator-decision",
        )
    stage_paths = PrivateStatePaths(
        root=stage_root,
        identity_file=stage_root / "identity.private.yaml",
        metadata_file=stage_root / "identity.private.meta.json",
        recovery_objects_root=stage_root / "private-recovery" / "objects",
        recovery_manifest=stage_root / "private-recovery" / "manifest.json",
    )

    swapped = False
    try:
        install_verified_private_state(stage_paths, closure, None, operation=operation)
        staged = read_private_state(stage_paths, operation=operation)
        if staged.binding != closure.binding:
            raise RuntimeError("staged starter successor did not verify")
        os.replace(paths.root, backup_root)
        try:
            os.replace(stage_root, paths.root)
            swapped = True
        except BaseException:
            os.replace(backup_root, paths.root)
            raise
        verified = read_private_state(paths, operation=operation)
        if verified.binding != closure.binding:
            raise RuntimeError("installed starter successor did not verify")
        shutil.rmtree(backup_root, ignore_errors=True)
        return PrivateStateInstallResult(
            True,
            closure.binding,
            sha256(closure.recovery_manifest_bytes),
        )
    except (MotherError, OSError, RuntimeError) as exc:
        if swapped and backup_root.exists():
            failed_root = paths.root.parent / f".{paths.root.name}.starter-failed-{token}"
            try:
                if failed_root.exists():
                    shutil.rmtree(failed_root)
                if paths.root.exists():
                    os.replace(paths.root, failed_root)
                os.replace(backup_root, paths.root)
                shutil.rmtree(failed_root, ignore_errors=True)
            except OSError:
                pass
        elif not paths.root.exists() and backup_root.exists():
            try:
                os.replace(backup_root, paths.root)
            except OSError:
                pass
        if stage_root.exists():
            shutil.rmtree(stage_root, ignore_errors=True)
        if isinstance(exc, MotherError):
            raise
        raise _error(
            operation,
            "MOTHER_STATE_PRIVATE_STATE_CONFLICT",
            "starter private-state replacement failed",
            retry_class="operator-decision",
            cause=exc,
        ) from exc

def install_verified_private_state(
    paths: PrivateStatePaths,
    closure: PrivateRecoveryClosure,
    expected_binding: PrivateStateBinding | None,
    *,
    operation: OperationIdentity,
) -> PrivateStateInstallResult:
    operation = _operation(operation)
    _validate_paths(paths, operation)
    if expected_binding is not None and not isinstance(expected_binding, PrivateStateBinding):
        raise TypeError("expected_binding must be PrivateStateBinding or None")
    _verify_closure(closure, operation)

    if paths.recovery_manifest.exists():
        current = read_private_state(paths, operation=operation)
        if expected_binding is not None and current.binding != expected_binding:
            raise _error(operation, "MOTHER_STATE_PRIVATE_STATE_CONFLICT", "observed private state does not match expected binding", retry_class="operator-decision")
        if current.binding == closure.binding:
            return PrivateStateInstallResult(False, closure.binding, sha256(closure.recovery_manifest_bytes))
        raise _error(operation, "MOTHER_STATE_PRIVATE_STATE_CONFLICT", "a different complete private state already exists", retry_class="operator-decision")
    if expected_binding is not None:
        raise _error(operation, "MOTHER_STATE_PRIVATE_STATE_CONFLICT", "expected private state is absent", retry_class="operator-decision")

    # Existing partial durable state is not safe to overwrite.
    partial_files = [path for path in paths.root.rglob("*") if path.is_file()] if paths.root.exists() else []
    if partial_files:
        raise _error(operation, "MOTHER_STATE_PRIVATE_STATE_CONFLICT", "incomplete private-state target already contains data", retry_class="operator-decision")

    paths.recovery_objects_root.mkdir(parents=True, exist_ok=True)
    _secure_private_directories(
        paths,
        paths.recovery_objects_root,
        operation=operation,
    )
    try:
        for obj in closure.recovery_objects:
            target = paths.recovery_objects_root / PurePosixPath(obj.relative_path)
            target.parent.mkdir(parents=True, exist_ok=True)
            _secure_private_directories(paths, target.parent, operation=operation)
            atomic_files.durable_create(target, obj.payload, operation=operation)
            _secure_private_path(target, is_directory=False, operation=operation)
        atomic_files.durable_create(paths.identity_file, closure.document_bytes, operation=operation)
        _secure_private_path(paths.identity_file, is_directory=False, operation=operation)
        atomic_files.durable_create(paths.metadata_file, closure.metadata_bytes, operation=operation)
        _secure_private_path(paths.metadata_file, is_directory=False, operation=operation)
        # Commit determinant is always published last.
        atomic_files.durable_create(paths.recovery_manifest, closure.recovery_manifest_bytes, operation=operation)
        _secure_private_path(paths.recovery_manifest, is_directory=False, operation=operation)
    except MotherError:
        raise
    return PrivateStateInstallResult(True, closure.binding, sha256(closure.recovery_manifest_bytes))


__all__ = [
    "PrivateRecoveryClosure",
    "PrivateRecoveryManifest",
    "PrivateRecoveryManifestEntry",
    "PrivateRecoveryObject",
    "PrivateStateInstallResult",
    "PrivateStateMetadata",
    "PrivateStateReadResult",
    "ResolvedValidatorIdentity",
    "build_recovery_closure",
    "install_verified_private_state",
    "prepare_private_state_bootstrap",
    "prepare_private_state_successor",
    "read_private_state",
    "replace_verified_starter_private_state",
    "resolve_validator_ref",
]
