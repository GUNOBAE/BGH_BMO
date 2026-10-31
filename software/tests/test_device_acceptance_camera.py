"""Camera acceptance harness through the production Host; injected SDK only."""
import builtins
import json
from pathlib import Path
import stat
import tempfile
import time
import unittest
from unittest.mock import Mock, patch
from types import SimpleNamespace

from bmo_ai.device_acceptance.camera import (CameraAcceptanceError, _SdkEvidence,
    check_saved_frame_target_loss, run_camera_acceptance)
from bmo_ai.features.camera.live import LivePicamera2Backend
from bmo_ai.features.camera.codec import png_bytes
from bmo_ai.features.camera.backends import FixtureDetector, OpenCVFaceDetector, _haar_cascade_path
from bmo_ai.features.camera.models import Frame
from bmo_ai.features.camera.store import PhotoStore
from bmo_ai.features.contracts import FeatureError
from test_feature_camera_adapters import Clock, FakeCamera, jpeg_bytes


class CameraAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.clock = Clock();self.clock.now = 10.
        self.sdks = []
        self.enterContext(patch.object(LivePicamera2Backend, "_close_uncertain", False))
        self.enterContext(patch("bmo_ai.features.camera.live.probe_camera_capability", return_value={
            "backend": "picamera2", "available": True, "code": "camera_backend_available",
            "modules": {}, "opened": False, "physical_tested": False, "tracking_available": True,
            "activation": "explicit_supervised_opt_in"}))
        # The offline component fixture has a detector that truthfully returns
        # no boxes. It must not label an unavailable NullDetector as tracking.
        self.enterContext(patch('bmo_ai.features.camera.live.OpenCVFaceDetector',
            side_effect=lambda **_kwargs: FixtureDetector(boxes=())))
        for target in ("subprocess.Popen", "socket.socket.connect", "socket.socket.connect_ex",
                "bmo_ai.cloud.create_client", "bmo_ai.private_credentials.resolve_credentials",
                "bmo_ai.fish_tts.synthesize_wav", "bmo_ai.local_piper.synthesize_wav",
                "bmo_ai.audio.record_wav", "bmo_ai.audio.transcribe"):
            self.enterContext(patch(target, side_effect=AssertionError("Real IO forbidden")))
        imported = builtins.__import__
        def no_devices(name, *args, **kwargs):
            if name.split(".")[0] in ("picamera2", "libcamera", "cv2", "numpy", "PIL"):
                raise AssertionError("Optional physical modules forbidden")
            return imported(name, *args, **kwargs)
        self.enterContext(patch("builtins.__import__", side_effect=no_devices))

    def factory(self, *, frozen=False):
        def create():
            sdk = FakeCamera(self.clock);self.sdks.append(sdk)
            original = sdk.wait
            def wait(*args, **kwargs):
                result = original(*args, **kwargs)
                if type(result) is dict:
                    kind, output = args[0]
                    size = sdk.size if kind in ("frame", "preview") else sdk.still_size
                    payload = jpeg_bytes(*size) if kind == 'preview' else png_bytes(*size, color=(0, 0, 0))
                    output.seek(0);output.truncate();output.write(payload)
                    self.clock.now += .001
                    result["SensorTimestamp"] = 1_000_000_000 if frozen else 1_000_000_000+round(self.clock()*1e9)
                return result
            sdk.wait = wait
            return sdk
        return create

    def sleep(self, seconds):
        self.clock.now += seconds
        # Yield to the production bounded preview/photo threads while advancing
        # injected time. Owner-thread polling alone is not a new sensor frame.
        time.sleep(.01)

    def run_fixture(self, **settings):
        return run_camera_acceptance(self.root/"camera", allow_camera=True, camera_factory=self.factory(),
            clock=self.clock, sleeper=self.sleep,
            ffprobe_runner=lambda _path: {"status": "NOT_RUN", "code": "offline_test"}, **settings)

    def test_explicit_opt_in_and_caps_are_required_before_any_files_or_host(self):
        with patch("bmo_ai.features.host.FeatureHost", side_effect=AssertionError("No Host before opt-in")):
            with self.assertRaises(CameraAcceptanceError): run_camera_acceptance(self.root/"camera")
            for settings in (dict(photo_count=4), dict(video_seconds=10.1), dict(video_seconds=True), dict(covered=1)):
                with self.subTest(settings=settings), self.assertRaises(CameraAcceptanceError):
                    run_camera_acceptance(self.root/"camera", allow_camera=True, **settings)
        self.assertFalse((self.root/"camera").exists());self.assertEqual(self.sdks, [])

    def test_identical_dark_frames_with_fresh_metadata_complete_three_photos_one_video_and_cleanup(self):
        report = self.run_fixture()
        self.assertEqual((report["status"], report["source"]), ("OFFLINE_PASS", "OFFLINE_INJECTED"), report)
        self.assertEqual(len(report["photos"]), 3)
        self.assertTrue(report["identical_photo_payloads"])
        self.assertFalse(report["pixel_hash_alone_indicates_frozen"])
        self.assertEqual(report["tracking"], "TARGET_NOT_VISIBLE")
        self.assertEqual((report["cancelled_countdown"], report["reopen"]), ("PASS", "PASS"))
        self.assertEqual(report["sdk"]["still_capture_requests"], 3)
        self.assertFalse(report['preview_displayed'])
        previews=report['preview_validation']
        self.assertEqual([row['photo_index'] for row in previews], [1, 2, 3, None])
        self.assertTrue(all(row['structure_validated'] and not row['decoded']
                            and row['encoding']=='image/jpeg' and not row['displayed'] and row['countdown_seconds']==5
                            for row in previews))
        self.assertEqual(previews[-1]['probe'], 'cancelled_photo')
        self.assertEqual(len([row for row in report['stages']
                              if row['stage'].startswith('preview_validated_')]), 4)
        self.assertEqual(report["sdk"]["created"], report["sdk"]["closed"])
        self.assertTrue(report["cleanup"]["resources_cleaned"])
        self.assertEqual(report["forbidden_calls"], {"api": 0, "voice": 0, "motion": 0})
        self.assertLessEqual(report["video"]["duration"], 3)
        self.assertGreaterEqual(report["video"]["frames"], 2)
        self.assertEqual(report["video"]["source_timestamp_progress"], "PASS")
        self.assertEqual(report["video"]["audio_streams"], 0)
        photos = list((self.root/"camera"/"photos").glob("*.png"))
        videos = list((self.root/"camera"/"videos").glob("*.avi"))
        self.assertEqual((len(photos), len(videos)), (3, 1))
        for path in photos+videos:
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        public = json.dumps(report)
        self.assertNotIn(str(self.root), public);self.assertNotIn("payload", public.replace("identical_photo_payloads", ""))

    def test_camera_busy_blocks_only_this_batch_and_does_not_retry_or_close_foreign_camera(self):
        attempts = []
        def busy(): attempts.append(1);raise RuntimeError("private camera owner detail")
        report = run_camera_acceptance(self.root/"camera", allow_camera=True, camera_factory=busy,
            clock=self.clock, sleeper=self.sleep)
        self.assertEqual(report["status"], "BLOCKED", report)
        self.assertEqual(len(attempts), 1);self.assertEqual(report["sdk"]["created"], 0)
        self.assertTrue(report["cleanup"]["resources_cleaned"])
        self.assertNotIn("private camera owner", json.dumps(report))

    def test_old_sensor_timestamps_still_fail_even_when_identical_pixels_are_allowed(self):
        report = run_camera_acceptance(self.root/"camera", allow_camera=True,
            camera_factory=self.factory(frozen=True), clock=self.clock,
            sleeper=self.sleep,
            ffprobe_runner=lambda _path: {"status": "NOT_RUN"})
        self.assertEqual(report["status"], "BLOCKED", report)
        self.assertEqual(report["code"], "photo_focus_uncertain")
        self.assertEqual(report["photos"], [])
        self.assertEqual(report['sdk']['still_capture_requests'], 0)
        self.assertTrue(report['sdk']['streams'])
        self.assertTrue(all(row['strictly_advancing'] is False for row in report['sdk']['streams']))
        self.assertTrue(report["cleanup"]["resources_cleaned"])

    def test_existing_or_symlinked_destination_is_never_overwritten(self):
        destination = self.root/"camera";destination.mkdir(mode=0o700)
        original = destination/"original.txt";original.write_text("preserve")
        with self.assertRaises(CameraAcceptanceError): self.run_fixture()
        self.assertEqual(original.read_text(), "preserve")
        link = self.root/"linked";link.symlink_to(destination, target_is_directory=True)
        with self.assertRaises(CameraAcceptanceError):
            run_camera_acceptance(link, allow_camera=True)
        self.assertEqual(self.sdks, [])

    def test_sdk_observer_accepts_tuple_metadata_without_changing_result_or_retaining_array(self):
        array = object()
        value = (array, {'SensorTimestamp': 123456, 'ExposureTime': 6000, 'AnalogueGain': 1.2, 'private': 'discard'})
        sdk = SimpleNamespace(wait=lambda *_a, **_kw: value)
        observer = _SdkEvidence(lambda: sdk, self.clock)
        observer.phase = 'video'
        self.assertIs(observer().wait('job'), value)
        self.assertEqual(observer.sources[0]['sensor_timestamp_ns'], 123456)
        self.assertNotIn('private', json.dumps(observer.summary()))
        self.assertNotIn('array', json.dumps(observer.summary()))

    def prior_photos(self):
        root = self.root/'camera';root.mkdir(mode=0o700)
        store = PhotoStore(root/'photos')
        original = {}
        for index in (1, 2, 3):
            request = 'acceptance_photo_'+str(index)
            self.assertIsNone(store.begin(request, simulated=False))
            frame = Frame(1, index, self.clock(), 64, 48, png_bytes(64, 48, color=(0, 0, 0)))
            result = store.save(request, frame, simulated=False, cancel_check=lambda: None)
            path = root/'photos'/(result.photo_id+'.png')
            original[path.name] = path.read_bytes()
        return root, original

    def test_resume_reuses_exact_three_prior_photos_without_any_new_still_and_persists_full_report(self):
        root, original = self.prior_photos()
        report = self.run_fixture(resume_after_photos=True)
        self.assertEqual(report['status'], 'OFFLINE_PASS', report)
        self.assertEqual(report['sdk']['still_capture_requests'], 0)
        self.assertFalse(report['preview_displayed'])
        self.assertEqual(report['preview_validation'], [])
        self.assertEqual(report['cancelled_countdown'], 'NOT_RERUN_PRIOR_BATCH_REPORTED_PASS')
        self.assertTrue(all(photo['reused'] for photo in report['photos']))
        self.assertEqual(len(list((root/'videos').glob('*.avi'))), 1)
        self.assertEqual({p.name: p.read_bytes() for p in (root/'photos').glob('*.png')}, original)
        receipt = root/report['private_report_file']
        self.assertEqual(stat.S_IMODE(receipt.stat().st_mode), 0o600)
        self.assertEqual(json.loads(receipt.read_text()), report)
        prior_creates = len(self.sdks)
        repeated = self.run_fixture(resume_after_photos=True)
        self.assertEqual(repeated['status'], 'BLOCKED')
        self.assertEqual(len(self.sdks), prior_creates)
        self.assertEqual(len(list((root/'videos').glob('*.avi'))), 1)

    def test_blocked_tracking_still_runs_one_video_and_persists_failure_details(self):
        with patch('bmo_ai.features.camera.backends.FixtureDetector.detect', side_effect=FeatureError('face_detector_failed')):
            report = self.run_fixture()
        self.assertEqual(report['status'], 'OFFLINE_PARTIAL', report)
        self.assertEqual(report['tracking'], 'BLOCKED')
        self.assertEqual(report['tracking_code'], 'camera_capture_failed')
        self.assertTrue(report['video']['completed'])
        self.assertEqual(report['sdk']['still_capture_requests'], 3)
        self.assertTrue(report['cleanup']['resources_cleaned'])
        receipt = self.root/'camera'/report['private_report_file']
        self.assertEqual(json.loads(receipt.read_text()), report)

    def test_debian_cv2_without_data_uses_only_installed_known_cascade_and_keeps_wheel_path(self):
        path = '/usr/share/opencv4/haarcascades/haarcascade_frontalface_default.xml'
        classifier = SimpleNamespace(empty=lambda: False, detectMultiScale=lambda *_a, **_kw: ())
        cv2 = SimpleNamespace(CascadeClassifier=Mock(return_value=classifier), IMREAD_GRAYSCALE=0,
            imdecode=Mock(return_value=SimpleNamespace(shape=(24, 32))))
        numpy = SimpleNamespace(uint8=object(), frombuffer=lambda *_a, **_kw: object())
        frame = Frame(1, 1, self.clock(), 32, 24, png_bytes(32, 24))
        with patch('bmo_ai.features.camera.backends.Path.is_file', autospec=True,
                   side_effect=lambda candidate: str(candidate) == path):
            detector = OpenCVFaceDetector(enabled=True, module_loader=lambda: (cv2, numpy))
            self.assertEqual(detector.detect(frame), ())
        cv2.CascadeClassifier.assert_called_once_with(path)
        wheel = SimpleNamespace(data=SimpleNamespace(haarcascades='/existing/wheel/'))
        self.assertEqual(_haar_cascade_path(wheel), '/existing/wheel/haarcascade_frontalface_default.xml')
        with patch('bmo_ai.features.camera.backends.Path.is_file', return_value=False):
            with self.assertRaises(FeatureError): _haar_cascade_path(SimpleNamespace())

    def test_saved_frame_loss_is_explicitly_software_seeded_and_opens_no_host_or_camera(self):
        path = self.root/'covered.png';path.write_bytes(png_bytes(64, 48, color=(0, 0, 0)));path.chmod(0o600)
        detector = SimpleNamespace(detect=Mock(return_value=()), close=Mock())
        with patch('bmo_ai.features.host.FeatureHost.__init__', side_effect=AssertionError('No Host construction')), \
             patch('bmo_ai.features.camera.backends._picamera_factory', side_effect=AssertionError('No camera opening')):
            report = check_saved_frame_target_loss(path, detector=detector)
        self.assertEqual(report['status'], 'OFFLINE_PASS')
        self.assertTrue(report['detector_injected']);self.assertEqual(report['seed_source'], 'SOFTWARE_SEED_NOT_REAL_DETECTION')
        self.assertEqual((report['seeded_status'], report['missing_status'], report['expired_status']), ('tracking', 'grace', 'lost'))
        self.assertEqual(report['final_gaze'], {'owner': 'idle', 'apply': True, 'x': 0., 'y': 0.})
        self.assertEqual(report['loss_return_gaze']['owner'], 'camera')
        self.assertEqual(report['center_return_seconds'], .35)
        self.assertTrue(report['centered']);self.assertFalse(report['has_camera_owner'])
        self.assertFalse(report['actual_timing_measured']);self.assertFalse(report['physical_tracking_accepted'])
        self.assertEqual((report['camera_opens'], report['cloud_calls'], report['motion_commands'], report['files_written']), (0, 0, 0, 0))
        detector.close.assert_called_once()

    def test_saved_frame_with_visible_detection_cannot_claim_target_loss_acceptance(self):
        from bmo_ai.features.camera.models import FaceBox
        path = self.root/'visible.png';path.write_bytes(png_bytes(64, 48));path.chmod(0o600)
        detector = SimpleNamespace(detect=lambda _frame: (FaceBox(.2, .2, .3, .3),), close=lambda: None)
        report = check_saved_frame_target_loss(path, detector=detector)
        self.assertEqual((report['status'], report['code']), ('BLOCKED', 'saved_frame_target_visible'))
        self.assertFalse(report['physical_tracking_accepted'])


if __name__ == "__main__": unittest.main()
