"""Private journal regression tests use only generated temporary databases."""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from bmo_ai.features.contracts import FeatureError
from bmo_ai.features.journal import ActionJournal


class FeatureJournalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="bmo-journal-fixture-")
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name) / "private"
        self.path = self.directory / "actions.sqlite"
        self.journals = []
        for target in ("socket.socket.connect", "subprocess.Popen"):
            self.enterContext(patch(target, side_effect=AssertionError("External operation forbidden")))

    def tearDown(self):
        for journal in self.journals:
            journal.close()

    def journal(self, directory=None):
        journal = ActionJournal(directory)
        self.journals.append(journal)
        return journal

    def intent(self, target="demo_fixture"):
        return {"name": "launch_game", "target": target}

    def test_default_is_memory_only_and_duplicate_does_not_reexecute(self):
        with patch("bmo_ai.features.journal._open_directory", side_effect=AssertionError("Disk must stay closed")):
            journal = self.journal()
        self.assertEqual(journal.begin("req1", self.intent()),
                         {"status": "accepted", "code": "accepted", "duplicate": False})
        journal.finish("req1", "simulated", "mock_only")
        self.assertEqual(journal.begin("req1", self.intent()),
                         {"status": "simulated", "code": "mock_only", "duplicate": True})
        self.assertFalse(self.directory.exists())

    def test_same_logical_intent_has_canonical_fingerprint_and_conflict_is_fixed_error(self):
        journal = self.journal()
        journal.begin("req1", {"name": "launch_game", "target": "fixture"})
        self.assertTrue(journal.begin("req1", {"target": "fixture", "name": "launch_game"})["duplicate"])
        with self.assertRaises(FeatureError) as caught:
            journal.begin("req1", self.intent("different"))
        self.assertEqual(caught.exception.code, "request_conflict")
        self.assertNotIn("different", str(caught.exception))

    def test_optional_persistent_directory_and_database_are_private(self):
        journal = self.journal(self.directory)
        journal.begin("req1", self.intent())
        self.assertEqual(stat.S_IMODE(self.directory.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)
        self.assertEqual(self.path.stat().st_nlink, 1)

    def test_restart_marks_accepted_and_running_unknown_and_preserves_final_results(self):
        first = self.journal(self.directory)
        first.begin("accepted", self.intent())
        first.begin("running", self.intent()); first.finish("running", "running", "sent")
        first.begin("finished", self.intent()); first.finish("finished", "simulated", "mock_only")
        first.close()
        second = self.journal(self.directory)
        for request in ("accepted", "running"):
            result = second.begin(request, self.intent())
            self.assertEqual(result, {"status": "unknown", "code": "restart_unknown", "duplicate": True})
        self.assertEqual(second.begin("finished", self.intent()),
                         {"status": "simulated", "code": "mock_only", "duplicate": True})

    def test_database_contains_fingerprint_only_not_intent_transcript_key_or_path(self):
        journal = self.journal(self.directory)
        sensitive_fixture = {"name": "take_photo", "target": "fixture",
                             "speech": "private_fixture_transcript", "key": "private_fixture_key",
                             "path": "/private/fixture/path"}
        journal.begin("req1", sensitive_fixture)
        row = journal._db.execute("SELECT id,fingerprint,state,code FROM requests").fetchone()
        expected = hashlib.sha256(json.dumps(sensitive_fixture, sort_keys=True,
                                  separators=(",", ":")).encode()).hexdigest()
        self.assertEqual(row, ("req1", expected, "accepted", "accepted"))
        journal.close()
        raw = self.path.read_bytes()
        for value in ("private_fixture_transcript", "private_fixture_key", "/private/fixture/path", "take_photo"):
            self.assertNotIn(value.encode(), raw)

    def test_database_symlink_hardlink_and_world_readable_file_are_rejected(self):
        self.directory.mkdir(mode=0o700)
        victim = Path(self.temp.name) / "outside"
        victim.write_bytes(b"private fixture")
        self.path.symlink_to(victim)
        with self.assertRaises(FeatureError):
            ActionJournal(self.directory)
        self.assertEqual(victim.read_bytes(), b"private fixture")
        self.path.unlink()
        journal = self.journal(self.directory); journal.close()
        self.path.chmod(0o644)
        with self.assertRaises(FeatureError):
            ActionJournal(self.directory)
        self.path.chmod(0o600)
        os.link(self.path, self.directory / "hardlink")
        with self.assertRaises(FeatureError):
            ActionJournal(self.directory)

    def test_symlink_directory_and_relative_storage_are_rejected(self):
        actual = Path(self.temp.name) / "actual"
        actual.mkdir(mode=0o700)
        self.directory.symlink_to(actual, target_is_directory=True)
        for path in (self.directory, Path("relative")):
            with self.subTest(kind=str(path.name)), self.assertRaises(FeatureError) as caught:
                ActionJournal(path)
            self.assertEqual(caught.exception.code, "unsafe_journal")
            self.assertNotIn(str(self.temp.name), str(caught.exception))

    def test_corrupt_existing_database_is_sanitized_as_feature_error(self):
        self.directory.mkdir(mode=0o700)
        self.path.write_bytes(b"not SQLite private fixture bytes")
        self.path.chmod(0o600)
        with self.assertRaises(FeatureError) as caught:
            ActionJournal(self.directory)
        self.assertNotIn("private", str(caught.exception))
        self.assertNotIn(str(self.path), str(caught.exception))

    def test_unserializable_nonobject_nonfinite_and_unbounded_intents_are_rejected_safely(self):
        journal = self.journal()
        bad = (object(), "private string", ["private"], {1: "private", "name": "x"},
               {"value": float("nan")}, {"value": "x" * 65536})
        for index, intent in enumerate(bad):
            with self.subTest(index=index), self.assertRaises(FeatureError) as caught:
                journal.begin("bad" + str(index), intent)
            self.assertNotIn("private", str(caught.exception))

    def test_closed_journal_does_not_leak_attribute_or_sqlite_errors(self):
        journal = self.journal()
        journal.close()
        with self.assertRaises(FeatureError):
            journal.begin("req1", self.intent())
        with self.assertRaises(FeatureError):
            journal.finish("req1", "failed", "closed")

    def test_finishing_unregistered_id_is_rejected_instead_of_silently_losing_record(self):
        journal = self.journal()
        with self.assertRaises(FeatureError) as caught:
            journal.finish("unregistered", "simulated", "mock_only")
        self.assertEqual(caught.exception.code, "unknown_request")
        self.assertFalse(journal.begin("unregistered", self.intent())["duplicate"])

    def test_open_descriptor_ownership_is_verified_before_sqlite_use(self):
        self.directory.mkdir(mode=0o700)
        self.path.write_bytes(b"")
        self.path.chmod(0o600)
        original = os.fstat

        def foreign_file(fd):
            info = original(fd)
            if not stat.S_ISREG(info.st_mode):
                return info
            return SimpleNamespace(st_mode=info.st_mode, st_uid=os.geteuid() + 1,
                                   st_nlink=info.st_nlink, st_size=info.st_size)

        with patch("bmo_ai.features.journal.os.fstat", side_effect=foreign_file), self.assertRaises(FeatureError):
            ActionJournal(self.directory)
        self.assertEqual(self.path.read_bytes(), b"")

    def test_invalid_request_ids_and_status_codes_are_safe(self):
        journal = self.journal()
        for request in ("", "../private", "has whitespace", None):
            with self.subTest(request_type=type(request).__name__), self.assertRaises(FeatureError):
                journal.begin(request, self.intent())
        journal.begin("req1", self.intent())
        for status, code in (("unexpected", "ok"), ("failed", "private body")):
            with self.subTest(status=status), self.assertRaises(FeatureError):
                journal.finish("req1", status, code)


if __name__ == "__main__":
    unittest.main()
