"""Native, read-only Windows DACL verification for already-private auth files.

No ACL verdict is cached. An unknown ACL or API error requires the existing
repair and verification path. This module does not grant permissions.
"""
from __future__ import annotations

import os


def _exact_private_rules(control: int, rules: list[tuple[int, int, int, str]],
                         owner: str, *, directory: bool) -> bool:
    # Protected DACL, exactly three explicit access-allowed full-control ACEs.
    # Reject unknown/conditional/object/inherited ACEs, even if apparently safe.
    allowed = {owner, 'S-1-5-18', 'S-1-5-32-544'}
    flags = 3 if directory else 0  # OBJECT_INHERIT_ACE | CONTAINER_INHERIT_ACE
    return (bool(control & 0x1000) and len(rules) == len(allowed)
            and {sid for _, _, _, sid in rules} == allowed
            and all(kind == 0 and ace_flags == flags and mask == 0x1F01FF
                    for kind, ace_flags, mask, _ in rules))


def already_private(path, owner: str, *, directory: bool) -> bool:
    if os.name != 'nt':
        return False
    import ctypes as c
    from ctypes import wintypes as w

    # All pointers returned by the security descriptor are borrowed except
    # ConvertSidToStringSid's output. Free both owning allocations with LocalFree.
    api = c.WinDLL('advapi32', use_last_error=True)
    kernel = c.WinDLL('kernel32', use_last_error=True)
    ptr = c.c_void_p
    dword = w.DWORD
    local_free = kernel.LocalFree
    local_free.argtypes = [ptr]
    local_free.restype = ptr

    def bind(name, args, result=w.BOOL):
        fn = getattr(api, name)
        fn.argtypes = args
        fn.restype = result
        return fn

    get_sd = bind('GetNamedSecurityInfoW', [w.LPWSTR, c.c_int, dword,
                  c.POINTER(ptr), c.POINTER(ptr), c.POINTER(ptr),
                  c.POINTER(ptr), c.POINTER(ptr)], dword)
    get_control = bind('GetSecurityDescriptorControl', [ptr, c.POINTER(w.WORD), c.POINTER(dword)])
    get_acl = bind('GetAclInformation', [ptr, ptr, dword, c.c_int])
    get_ace = bind('GetAce', [ptr, dword, c.POINTER(ptr)])
    valid_sid = bind('IsValidSid', [ptr])
    sid_length = bind('GetLengthSid', [ptr], dword)
    sid_string = bind('ConvertSidToStringSidW', [ptr, c.POINTER(ptr)])
    class AclSize(c.Structure):
        _fields_ = [('count', dword), ('used', dword), ('free', dword)]
    class AceHeader(c.Structure):
        _fields_ = [('kind', c.c_ubyte), ('flags', c.c_ubyte), ('size', w.WORD)]

    dacl, sd = ptr(), ptr()
    try:
        # SE_FILE_OBJECT=1; DACL_SECURITY_INFORMATION=4.
        if get_sd(str(path), 1, 4, None, None, c.byref(dacl), None, c.byref(sd)) or not dacl:
            return False
        control, revision = w.WORD(), dword()
        if not get_control(sd, c.byref(control), c.byref(revision)) or not control.value & 0x1000:
            return False
        info = AclSize()
        if not get_acl(dacl, c.byref(info), c.sizeof(info), 2) or info.count != 3:
            return False
        rules = []
        for i in range(info.count):
            ace = ptr()
            if not get_ace(dacl, i, c.byref(ace)) or not ace:
                return False
            header = AceHeader.from_address(ace.value)
            if header.kind != 0 or header.size < 16:
                return False
            mask = dword.from_address(ace.value + 4).value
            sid = ptr(ace.value + 8)
            # SID has an 8-byte header followed by four bytes per subauthority.
            count = c.c_ubyte.from_address(sid.value + 1).value
            if 8 + 4 * count > header.size - 8 or not valid_sid(sid) or sid_length(sid) > header.size - 8:
                return False
            string = ptr()
            if not sid_string(sid, c.byref(string)) or not string:
                return False
            try:
                rules.append((header.kind, header.flags, mask, c.wstring_at(string)))
            finally:
                local_free(string)
        return _exact_private_rules(control.value, rules, owner, directory=directory)
    finally:
        if sd:
            local_free(sd)
