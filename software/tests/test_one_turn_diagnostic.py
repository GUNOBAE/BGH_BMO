"""Opt-in private text capture; no API, device, audio or history source."""
from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import stat
import time
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from bmo_ai.desktop_app import main, parser
from bmo_ai.one_turn_diagnostic import OneTurnDiagnostic, OneTurnDiagnosticError, validate_destination


class OneTurnDiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(TemporaryDirectory()))
        self.path = self.root / 'one-turn.json'

    def diagnostic(self, path=None):
        value = OneTurnDiagnostic(path or self.path)
        self.addCleanup(value.close)
        return value

    def read(self):
        return json.loads(self.path.read_text())

    def test_private_one_turn_model_and_allowlisted_final_trace_without_history_or_public_text(self):
        value = self.diagnostic()
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)
        self.assertEqual(value.snapshot(), dict(armed=True, captured=False, complete=False, error_code=None))
        text = 'Get angry.'
        model = '{"speech":"A tiny theatrical protest!","emotion":"angry"}'
        self.assertTrue(value.record_model('session:1', text, model))
        self.assertTrue(value.record_trace('session:1', dict(
            turn=1, requested_emotion='angry', model_emotion='angry', resolved_emotion='angry',
            emitted_emotion='angry', validation='validated', degradation_reason='none',
            request_validation='validated', request_kind='acting', character_life=True,
            tts_mechanism='fish_character_s2_v1', tts_backend='fish', tts_override_soft=True,
            emission_status='ended', transcript='must not leak into trace',
            raw_provider={'untrusted':'not a trace'}, exception='/private/path',
            voice_style='a sentence that is not an enum')))
        data = self.read()
        self.assertEqual(data['transcript'], text)
        self.assertEqual(data['model_reply'], model)
        self.assertEqual(data['trace']['tts_override_soft'], True)
        self.assertFalse(data['trace']['contains_text'])
        self.assertNotIn('transcript', data['trace'])
        self.assertNotIn('raw_provider', data['trace'])
        self.assertNotIn('voice_style', data['trace'])
        self.assertNotIn('history', data)
        self.assertEqual(value.snapshot(), dict(armed=False, captured=True, complete=True, error_code=None))
        public = json.dumps(value.snapshot())
        for private in (text, model, str(self.path)):
            self.assertNotIn(private, public)

    def test_capture_is_consumed_once_and_later_turn_trace_cannot_replace_first(self):
        value = self.diagnostic()
        self.assertTrue(value.record_model('session:1', 'First only.', '{"emotion":"neutral"}'))
        original = self.path.read_bytes()
        self.assertFalse(value.record_model('session:2', 'Must not be saved.', 'another reply'))
        self.assertFalse(value.record_trace('session:2', dict(emitted_emotion='happy')))
        self.assertEqual(self.path.read_bytes(), original)
        self.assertTrue(value.record_trace('session:1', dict(emission_status='cancelled')))
        self.assertTrue(value.snapshot()['complete'])

    def test_atomic_updates_keep_complete_private_json_and_leave_no_temporary_files(self):
        value = self.diagnostic()
        value.record_model('session:1', 'One question.', '{"emotion":"angry"}')
        inode = self.path.stat().st_ino
        for stage in ('staged', 'started', 'ended'):
            self.assertTrue(value.record_trace('session:1', dict(emission_status=stage)))
            self.assertEqual(self.read()['trace']['emission_status'], stage)
            self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)
        self.assertNotEqual(inode, self.path.stat().st_ino)
        self.assertEqual(list(self.root.iterdir()), [self.path])

    def test_existing_destination_and_symlink_are_rejected_without_overwriting(self):
        self.path.write_bytes(b'Preserve this user file')
        with self.assertRaises(OneTurnDiagnosticError):
            self.diagnostic()
        self.assertEqual(self.path.read_bytes(), b'Preserve this user file')
        linked = self.root / 'linked.json'
        linked.symlink_to(self.path)
        with self.assertRaises(OneTurnDiagnosticError):
            self.diagnostic(linked)
        real = self.root / 'real'
        real.mkdir(mode=0o700)
        parent_link = self.root / 'linked-parent'
        parent_link.symlink_to(real, target_is_directory=True)
        with self.assertRaises(OneTurnDiagnosticError):
            self.diagnostic(parent_link / 'new.json')
        self.assertEqual(list(real.iterdir()), [])

    def test_checkout_and_nonprivate_parent_are_rejected(self):
        checkout = self.root / 'checkout'
        checkout.mkdir(mode=0o700)
        (checkout / '.git').mkdir()
        with self.assertRaises(OneTurnDiagnosticError) as error:
            self.diagnostic(checkout / 'capture.json')
        self.assertEqual(error.exception.code, 'diagnostic_outside_checkout_required')
        public = self.root / 'public'
        public.mkdir(mode=0o700)
        public.chmod(0o755)
        with self.assertRaises(OneTurnDiagnosticError) as error:
            self.diagnostic(public / 'capture.json')
        self.assertEqual(error.exception.code, 'diagnostic_private_parent_required')
        with self.assertRaises(OneTurnDiagnosticError):
            validate_destination(Path('relative.json'))

    def test_new_intermediate_directories_are_private(self):
        destination = self.root / 'first' / 'second' / 'turn.json'
        value = self.diagnostic(destination)
        for parent in (destination.parent, destination.parent.parent):
            self.assertEqual(stat.S_IMODE(parent.stat().st_mode), 0o700)
        self.assertTrue(value.record_model('session:1', 'Hello.', '{}'))

    def test_replaced_destination_and_write_error_are_safe_and_do_not_break_voice_caller(self):
        value = self.diagnostic()
        self.assertTrue(value.record_model('session:1', 'Hello.', '{}'))
        self.path.unlink()
        self.path.write_bytes(b'Replacement user file')
        self.path.chmod(0o600)
        self.assertFalse(value.record_trace('session:1', dict(emission_status='ended')))
        self.assertEqual(self.path.read_bytes(), b'Replacement user file')
        self.assertEqual(value.snapshot()['error_code'], 'diagnostic_write_failed')
        other = self.diagnostic(self.root / 'other.json')
        with patch('bmo_ai.one_turn_diagnostic.os.replace', side_effect=OSError('private error')):
            self.assertFalse(other.record_model('session:1', 'Hello.', '{}'))
        self.assertEqual(other.snapshot()['error_code'], 'diagnostic_write_failed')
        self.assertNotIn('private error', json.dumps(other.snapshot()))

    def test_invalid_record_does_not_capture_a_different_later_turn(self):
        value = self.diagnostic()
        self.assertFalse(value.record_model('bad turn identifier', 'Hello.', '{}'))
        self.assertFalse(value.record_model('session:2', 'Later.', '{}'))
        self.assertFalse(value.snapshot()['captured'])
        self.assertEqual(value.snapshot()['error_code'], 'diagnostic_invalid_record')

    def test_actual_host_feature_response_player_trace_captures_only_first_literal_turn(self):
        from bmo_ai.features.host import FeatureHost
        from bmo_ai.features.runtime import RuntimePolicy
        from bmo_ai.integration_profile import integration_config
        from bmo_ai.integration_voice import IntegrationVoice
        class Provider:
            def __init__(self):
                self.responses = self
                self.requests = []
            def create(self, **request):
                self.requests.append(request)
                token = request['text']['format']['schema']['properties']['request']['properties']['turn_token']['enum'][0]
                acting = request['input'][-1]['content'] == 'Get angry.'
                value = dict(version=4, speech='Hey! That was my cookie!' if acting else 'Four.',
                    emotion='neutral', gesture='none', intent=None, reaction='none',
                    character=dict(delivery_id='calm', intensity='medium', gaze_id='none', source='dialogue'),
                    request=dict(turn_token=token, kind='acting' if acting else 'none',
                        disposition='execute' if acting else 'none', emotion='angry' if acting else 'none',
                        subtitle_mode='unchanged', confirmation_token=None),
                    captions=dict(en='', ko=''), photo_success=None)
                return SimpleNamespace(status='completed', incomplete_details=None, error=None,
                    output=[], output_text=json.dumps(value))
        provider = Provider()
        config = integration_config()
        voice = IntegrationVoice(config, provider, simulate=True, expressive=True, character_life=True,
            emit=lambda *_a, **_kw: None,
            player=lambda _cmd, on_start, cancel_check, **_kw: (cancel_check(), on_start(), cancel_check()))
        with patch('subprocess.Popen', side_effect=AssertionError('No device/process IO')), \
                patch('socket.socket.connect', side_effect=AssertionError('No network')), \
                patch('bmo_ai.fish_tts.synthesize_wav', side_effect=AssertionError('No paid TTS')):
            host = FeatureHost(config=config, client=provider, voice=voice,
                runtime=RuntimePolicy(simulate=True), character_life=True, expressive_voice=True,
                one_turn_diagnostic=self.path)
            self.addCleanup(host.close)
            def finish():
                deadline = time.monotonic() + 4
                while time.monotonic() < deadline:
                    host.heartbeat()
                    host.poll()
                    if not host.session.busy and host.audio.owner is None:
                        return
                    time.sleep(.002)
                self.fail('Product turn did not finish')
            self.assertTrue(host.start_question('Get angry.'))
            finish()
            data = self.read()
            self.assertEqual(data['transcript'], 'Get angry.')
            self.assertEqual(json.loads(data['model_reply'])['emotion'], 'neutral')
            self.assertEqual(data['trace']['requested_emotion'], 'angry')
            self.assertEqual(data['trace']['model_emotion'], 'neutral')
            self.assertEqual(data['trace']['resolved_emotion'], 'angry')
            self.assertEqual(data['trace']['emitted_emotion'], 'angry')
            self.assertEqual(data['trace']['tts_mechanism'], 'fish_character_s2_v1')
            self.assertEqual(data['trace']['tts_backend'], 'synthetic')
            self.assertTrue(data['trace']['tts_override_soft'])
            self.assertTrue(data['trace']['character_life_enabled'])
            self.assertTrue(host.snapshot()['one_turn_diagnostic']['complete'])
            for private in ('Get angry.', 'Hey! That was my cookie!', str(self.path)):
                self.assertNotIn(private, json.dumps(host.snapshot()))
            first_record = self.path.read_bytes()
            self.assertTrue(host.start_question('What is two plus two?'))
            finish()
            self.assertEqual(len(provider.requests), 2)
            self.assertEqual(self.path.read_bytes(), first_record)


