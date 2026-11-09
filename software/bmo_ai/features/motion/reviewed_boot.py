"""Two exact reviewed CDC boot variants; never normalize arbitrary Python.

The canonical legacy bridge boot and the earlier supervised bridge boot differ
only in their reviewed documentation. Both are retained in firmware/staging.
An installed variant must survive a candidate update byte for byte and its
actual hash remains part of commissioning and IMU profile provenance.
"""
import hashlib


CANONICAL_BOOT_SHA256 = '1540f755a400f40babdf0457481b77559855ee95018092901b42d44baeacdbea'
SUPERVISED_BOOT_SHA256 = 'f59e20f97feef6eab364bdb305cb3d7d0639c7f3aa88a0f5e3ff517912c122eb'
KNOWN_BOOT_SHA256 = frozenset((CANONICAL_BOOT_SHA256, SUPERVISED_BOOT_SHA256))


def reviewed_boot_sha256(raw):
    if type(raw) is not bytes:
        raise ValueError('unreviewed_boot')
    actual = hashlib.sha256(raw).hexdigest()
    if actual not in KNOWN_BOOT_SHA256:
        raise ValueError('unreviewed_boot')
    return actual
