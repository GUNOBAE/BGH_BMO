"""Permission-only repair of the three existing reviewed public game assets."""
from contextlib import redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from bmo_ai.features.contracts import FeatureError
from bmo_ai.features.games import packaged
from bmo_ai.game_pack_prepare import prepare_game_pack, main


class GamePackPrepareTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory(prefix='bmo-game-repair-')))
        self.pack = self.root/'public-games'
        self.pack.mkdir()
        self.names = ('manifest.json','engines.js','LICENSE.txt')
        for name in self.names:
            shutil.copyfile(packaged.PACK_ROOT/name, self.pack/name)
            (self.pack/name).chmod(0o664)
        self.original = {name:(self.pack/name).read_bytes() for name in self.names}

    def test_same_pi_permissions_fail_loader_but_default_audit_is_read_only(self):
        with self.assertRaises(FeatureError) as caught: packaged.load_bundle(root=self.pack)
        self.assertEqual(caught.exception.code, 'games_bundle_permissions')
        report = prepare_game_pack(root=self.pack)
        self.assertTrue(report['dry_run'])
        self.assertEqual(report['status'], 'REPAIR_REQUIRED')
        self.assertTrue(all(row['original_mode']=='0664' and row['desired_mode']=='0644' for row in report['files']))
        self.assertEqual({(self.pack/name).stat().st_mode & 0o777 for name in self.names},{0o664})
        self.assertEqual(self.original,{name:(self.pack/name).read_bytes() for name in self.names})

    def test_explicit_apply_changes_only_write_bits_three_hash_verified_assets(self):
        unrelated = self.pack/'unrelated.txt'
        unrelated.write_text('keep me');unrelated.chmod(0o666)
        report = prepare_game_pack(root=self.pack, apply=True)
        self.assertFalse(report['dry_run'])
        self.assertEqual(report['status'],'REPAIRED')
        self.assertEqual(tuple(report['catalog_ids']), packaged.GAME_IDS)
        self.assertFalse(report['source_bytes_changed'] or report['recursive'] or report['executed_games'])
        self.assertEqual({(self.pack/name).stat().st_mode & 0o777 for name in self.names},{0o644})
        self.assertEqual(unrelated.stat().st_mode & 0o777,0o666)
        self.assertEqual(self.original,{name:(self.pack/name).read_bytes() for name in self.names})
        self.assertEqual(prepare_game_pack(root=self.pack,apply=True)['status'],'READY')
        self.assertEqual(tuple(item.id for item in packaged.load_bundle(root=self.pack).catalog),packaged.GAME_IDS)

    def test_invalid_last_asset_prevents_every_permission_change(self):
        (self.pack/'LICENSE.txt').write_bytes(b'altered')
        with self.assertRaises(FeatureError) as caught: prepare_game_pack(root=self.pack,apply=True)
        self.assertEqual(caught.exception.code,'game_asset_hash_mismatch')
        self.assertEqual({(self.pack/name).stat().st_mode & 0o777 for name in self.names},{0o664})

    def test_manifest_pin_is_not_accepted_from_arguments_or_recomputed_after_edit(self):
        path = self.pack/'manifest.json'
        manifest = json.loads(path.read_bytes())
        raw = b'injected';(self.pack/'engines.js').write_bytes(raw)
        manifest['files']['engines.js'] = hashlib.sha256(raw).hexdigest()
        path.write_text(json.dumps(manifest))
        with self.assertRaises(FeatureError) as caught: prepare_game_pack(root=self.pack,apply=True)
        self.assertEqual(caught.exception.code,'game_manifest_hash_mismatch')
        self.assertTrue(all((self.pack/name).stat().st_mode & 0o022 for name in self.names))

    def test_symlink_hardlink_or_ancestor_symlink_has_no_permission_side_effect(self):
        for kind in ('symlink','hardlink','parent_symlink'):
            candidate = self.root/kind
            shutil.copytree(self.pack,candidate)
            source = candidate/'engines.js'
            target = self.root/(kind+'-target')
            if kind != 'parent_symlink':
                source.rename(target)
                if kind=='symlink': source.symlink_to(target)
                else: os.link(target,source)
            else:
                link = self.root/'linked-directory';link.symlink_to(candidate,target_is_directory=True)
                candidate = link
            with self.subTest(kind=kind),self.assertRaises(FeatureError):
                prepare_game_pack(root=candidate,apply=True)
            if kind != 'parent_symlink': self.assertEqual(target.stat().st_mode & 0o777,0o664)

    def test_missing_file_error_is_separate_from_permissions_and_hash(self):
        # Loader must reach the missing asset without a prior permission fault.
        for name in self.names: (self.pack/name).chmod(0o644)
        (self.pack/'LICENSE.txt').unlink()
        with self.assertRaises(FeatureError) as caught: packaged.load_bundle(root=self.pack)
        self.assertEqual(caught.exception.code,'game_bundle_missing')
        with self.assertRaises(FeatureError) as caught: prepare_game_pack(root=self.pack)
        self.assertEqual(caught.exception.code,'game_bundle_missing')

    def test_cli_requires_apply_and_reports_only_local_public_file_diagnostics(self):
        out = io.StringIO()
        with redirect_stdout(out): code = main(['--root',str(self.pack),'--dry-run'])
        self.assertEqual(code,0)
        self.assertTrue(json.loads(out.getvalue())['dry_run'])
        self.assertEqual((self.pack/'engines.js').stat().st_mode & 0o777,0o664)
        out = io.StringIO()
        with redirect_stdout(out): code = main(['--root',str(self.pack),'--apply'])
        self.assertEqual(code,0)
        self.assertEqual(json.loads(out.getvalue())['status'],'REPAIRED')
        self.assertEqual((self.pack/'engines.js').stat().st_mode & 0o777,0o644)


if __name__=='__main__': unittest.main()