class OneTurnDiagnosticCliTests(unittest.TestCase):
    def test_status_and_help_do_not_create_or_validate_a_private_record(self):
        forbidden = patch('bmo_ai.one_turn_diagnostic.validate_destination', side_effect=AssertionError('No file inspection'))
        with forbidden, patch('os.open', side_effect=AssertionError('No file IO')), redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(['--status', '--character-life', '--one-turn-diagnostic', '/private/one.json']), 0)
            report = json.loads(output.getvalue())
            self.assertEqual(report['one_turn_diagnostic'], dict(requested=True, one_turn_only=True, file_created=False))
            self.assertNotIn('/private/one.json', output.getvalue())
        with redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as result:
            main(['--help'])
        self.assertEqual(result.exception.code, 0)

    def test_option_is_explicit_character_session_only(self):
        for args in (
            ['--status', '--one-turn-diagnostic', '/private/one.json'],
            ['--install-desktop', '--character-life', '--one-turn-diagnostic', '/private/one.json'],
            ['--live', '--prepare-greeting', '--character-life', '--one-turn-diagnostic', '/private/one.json'],
        ):
            with self.subTest(args=args), patch('os.open', side_effect=AssertionError('No launcher IO')), \
                    redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as result:
                main(args)
            self.assertEqual(result.exception.code, 2)
        self.assertEqual(parser().parse_args(['--one-turn-diagnostic', '/private/one.json']).one_turn_diagnostic,
                         Path('/private/one.json'))

    def test_invalid_path_fails_before_lock_browser_or_credentials(self):
        with patch('os.open', side_effect=AssertionError('No launcher or diagnostic writes')), \
                patch('bmo_ai.private_credentials.resolve_credentials', side_effect=AssertionError('No credentials')), \
                redirect_stderr(io.StringIO()) as error, self.assertRaises(SystemExit) as result:
            main(['--live', '--character-life', '--one-turn-diagnostic', 'relative.json'])
        self.assertEqual(result.exception.code, 2)
        self.assertIn('diagnostic_absolute_path_required', error.getvalue())


if __name__ == '__main__':
    unittest.main()
