"""Pinned preparation and inference summaries with no downloads or real models."""
from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import wave

from bmo_ai.features.input.wake_word import LocalWakeDetector, PHRASES
from bmo_ai import wake_assets
import test_character_wake_word as wake_fixtures


class WakeAssetTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(TemporaryDirectory()))
        for target in ('socket.socket.connect', 'subprocess.Popen', 'urllib.request.urlopen',
                'bmo_ai.cloud.create_client', 'bmo_ai.audio.record_wav', 'bmo_ai.audio.transcribe',
                'bmo_ai.fish_tts.synthesize_wav'):
            self.enterContext(patch(target, side_effect=AssertionError('External/device IO forbidden')))
        files = {}
        self.payloads = {}
        for name, item in wake_assets.FILES.items():
            payload = b'NOT A REAL MODEL: fixture '+name.encode()
            self.payloads[item['file']] = payload
            files[name] = dict(item, bytes=len(payload), sha256=sha256(payload).hexdigest())
        self.enterContext(patch.object(wake_assets, 'FILES', files))

    def fetch(self, url, limit):
        self.assertIn(wake_assets.REVISION, url)
        self.assertEqual(limit, 8*1024*1024)
        return self.payloads[url.rsplit('/', 1)[1]]

    def test_download_needs_explicit_flag_and_manifest_declares_only_english_candidate(self):
        folder = self.root/'models'
        with self.assertRaises(wake_assets.WakePreparationError) as error:
            wake_assets.prepare_candidate(folder, fetcher=self.fetch)
        self.assertEqual(error.exception.code, 'explicit_public_download_required')
        result = wake_assets.prepare_candidate(folder, allow_download=True, fetcher=self.fetch)
        self.assertEqual((result['downloaded'], result['reused']), (3, 0))
        manifest = json.loads((folder/'manifest.json').read_text())
        self.assertEqual(manifest['phrases'], ['Hey Beemo'])
        self.assertEqual(manifest['supported_phrases'], ['Hey Beemo'])
        self.assertEqual(manifest['requested_phrases'], list(PHRASES))
        self.assertEqual(manifest['korean_acceptance'], 'UNVERIFIED')
        for path in folder.iterdir(): self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        result = wake_assets.prepare_candidate(folder, fetcher=Mock(side_effect=AssertionError('No repeat download')))
        self.assertEqual((result['downloaded'], result['reused']), (0, 3))

    def test_download_hash_mismatch_and_existing_tampering_never_overwrite_assets(self):
        with self.assertRaises(wake_assets.WakePreparationError) as error:
            wake_assets.prepare_candidate(self.root/'bad', allow_download=True, fetcher=lambda *_: b'wrong')
        self.assertEqual(error.exception.code, 'public_model_hash_mismatch')
        folder = self.root/'models'
        wake_assets.prepare_candidate(folder, allow_download=True, fetcher=self.fetch)
        target = folder/'hey_beemo.onnx';target.write_bytes(b'preserve prior mismatching file')
        with self.assertRaises(wake_assets.WakePreparationError) as error:
            wake_assets.prepare_candidate(folder, allow_download=True, fetcher=self.fetch)
        self.assertEqual(error.exception.code, 'local_model_hash_mismatch')
        self.assertEqual(target.read_bytes(), b'preserve prior mismatching file')

    def test_model_cache_in_git_checkout_or_symlink_is_rejected(self):
        (self.root/'.git').mkdir()
        with self.assertRaises(wake_assets.WakePreparationError): wake_assets.prepare_candidate(self.root/'models')
        other = self.root/'outside';other.mkdir()
        link = self.root/'linked';link.symlink_to(other, target_is_directory=True)
        with self.assertRaises(wake_assets.WakePreparationError): wake_assets.prepare_candidate(link)

    def test_subset_manifest_loads_without_claiming_other_requested_phrases_or_acoustic_pass(self):
        fixture = wake_fixtures.WakeManifestTests('test_checked_custom_model_adapter_uses_int16_80ms_chunks')
        fixture.setUp();self.addCleanup(fixture.doCleanups)
        fixture.data.update(phrases=['Hey Beemo'], supported_phrases=['Hey Beemo'])
        fixture.write()
        detector = LocalWakeDetector(fixture.path, loader=fixture.loader)
        self.addCleanup(detector.close)
        self.assertTrue(detector.load())
        state = detector.snapshot()
        self.assertEqual(state['supported_phrases'], ['Hey Beemo'])
        self.assertEqual(state['unverified_phrases'], ['Hey BMO', '헤이 비모'])
        self.assertEqual(state['acoustic_acceptance'], 'NOT_RUN')
        self.assertFalse(detector.feed(b'\0\0'*1280));self.assertTrue(detector.feed(b'\0\0'*1280))
        self.assertEqual(detector.snapshot()['last_score'], .9)
        detector.reset();self.assertIsNone(detector.snapshot()['last_score'])
        fixture.data.update(phrases=['hey jarvis'], supported_phrases=['hey jarvis']);fixture.write()
        self.assertFalse(detector.load());self.assertEqual(detector.snapshot()['status'], 'MODEL_INVALID')

    def detector(self):
        class Detector:
            closed = False
            last_score = None
            def __init__(self, _manifest): pass
            def load(self): return True
            def reset(self): self.last_score = None
            def feed(self, _pcm): self.last_score = .01;return False
            def snapshot(self):
                return dict(status='READY', last_score=self.last_score, model_sha256='a'*64,
                    execution_providers={'classifier': ['INJECTED_TEST_ONLY']},
                    supported_phrases=['Hey Beemo'], requested_phrases=list(PHRASES),
                    unverified_phrases=['Hey BMO', '헤이 비모'])
            def close(self): self.closed = True
        return Detector

    def wav(self, *, channels=1):
        path = self.root/'private.wav'
        with wave.open(str(path), 'wb') as output:
            output.setnchannels(channels);output.setsampwidth(2);output.setframerate(16000)
            output.writeframes(b'\0\0'*1600*channels)
        path.chmod(0o600)
        return path

    def test_single_file_inference_summary_has_bounded_scores_and_no_acoustic_claim(self):
        path = self.wav()
        with patch.object(wake_assets, 'LocalWakeDetector', self.detector()), \
             patch.object(wake_assets.importlib.metadata, 'version', return_value='TEST_FIXTURE'):
            result = wake_assets.infer_wav(self.root/'manifest.json', path)
        self.assertEqual(result['status'], 'OPERATIONAL_INFERENCE_PASS')
        self.assertEqual(result['activations'], 0);self.assertEqual(result['peak_score'], .01)
        self.assertEqual(len(result['windows']), 1);self.assertEqual(result['buffered_tail_samples'], 320)
        self.assertEqual((result['microphone_opens'], result['playback_calls'], result['network_audio_uploads']), (0, 0, 0))
        self.assertFalse(result['accuracy_evaluated']);self.assertEqual(result['human_pronunciation'], 'NOT_RUN')
        self.assertEqual(result['korean_pronunciation'], 'UNVERIFIED')
        self.assertNotIn(str(self.root), json.dumps(result))

    def test_wav_count_and_format_caps_fail_before_audio_source_or_network(self):
        with self.assertRaises(wake_assets.WakePreparationError):
            wake_assets.inspect_saved_wavs(self.root/'manifest.json', [self.root/'x.wav']*4)
        path = self.wav(channels=2)
        with patch.object(wake_assets, 'LocalWakeDetector', self.detector()), \
             patch.object(wake_assets.importlib.metadata, 'version', return_value='TEST_FIXTURE'):
            with self.assertRaises(wake_assets.WakePreparationError) as error:
                wake_assets.inspect_saved_wavs(self.root/'manifest.json', [path])
        self.assertEqual(error.exception.code, 'saved_wav_format_unsupported')

    def test_report_is_private_exclusive_and_preserves_prior_receipt(self):
        with patch.object(wake_assets.secrets, 'token_hex', return_value='fixed'):
            name = wake_assets.save_private_report(self.root/'reports', {'status': 'TEST_ONLY'})
            with self.assertRaises(FileExistsError): wake_assets.save_private_report(self.root/'reports', {'status': 'overwrite'})
        path = self.root/'reports'/name
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(json.loads(path.read_text()), {'status': 'TEST_ONLY'})


if __name__ == '__main__': unittest.main()
