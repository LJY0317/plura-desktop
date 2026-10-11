import ctypes
from pathlib import Path
import struct
import unittest
from unittest.mock import patch
import uuid

from plura_desktop.platforms.macos_volume import _decode_volume, volume_uuid


def reply(*, kind=b"apfs\0", flags=0x1000, fields=0x144000, identifier=None):
    identifier = identifier or uuid.UUID("12345678-1234-5678-1234-567812345678")
    return struct.pack("=7I", 52 + len(kind), 0x80000000, fields, 0, 0, 0, flags) + identifier.bytes + struct.pack("=iI", 8, len(kind)) + kind


class VolumeTests(unittest.TestCase):
    def test_same_uuid_format_as_existing_manifest(self):
        self.assertEqual(_decode_volume(reply()), "12345678-1234-5678-1234-567812345678")

    def test_rejects_incomplete_identity_and_nonlocal_filesystem(self):
        for data in (reply(fields=0x104000), reply(flags=0), reply(kind=b"hfs\0"),
                     reply(identifier=uuid.UUID(int=0)), b"", reply()[:52]):
            with self.subTest(data=data), self.assertRaises(RuntimeError):
                _decode_volume(data)

    def test_rejects_malformed_size_and_variable_reference(self):
        for position, value, format in ((0, 0, "=I"), (0, 300, "=I"),
                                       (44, -44, "=i"), (44, 100, "=i"), (48, 0, "=I"), (48, 17, "=I")):
            data = bytearray(reply())
            struct.pack_into(format, data, position, value)
            with self.subTest(position=position, value=value), self.assertRaises(RuntimeError):
                _decode_volume(data)

    def test_each_lookup_is_fresh_and_uses_only_volume_attributes(self):
        class Call:
            def __init__(self):
                self.calls = 0

            def __call__(self, path, attributes, output, size, options):
                self.calls += 1
                request = attributes._obj
                self_outer.assertEqual((request.bitmapcount, request.common, request.volume,
                                       request.directory, request.file, request.fork, options),
                                      (5, 0x80000000, 0x80144000, 0, 0, 0, 0))
                value = uuid.UUID(int=self.calls)
                data = reply(identifier=value)
                ctypes.memmove(output, data, len(data))
                return 0

        self_outer = self
        call = Call()
        with patch("plura_desktop.platforms.macos_volume.c.CDLL") as library:
            library.return_value.getattrlist = call
            self.assertNotEqual(volume_uuid(Path("/synthetic")), volume_uuid(Path("/synthetic")))
        self.assertEqual(call.calls, 2)

    def test_syscall_failure_does_not_fall_back_or_invent_identity(self):
        with patch("plura_desktop.platforms.macos_volume.c.CDLL") as library:
            library.return_value.getattrlist.return_value = -1
            with self.assertRaisesRegex(RuntimeError, "lookup failed"):
                volume_uuid(Path("/synthetic"))


if __name__ == "__main__":
    unittest.main()
