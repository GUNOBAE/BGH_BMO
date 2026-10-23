"""Synthetic temporary stores only; never inspect a production credential path."""
from dataclasses import FrozenInstanceError
import fcntl
import getpass
import json
import os
from pathlib import Path
import stat
import tempfile
from threading import Event, Thread
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import warnings

from bmo_ai import private_credentials as private
from bmo_ai.private_credentials import CredentialError, Credentials, resolve_credentials


OLD_OPENAI = "fixture-openai-old"
OLD_FISH = "fixture-fish-old"
NEW_OPENAI = "fixture-openai-new"
NEW_FISH = "fixture-fish-new"


class PrivateCredentialTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="bmo-credential-fixture-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.directory = self.root / "private-bmo"
        self.path = self.directory / "credentials.json"
        for target in ("socket.socket.connect", "socket.socket.connect_ex", "subprocess.Popen"):
            self.enterContext(patch(target, side_effect=AssertionError("External operation forbidden")))

    def resolve(self, **kwargs):
        defaults = dict(config_dir=self.directory, environ={}, interactive=False)
        defaults.update(kwargs)
        return resolve_credentials(**defaults)

    def stored(self, **values):
        self.directory.mkdir(mode=0o700, exist_ok=True)
        self.path.write_text(json.dumps({"version": 1, **values}), encoding="utf-8")
        self.path.chmod(0o600)
        return self.path.read_bytes()

    def complete_store(self):
        return self.stored(openai_api_key=OLD_OPENAI, fish_api_key=OLD_FISH)

    def assertSafeError(self, operation, code):
        with self.assertRaises(CredentialError) as caught:
            operation()
        error = caught.exception
        self.assertEqual(error.code, code)
        self.assertEqual(str(error), error.safe_message)
        self.assertEqual(str(error), private._ERRORS[code])
        for forbidden in (str(self.root), OLD_OPENAI, OLD_FISH, NEW_OPENAI, NEW_FISH, "private-details"):
            self.assertNotIn(forbidden, str(error))
            self.assertNotIn(forbidden, repr(error))
        return error

    def assertNoTemporaryKeys(self):
        if self.directory.exists():
            self.assertEqual(list(self.directory.glob(".credentials.*.tmp")), [])

    def test_environment_complete_never_opens_or_creates_any_store(self):
        with patch.object(private, "_open_directory", side_effect=AssertionError("Storage must stay closed")):
            credentials = self.resolve(environ={"OPENAI_API_KEY": NEW_OPENAI, "FISH_API_KEY": NEW_FISH})
        self.assertEqual((credentials.openai_api_key, credentials.fish_api_key), (NEW_OPENAI, NEW_FISH))
        self.assertFalse(self.directory.exists())

    def test_first_prompt_saves_once_with_private_directory_and_file(self):
        prompt = Mock(side_effect=[NEW_OPENAI, NEW_FISH])
        credentials = self.resolve(interactive=True, input_secret=prompt)
        self.assertEqual(credentials, Credentials(NEW_OPENAI, NEW_FISH))
        self.assertEqual(prompt.call_count, 2)
        self.assertEqual(stat.S_IMODE(self.directory.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)
        self.assertEqual(json.loads(self.path.read_bytes()),
                         {"version": 1, "openai_api_key": NEW_OPENAI, "fish_api_key": NEW_FISH})
        self.assertNoTemporaryKeys()

    def test_saved_credentials_reuse_without_prompt_or_modification(self):
        before = self.complete_store()
        prompt = Mock(side_effect=AssertionError("No prompt needed"))
        credentials = self.resolve(input_secret=prompt)
        self.assertEqual(credentials, Credentials(OLD_OPENAI, OLD_FISH))
        self.assertEqual(self.path.read_bytes(), before)
        self.assertFalse((self.directory / ".credentials.lock").exists())
        prompt.assert_not_called()

    def test_partial_environment_persists_only_newly_prompted_missing_key(self):
        prompt = Mock(return_value=NEW_FISH)
        credentials = self.resolve(environ={"OPENAI_API_KEY": NEW_OPENAI},
                                   interactive=True, input_secret=prompt)
        self.assertEqual(credentials, Credentials(NEW_OPENAI, NEW_FISH))
        self.assertEqual(prompt.call_count, 1)
        self.assertEqual(json.loads(self.path.read_bytes()), {"version": 1, "fish_api_key": NEW_FISH})
        self.assertNotIn(NEW_OPENAI.encode(), self.path.read_bytes())
        self.assertEqual(self.resolve(environ={"OPENAI_API_KEY": NEW_OPENAI}), credentials)

    def test_environment_overrides_saved_values_without_persisting_environment(self):
        before = self.complete_store()
        credentials = self.resolve(environ={"OPENAI_API_KEY": NEW_OPENAI})
        self.assertEqual(credentials, Credentials(NEW_OPENAI, OLD_FISH))
        self.assertEqual(self.path.read_bytes(), before)

    def test_prompt_merges_with_partial_store_without_copying_environment_override(self):
        self.stored(openai_api_key=OLD_OPENAI)
        credentials = self.resolve(environ={"OPENAI_API_KEY": NEW_OPENAI}, interactive=True,
                                   input_secret=Mock(return_value=NEW_FISH))
        self.assertEqual(credentials, Credentials(NEW_OPENAI, NEW_FISH))
        self.assertEqual(json.loads(self.path.read_bytes()),
                         {"version": 1, "openai_api_key": OLD_OPENAI, "fish_api_key": NEW_FISH})

    def test_noninteractive_missing_fails_without_creating_directory_or_lock(self):
        prompt = Mock(side_effect=AssertionError("No interactive input"))
        self.assertSafeError(lambda: self.resolve(input_secret=prompt), "credentials_missing")
        self.assertFalse(self.directory.exists())
        self.directory.mkdir(mode=0o700)
        self.assertSafeError(lambda: self.resolve(input_secret=prompt), "credentials_missing")
        self.assertEqual(list(self.directory.iterdir()), [])
        prompt.assert_not_called()

    def test_force_prompt_replaces_both_keys_and_ignores_environment_values(self):
        self.complete_store()
        prompt = Mock(side_effect=[NEW_OPENAI, NEW_FISH])
        credentials = self.resolve(force_prompt=True, interactive=True, input_secret=prompt,
                                   environ={"OPENAI_API_KEY": "session-openai", "FISH_API_KEY": "session-fish"})
        self.assertEqual(credentials, Credentials(NEW_OPENAI, NEW_FISH))
        self.assertEqual(prompt.call_count, 2)
        self.assertEqual(self.resolve(), credentials)

    def test_force_prompt_requires_interactive_input_without_modifying_store(self):
        before = self.complete_store()
        self.assertSafeError(lambda: self.resolve(force_prompt=True), "input_required")
        self.assertEqual(self.path.read_bytes(), before)

    def test_interrupt_or_eof_on_second_prompt_preserves_original_bytes(self):
        before = self.complete_store()
        for failure, code in ((KeyboardInterrupt(), "cancelled"), (EOFError(), "input_unavailable"),
                              (RuntimeError("private-details"), "input_unavailable")):
            with self.subTest(failure=type(failure).__name__):
                self.assertSafeError(lambda: self.resolve(force_prompt=True, interactive=True,
                                     input_secret=Mock(side_effect=[NEW_OPENAI, failure])), code)
                self.assertEqual(self.path.read_bytes(), before)
                self.assertNoTemporaryKeys()

    def test_getpass_echo_fallback_warning_fails_before_fallback_can_read(self):
        fallback = Mock()

        def unsafe_getpass(prompt):
            warnings.warn("private-details", getpass.GetPassWarning)
            fallback()
            return NEW_OPENAI

        with patch.object(private.getpass, "getpass", side_effect=unsafe_getpass):
            self.assertSafeError(lambda: self.resolve(interactive=True), "input_unavailable")
        fallback.assert_not_called()
        self.assertFalse(self.path.exists())

    def test_empty_control_whitespace_nonascii_and_oversized_keys_never_persist(self):
        invalid = ("", " ", "fixture\nkey", "fixture\rkey", "fixture\0key", "fixture\tkey",
                   "fixture key", "fixture\x7fkey", "fixture한글", "x" * (private.MAX_KEY_BYTES + 1), None)
        for value in invalid:
            with self.subTest(kind=type(value).__name__, length=len(value) if isinstance(value, str) else None):
                self.assertSafeError(lambda: self.resolve(interactive=True,
                                     input_secret=Mock(return_value=value)), "invalid_key")
                self.assertFalse(self.path.exists())
                self.assertNoTemporaryKeys()

    def test_invalid_environment_key_is_rejected_without_store_fallback(self):
        self.complete_store()
        self.assertSafeError(lambda: self.resolve(environ={"OPENAI_API_KEY": "private-details\n"}), "invalid_key")

    def test_saved_schema_and_duplicate_json_are_strict_and_bounded(self):
        self.directory.mkdir(mode=0o700)
        cases = [
            b'{"version":1,"version":1}', b'{"version":true}', b'{"version":2}',
            b'[]', b'{"version":1,"unexpected":"private-details"}',
            b'{"version":1,"openai_api_key":null}', b'{"version":1,"openai_api_key":""}',
            b'{"version":1,"openai_api_key":"private-details\\n"}',
            b'{"version":1,"openai_api_key":"one","openai_api_key":"two"}',
            b'\xff', b'{"version":', b"x" * (private.MAX_STORE_BYTES + 1),
        ]
        for index, raw in enumerate(cases):
            with self.subTest(index=index):
                self.path.write_bytes(raw)
                self.path.chmod(0o600)
                self.assertSafeError(self.resolve, "invalid_store")
                self.assertEqual(self.path.read_bytes(), raw)

    def test_file_symlink_is_never_followed_or_overwritten(self):
        victim = self.root / "external-private-fixture"
        victim.write_bytes(b"private-details")
        self.directory.mkdir(mode=0o700)
        self.path.symlink_to(victim)
        self.assertSafeError(self.resolve, "unsafe_file")
        self.assertEqual(victim.read_bytes(), b"private-details")
        self.assertTrue(self.path.is_symlink())

    def test_directory_and_ancestor_symlinks_are_rejected(self):
        target = self.root / "target"
        target.mkdir(mode=0o700)
        self.directory.symlink_to(target, target_is_directory=True)
        self.assertSafeError(self.resolve, "unsafe_directory")
        self.assertEqual(list(target.iterdir()), [])
        self.assertSafeError(lambda: self.resolve(config_dir=self.directory / "nested"), "unsafe_directory")
        self.assertFalse((target / "nested").exists())

    def test_multiple_hardlinks_are_rejected(self):
        self.complete_store()
        os.link(self.path, self.directory / "another-name")
        self.assertSafeError(self.resolve, "unsafe_file")

    def test_nonregular_file_and_fifo_fail_without_waiting(self):
        self.directory.mkdir(mode=0o700)
        self.path.mkdir(mode=0o700)
        self.assertSafeError(self.resolve, "unsafe_file")
        self.path.rmdir()
        os.mkfifo(self.path, 0o600)
        self.assertSafeError(self.resolve, "unsafe_file")

    def test_unsafe_directory_and_file_modes_are_not_silently_repaired(self):
        before = self.complete_store()
        for mode in (0o644, 0o640, 0o400, 0o700):
            with self.subTest(mode=oct(mode)):
                self.path.chmod(mode)
                self.assertSafeError(self.resolve, "unsafe_file")
                self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), mode)
        self.path.chmod(0o600)
        self.directory.chmod(0o755)
        self.assertSafeError(self.resolve, "unsafe_directory")
        self.assertEqual(stat.S_IMODE(self.directory.stat().st_mode), 0o755)
        self.assertEqual(self.path.read_bytes(), before)

    def test_owner_is_verified_from_open_descriptor_for_directory_and_file(self):
        self.complete_store()
        original = private.os.fstat
        for target, code in (("directory", "unsafe_directory"), ("file", "unsafe_file")):
            def changed_owner(fd):
                info = original(fd)
                match = stat.S_ISDIR(info.st_mode) if target == "directory" else stat.S_ISREG(info.st_mode)
                if not match:
                    return info
                return SimpleNamespace(st_mode=info.st_mode, st_uid=os.geteuid() + 1,
                                       st_nlink=info.st_nlink, st_size=info.st_size)
            with self.subTest(target=target), patch.object(private.os, "fstat", side_effect=changed_owner):
                self.assertSafeError(self.resolve, code)

    def test_write_fsync_and_replace_failures_preserve_original_bytes(self):
        before = self.complete_store()
        for target in ("write", "fsync", "replace"):
            with self.subTest(target=target), patch.object(private.os, target,
                    side_effect=OSError("private-details")):
                self.assertSafeError(lambda: self.resolve(force_prompt=True, interactive=True,
                                     input_secret=Mock(side_effect=[NEW_OPENAI, NEW_FISH])), "store_write_failed")
            self.assertEqual(self.path.read_bytes(), before)
            self.assertNoTemporaryKeys()

    def test_interrupt_before_atomic_replace_preserves_original_bytes(self):
        before = self.complete_store()
        with patch.object(private.os, "write", side_effect=KeyboardInterrupt()):
            self.assertSafeError(lambda: self.resolve(force_prompt=True, interactive=True,
                                 input_secret=Mock(side_effect=[NEW_OPENAI, NEW_FISH])), "cancelled")
        self.assertEqual(self.path.read_bytes(), before)
        self.assertNoTemporaryKeys()

    def test_directory_fsync_failure_after_commit_can_leave_complete_new_file(self):
        self.complete_store()
        original = private.os.fsync

        def fail_directory(fd):
            if stat.S_ISDIR(os.fstat(fd).st_mode):
                raise OSError("private-details")
            return original(fd)

        with patch.object(private.os, "fsync", side_effect=fail_directory):
            self.assertSafeError(lambda: self.resolve(force_prompt=True, interactive=True,
                                 input_secret=Mock(side_effect=[NEW_OPENAI, NEW_FISH])), "store_write_failed")
        self.assertEqual(self.resolve(), Credentials(NEW_OPENAI, NEW_FISH))
        self.assertNoTemporaryKeys()

    def test_destination_swapped_to_symlink_during_input_is_not_overwritten(self):
        self.complete_store()
        victim = self.root / "external-fixture"
        victim.write_bytes(b"private-details")
        calls = []

        def prompt(label):
            calls.append(label)
            if len(calls) == 2:
                self.path.unlink()
                self.path.symlink_to(victim)
            return NEW_OPENAI if len(calls) == 1 else NEW_FISH

        self.assertSafeError(lambda: self.resolve(force_prompt=True, interactive=True,
                             input_secret=prompt), "unsafe_file")
        self.assertTrue(self.path.is_symlink())
        self.assertEqual(victim.read_bytes(), b"private-details")
        self.assertNoTemporaryKeys()

    def test_parallel_resolvers_prompt_once_and_second_reloads_committed_keys(self):
        entered, release, contended = Event(), Event(), Event()
        original = private.fcntl.flock
        first_prompt = Mock(side_effect=[NEW_OPENAI, NEW_FISH])
        second_prompt = Mock(side_effect=AssertionError("A second prompt must not happen"))
        results, errors = [], []

        def first(label):
            if first_prompt.call_count == 0:
                entered.set()
                if not release.wait(3):
                    raise AssertionError("Synthetic prompt gate timed out")
            return first_prompt(label)

        def flock(fd, operation):
            try:
                return original(fd, operation)
            except BlockingIOError:
                contended.set()
                raise

        def run(prompt):
            try:
                results.append(self.resolve(interactive=True, input_secret=prompt))
            except Exception as error:
                errors.append(error)

        threads = [Thread(target=run, args=(first,)), Thread(target=run, args=(second_prompt,))]
        with patch.object(private.fcntl, "flock", side_effect=flock):
            try:
                threads[0].start()
                self.assertTrue(entered.wait(1))
                threads[1].start()
                self.assertTrue(contended.wait(1))
            finally:
                release.set()
                for thread in threads:
                    if thread.ident is not None:
                        thread.join(timeout=3)
        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(errors, [])
        self.assertEqual(results, [Credentials(NEW_OPENAI, NEW_FISH)] * 2)
        self.assertEqual(first_prompt.call_count, 2)
        second_prompt.assert_not_called()
        self.assertNoTemporaryKeys()

    def test_held_setup_lock_times_out_without_prompt_or_partial_file(self):
        self.directory.mkdir(mode=0o700)
        fd = os.open(self.directory / ".credentials.lock", os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            prompt = Mock()
            with patch.object(private, "LOCK_TIMEOUT_SECONDS", 0):
                self.assertSafeError(lambda: self.resolve(interactive=True, input_secret=prompt), "credentials_busy")
            prompt.assert_not_called()
            self.assertFalse(self.path.exists())
        finally:
            os.close(fd)

    def test_unsafe_setup_lock_is_rejected(self):
        self.directory.mkdir(mode=0o700)
        victim = self.root / "unrelated-fixture"
        victim.write_bytes(b"private-details")
        (self.directory / ".credentials.lock").symlink_to(victim)
        self.assertSafeError(lambda: self.resolve(interactive=True,
                             input_secret=Mock(return_value=NEW_OPENAI)), "unsafe_file")
        self.assertEqual(victim.read_bytes(), b"private-details")

    def test_xdg_and_default_home_are_used_only_inside_explicit_fixture_roots(self):
        prompt = Mock(side_effect=[NEW_OPENAI, NEW_FISH])
        xdg = self.root / "xdg"
        credentials = self.resolve(config_dir=None, environ={"XDG_CONFIG_HOME": str(xdg)},
                                   interactive=True, input_secret=prompt)
        self.assertEqual(credentials, Credentials(NEW_OPENAI, NEW_FISH))
        self.assertTrue((xdg / "bmo" / "credentials.json").is_file())
        fake_home = self.root / "home"
        with patch.object(private.Path, "home", return_value=fake_home):
            self.resolve(config_dir=None, environ={}, interactive=True,
                         input_secret=Mock(side_effect=[NEW_OPENAI, NEW_FISH]))
        self.assertTrue((fake_home / ".config" / "bmo" / "credentials.json").is_file())

    def test_relative_xdg_and_invalid_options_fail_before_any_storage_access(self):
        bad = ({"config_dir": "relative"}, {"config_dir": self.root / ".." / "escape"},
               {"config_dir": None, "environ": {"XDG_CONFIG_HOME": "relative"}},
               {"config_dir": None, "environ": {"XDG_CONFIG_HOME": 4}},
               {"interactive": "yes"}, {"force_prompt": 1}, {"input_secret": 4}, {"environ": []})
        with patch.object(private, "_open_directory", side_effect=AssertionError("No storage access")):
            for kwargs in bad:
                with self.subTest(option_names=tuple(kwargs)):
                    self.assertSafeError(lambda: self.resolve(**kwargs), "invalid_config")

    def test_credential_repr_str_and_errors_never_expose_values(self):
        credentials = Credentials(NEW_OPENAI, NEW_FISH)
        for rendered in (repr(credentials), str(credentials), repr({"credentials": credentials})):
            self.assertNotIn(NEW_OPENAI, rendered)
            self.assertNotIn(NEW_FISH, rendered)
            self.assertIn("redacted", rendered)
        with self.assertRaises(FrozenInstanceError):
            credentials.openai_api_key = "changed"
        self.assertFalse(hasattr(credentials, "__dict__"))
        self.assertEqual(CredentialError("private-details").code, "store_unavailable")


if __name__ == "__main__":
    unittest.main()
