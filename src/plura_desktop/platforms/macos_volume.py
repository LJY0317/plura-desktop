"""Fresh APFS identity using Darwin's public getattrlist syscall; no subprocess/cache."""
from __future__ import annotations

import ctypes as c
import os
from pathlib import Path
import struct
import uuid

# Darwin sys/attr.h and sys/mount.h; kept inside the macOS adapter.
_RETURNED = 0x80000000
_MOUNTFLAGS = 0x00004000
_UUID = 0x00040000
_FSTYPENAME = 0x00100000
_INFO = 0x80000000
_LOCAL = 0x00001000
_VOLUME_FIELDS = _MOUNTFLAGS | _UUID | _FSTYPENAME


class _Attributes(c.Structure):
    _fields_ = [("bitmapcount", c.c_uint16), ("reserved", c.c_uint16),
                ("common", c.c_uint32), ("volume", c.c_uint32),
                ("directory", c.c_uint32), ("file", c.c_uint32), ("fork", c.c_uint32)]


def _decode_volume(data: bytes) -> str:
    # uint32 length + attribute_set_t + uint32 flags + uuid_t + attrreference_t.
    if len(data) < 52:
        raise RuntimeError("Filesystem identity response truncated")
    size, common, volume, directory, file, fork, flags = struct.unpack_from("=7I", data)
    if not 52 <= size <= len(data):
        raise RuntimeError("Filesystem identity response size invalid")
    if common != _RETURNED or volume & _VOLUME_FIELDS != _VOLUME_FIELDS or directory or file or fork:
        raise RuntimeError("Filesystem identity attributes unavailable")
    offset, length = struct.unpack_from("=iI", data, 44)
    start = 44 + offset
    if start < 52 or not 1 <= length <= 16 or start + length > size:
        raise RuntimeError("Filesystem type name invalid")
    if data[start:start + length] != b"apfs\0":
        raise RuntimeError("Managed path is not on APFS")
    if not flags & _LOCAL:
        raise RuntimeError("Managed path is not on a local disk volume")
    identifier = uuid.UUID(bytes=data[28:44])
    if identifier.int == 0:
        raise RuntimeError("APFS Volume UUID unavailable")
    return str(identifier).upper()


def volume_uuid(path: Path) -> str:
    try:
        libc = c.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
        call = libc.getattrlist
        call.argtypes = [c.c_char_p, c.POINTER(_Attributes), c.c_void_p, c.c_size_t, c.c_ulong]
        call.restype = c.c_int
        attributes = _Attributes(5, 0, _RETURNED, _INFO | _VOLUME_FIELDS, 0, 0, 0)
        buffer = c.create_string_buffer(256)
        # New syscall every lookup; volume and inode checks retain their existing lifecycle.
        if call(os.fsencode(path), c.byref(attributes), buffer, len(buffer), 0) != 0:
            raise OSError(c.get_errno(), "Filesystem identity syscall failed")
        return _decode_volume(buffer.raw)
    except (OSError, AttributeError, ValueError) as error:
        raise RuntimeError("APFS volume identity lookup failed") from error
