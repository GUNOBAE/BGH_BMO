"""Audit exact packaged public games; explicitly repair only write permission bits.

Default is read-only. --apply never changes source bytes, owner, directories,
unknown files or hashes, and is separate from the fail-closed runtime loader.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import stat

from .features.contracts import FeatureError
from .features.games.packaged import PACK_ROOT, MANIFEST_SHA256, FILES, load_bundle


_NAMES = ('manifest.json', 'engines.js', 'LICENSE.txt')


def _directory(path):
    """Pin an absolute directory without following any ancestor symlinks."""
    path = Path(path).absolute()
    if '..' in path.parts or '\0' in str(path):
        raise FeatureError('unsafe_game_bundle')
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        for part in path.parts[1:]:
            next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                              dir_fd=fd)
            os.close(fd)
            fd = next_fd
        return fd
    except BaseException:
        os.close(fd)
        raise


def _identity(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns,
            info.st_mode, info.st_uid, info.st_nlink)


def _read(fd, maximum):
    os.lseek(fd, 0, os.SEEK_SET)
    before = os.fstat(fd)
    if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
            or before.st_uid not in (0, os.geteuid()) or not 0 < before.st_size <= maximum):
        raise FeatureError('unsafe_game_bundle')
    raw = os.read(fd, maximum + 1)
    after = os.fstat(fd)
    if len(raw) != before.st_size or _identity(before) != _identity(after):
        raise FeatureError('game_bundle_changed')
    return raw, before


def prepare_game_pack(*, root=PACK_ROOT, apply=False):
    """Verify all three pins before optionally removing only0022 on their FDs."""
    if type(apply) is not bool:
        raise ValueError('literal_apply_required')
    directory = None
    opened = {}
    try:
        directory = _directory(root)
        expected = {'manifest.json': MANIFEST_SHA256}
        reports = []
        for name in _NAMES:
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                         dir_fd=directory)
            opened[name] = fd
            raw, info = _read(fd, 16384 if name == 'manifest.json' else 524288)
            digest = hashlib.sha256(raw).hexdigest()
            if digest != expected[name]:
                raise FeatureError('game_manifest_hash_mismatch' if name == 'manifest.json' else 'game_asset_hash_mismatch')
            if name == 'manifest.json':
                manifest = json.loads(raw)
                if set(manifest['files']) != FILES:
                    raise FeatureError('invalid_game_manifest')
                expected.update(manifest['files'])
            desired = stat.S_IMODE(info.st_mode) & ~0o0022
            reports.append(dict(file=name, sha256=digest, original_mode=format(stat.S_IMODE(info.st_mode), '04o'),
                desired_mode=format(desired, '04o'), needs_change=bool(info.st_mode & 0o0022)))
        needed = any(r['needs_change'] for r in reports)
        # Validate every pinned descriptor again before the first mutation. No
        # repair is attempted for another owner, even if a privileged process
        # could override it. Root-run inspection of user-owned files stays safe.
        for report in reports:
            fd = opened[report['file']]
            raw, info = _read(fd, 524288)
            linked = os.stat(report['file'], dir_fd=directory, follow_symlinks=False)
            if _identity(info) != _identity(linked) or hashlib.sha256(raw).hexdigest() != report['sha256']:
                raise FeatureError('game_bundle_changed')
            if apply and report['needs_change'] and info.st_uid != os.geteuid():
                raise FeatureError('games_bundle_owner_repair_required')
        if apply:
            for report in reports:
                if report['needs_change']:
                    os.fchmod(opened[report['file']], int(report['desired_mode'], 8))
            for report in reports:
                raw, info = _read(opened[report['file']], 524288)
                if (hashlib.sha256(raw).hexdigest() != report['sha256'] or info.st_mode & 0o0022):
                    raise FeatureError('game_bundle_changed')
            bundle = load_bundle(root=root)
            return dict(status='REPAIRED' if needed else 'READY', dry_run=False,
                files=reports, catalog_ids=[item.id for item in bundle.catalog],
                source_bytes_changed=False, recursive=False, executed_games=False)
        return dict(status='REPAIR_REQUIRED' if needed else 'READY', dry_run=True,
            files=reports, catalog_ids=([item.id for item in load_bundle(root=root).catalog] if not needed else []),
            source_bytes_changed=False, recursive=False,
            executed_games=False, apply_argv=(['python', '-m', 'bmo_ai.game_pack_prepare',
                '--root', str(Path(root).absolute()), '--apply'] if needed else None))
    except FeatureError:
        raise
    except FileNotFoundError:
        raise FeatureError('game_bundle_missing') from None
    except PermissionError:
        raise FeatureError('games_bundle_permissions') from None
    except (OSError, ValueError, KeyError, TypeError):
        raise FeatureError('unsafe_game_bundle') from None
    finally:
        for fd in opened.values():
            os.close(fd)
        if directory is not None:
            os.close(directory)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', default=str(PACK_ROOT))
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--dry-run', action='store_true', help='Read-only audit (default).')
    mode.add_argument('--apply', action='store_true', help='Remove group/other write on only three verified public game assets.')
    args = parser.parse_args(argv)
    try:
        print(json.dumps(prepare_game_pack(root=args.root, apply=args.apply), sort_keys=True))
        return 0
    except FeatureError as error:
        print(json.dumps(dict(status='BLOCKED', code=error.code, dry_run=not args.apply,
                              source_bytes_changed=False, executed_games=False)))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
