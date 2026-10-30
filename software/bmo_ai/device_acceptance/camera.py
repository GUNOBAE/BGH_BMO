"""Explicit, bounded camera acceptance using the candidate production Host.

Importing this module does not import Picamera2 or open a device. The caller
must separately authorize the camera, choose a new private destination, and
ensure no other BMO owner is using it. Native SDK configure/close calls cannot
be given an honest Python-only hard timeout; the supervising caller must also
bound the process and review the cleanup receipt. Images stay local.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import secrets
import stat
import subprocess
import time
from types import SimpleNamespace

from ..features.contracts import FeatureError


class CameraAcceptanceError(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__("The bounded camera acceptance could not proceed.")


def _private_destination(directory, *, resume_after_photos=False):
    from ..features.media.assets import _open_root
    root = Path(directory)
    if not root.is_absolute() or ".." in root.parts or root.name in ("", ".", ".."):
        raise CameraAcceptanceError("private_destination_required")
    parent = _open_root(root.parent)
    try:
        info = os.fstat(parent)
        if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise CameraAcceptanceError("private_parent_required")
        try:
            os.mkdir(root.name, mode=0o700, dir_fd=parent)
        except FileExistsError:
            pass
        descriptor = os.open(root.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                             dir_fd=parent)
        try:
            info = os.fstat(descriptor)
            if (info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700
                    or os.listdir(descriptor) and not resume_after_photos):
                raise CameraAcceptanceError("empty_private_destination_required")
        finally:
            os.close(descriptor)
    except OSError:
        raise CameraAcceptanceError("unsafe_private_destination") from None
    finally:
        os.close(parent)
    return root


def _owned_bytes(path, *, limit):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    try:
        info = os.fstat(descriptor)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1 or not 0 < info.st_size <= limit):
            raise CameraAcceptanceError('unsafe_prior_camera_file')
        with os.fdopen(descriptor, 'rb') as stream:
            descriptor = None
            return stream.read(limit+1)
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _resume_photos(root):
    """Verify exactly the three original saved results, without recapture."""
    from ..features.camera.codec import validate_png
    from ..features.camera.models import PhotoResult
    from ..features.media.assets import _open_root
    photo_root = root/'photos'
    fd = _open_root(photo_root)
    try:
        info = os.fstat(fd)
        if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise CameraAcceptanceError('unsafe_prior_photo_directory')
        names = set(os.listdir(fd))
    finally:
        os.close(fd)
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise CameraAcceptanceError('prior_photo_journal_invalid')
            result[key] = value
        return result
    records = json.loads(_owned_bytes(photo_root/'.photo-journal.json', limit=1024*1024), object_pairs_hook=unique)
    requests = {'acceptance_photo_'+str(index) for index in (1, 2, 3)}
    if type(records) is not dict or set(records) != requests:
        raise CameraAcceptanceError('exact_three_prior_photos_required')
    photos, expected = [], {'.photo-journal.json', '.photo-journal.lock'}
    for request in sorted(requests):
        result = PhotoResult(**records[request]['result'])
        if (result.request_id != request or result.status != 'succeeded' or not result.completed
                or result.photo_id != 'photo_'+hashlib.sha256(request.encode()).hexdigest()[:32]):
            raise CameraAcceptanceError('prior_photo_result_invalid')
        name = result.photo_id+'.png';expected.add(name)
        payload = _owned_bytes(photo_root/name, limit=8*1024*1024)
        width, height = validate_png(payload)
        if hashlib.sha256(payload).hexdigest() != result.sha256 or len(payload) != result.bytes:
            raise CameraAcceptanceError('prior_photo_hash_mismatch')
        photos.append(dict(photo_id=result.photo_id, bytes=len(payload), sha256=result.sha256,
            width=width, height=height, decoded=True, simulated=result.simulated,
            completed=True, reused=True, source_timestamp_status='UNMEASURED_PRIOR_BATCH'))
    if names != expected:
        raise CameraAcceptanceError('unexpected_prior_photo_files')
    videos = root/'videos'
    if videos.exists() or videos.is_symlink():
        fd = _open_root(videos)
        try:
            info = os.fstat(fd)
            if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700 or os.listdir(fd):
                raise CameraAcceptanceError('prior_video_must_be_empty')
        finally:
            os.close(fd)
    try:
        marker = os.open(root/'.camera-resume-once', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    except FileExistsError:
        raise CameraAcceptanceError('camera_resume_already_used') from None
    os.close(marker)
    return photos


def _persist_report(root, report):
    """An exclusive private receipt survives callers printing only a subset."""
    from ..features.media.assets import _open_root
    directory = _open_root(root)
    descriptor = None
    try:
        name = 'camera-acceptance-'+secrets.token_hex(12)+'.json'
        descriptor = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                             0o600, dir_fd=directory)
        report['private_report_file'] = name
        report['report_persisted'] = True
        payload = json.dumps(report, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode()
        with os.fdopen(descriptor, 'wb') as stream:
            descriptor = None
            stream.write(payload);stream.flush();os.fsync(stream.fileno())
        os.fsync(directory)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        os.close(directory)


class _SdkEvidence:
    """Wrap public SDK methods; retain metadata scalars, never image contents."""
    def __init__(self, factory, clock):
        self.factory, self.clock = factory, clock
        self.phase = "initializing"
        self.sources = []
        self.created = self.stills = self.stopped = self.closed = 0

    def __call__(self):
        camera = self.factory()
        self.created += 1
        stream = self.created
        evidence = self
        class ObservedCamera:
            def __getattr__(self, name): return getattr(camera, name)
            def switch_mode_and_capture_file(self, *args, **kwargs):
                evidence.stills += 1
                return camera.switch_mode_and_capture_file(*args, **kwargs)
            def wait(self, *args, **kwargs):
                value = camera.wait(*args, **kwargs)
                metadata = (value if type(value) is dict else value[1]
                    if type(value) in (tuple, list) and len(value) == 2 and type(value[1]) is dict else None)
                if metadata is not None and len(evidence.sources) < 128:
                    sensor = metadata.get("SensorTimestamp")
                    if type(sensor) is int and sensor > 0:
                        row = dict(stream=stream, phase=evidence.phase,
                            sensor_timestamp_ns=sensor, received_monotonic=evidence.clock())
                        for source, target in (("ExposureTime", "exposure_us"), ("AnalogueGain", "analogue_gain")):
                            item = metadata.get(source)
                            if type(item) in (int, float) and math.isfinite(item) and item >= 0:
                                row[target] = item
                        evidence.sources.append(row)
                return value
            def stop(self, *args, **kwargs):
                result = camera.stop(*args, **kwargs);evidence.stopped += 1;return result
            def close(self, *args, **kwargs):
                result = camera.close(*args, **kwargs);evidence.closed += 1;return result
        return ObservedCamera()

    def summary(self):
        streams = []
        for stream in range(1, self.created+1):
            rows = [row for row in self.sources if row["stream"] == stream]
            stamps = [row["sensor_timestamp_ns"] for row in rows]
            streams.append(dict(stream=stream, frames=len(stamps),
                strictly_advancing=all(b > a for a, b in zip(stamps, stamps[1:])) if len(stamps) >= 2 else None,
                source_span_ms=round((stamps[-1]-stamps[0])/1e6, 3) if stamps else None))
        return dict(created=self.created, still_capture_requests=self.stills,
                    stopped=self.stopped, closed=self.closed, streams=streams,
                    source_frames=list(self.sources), source_metadata_truncated=len(self.sources) >= 128)


def _ffprobe(path):
    binary = shutil.which("ffprobe")
    if binary is None:
        return {"status": "NOT_RUN", "code": "ffprobe_missing"}
    try:
        completed = subprocess.run([binary, "-v", "error", "-count_frames", "-show_entries",
            "stream=codec_type,codec_name,width,height,nb_read_frames,avg_frame_rate,duration", "-of", "json", str(path)],
            capture_output=True, text=True, timeout=8, check=False,
            env={key: os.environ[key] for key in ("PATH", "LANG", "LC_ALL") if key in os.environ})
        if completed.returncode != 0 or len(completed.stdout) > 16384:
            return {"status": "FAIL", "code": "ffprobe_decode_failed"}
        streams = json.loads(completed.stdout)["streams"]
        result = []
        for stream in streams:
            result.append({key: stream[key] for key in ("codec_type", "codec_name", "width", "height",
                "nb_read_frames", "avg_frame_rate", "duration") if key in stream})
        return {"status": "PASS", "streams": result}
    except (OSError, subprocess.SubprocessError, ValueError, TypeError, KeyError):
        return {"status": "FAIL", "code": "ffprobe_decode_failed"}


def check_saved_frame_target_loss(path, *, detector=None):
    """Read one saved PNG, then prove software target expiry without a device.

    OpenCV runs on the saved image, not on a camera stream. The initial target
    is an explicit SOFTWARE_SEED and all tracker times are simulated, so this
    check cannot establish physical tracking or end-to-end latency acceptance.
    It writes nothing; the caller may retain this metadata-only result privately.
    """
    from ..features.camera.backends import OpenCVFaceDetector
    from ..features.camera.codec import validate_png
    from ..features.camera.models import FaceBox, Frame
    from ..features.camera.tracking import TargetTracker
    from ..features.host import FeatureHost
    from ..features.media.assets import _open_root
    from ..features.resources import GazeArbiter
    source = Path(path)
    if not source.is_absolute() or '..' in source.parts or source.suffix != '.png':
        raise CameraAcceptanceError('private_saved_png_required')
    directory = _open_root(source.parent)
    os.close(directory)
    payload = _owned_bytes(source, limit=8*1024*1024)
    width, height = validate_png(payload)
    injected = detector is not None
    detector = detector if injected else OpenCVFaceDetector(enabled=True)
    now = [100.]
    frame = Frame(1, 1, now[0], width, height, payload)
    report = dict(version=1, source='SAVED_FILE_NO_CAMERA',
        detector_backend='INJECTED' if injected else 'OPENCV_HAAR_LOCAL', detector_injected=injected,
        sha256=hashlib.sha256(payload).hexdigest(), width=width, height=height,
        seed_source='SOFTWARE_SEED_NOT_REAL_DETECTION', time_kind='SIMULATED_MONOTONIC',
        actual_timing_measured=False, physical_tracking_accepted=False,
        camera_opens=0, cloud_calls=0, motion_commands=0, files_written=0)
    try:
        boxes = tuple(detector.detect(frame))
        if any(not isinstance(box, FaceBox) for box in boxes):
            raise CameraAcceptanceError('invalid_saved_frame_detection')
        report['detected_count'] = len(boxes)
        if boxes:
            return dict(report, status='BLOCKED', code='saved_frame_target_visible')
        tracker, gaze = TargetTracker(), GazeArbiter(clock=lambda: now[0])
        host_view = SimpleNamespace(modes=SimpleNamespace(generation=1), gaze=gaze)
        seeded = tracker.update(frame, (FaceBox(.7, .25, .2, .2),), now=now[0], generation=1)
        FeatureHost._camera_target(host_view, seeded)
        initial = gaze.resolve(phase='idle', generation=1)
        now[0] += .1
        missing = tracker.update(Frame(1, 2, now[0], width, height, payload), boxes,
                                 now=now[0], generation=1)
        FeatureHost._camera_target(host_view, missing)
        now[0] = 100.+tracker.config.max_age+tracker.config.loss_grace+.1
        expired = tracker.expire(now[0], generation=1)
        FeatureHost._camera_target(host_view, expired)
        default = gaze.resolve(phase='idle', generation=1)
        centered = (default['owner'] == 'idle' and default['x'] == 0. and default['y'] == 0.)
        passed = seeded.status == 'tracking' and initial['owner'] == 'camera' and expired.status == 'lost' and centered
        return dict(report, status='OFFLINE_PASS' if passed else 'FAIL',
            code='saved_frame_software_target_loss_verified' if passed else 'saved_frame_target_loss_failed',
            seeded_status=seeded.status, initial_gaze=initial, missing_status=missing.status,
            expired_status=expired.status, expired_target_x=expired.x, expired_target_y=expired.y,
            final_gaze=default, centered=centered, has_camera_owner=default['owner'] == 'camera')
    except FeatureError as error:
        return dict(report, status='BLOCKED', code=error.code)
    finally:
        detector.close()


def run_camera_acceptance(directory, *, allow_camera=False, photo_count=3, video_seconds=3.,
                          covered=True, clock=time.monotonic, sleeper=time.sleep,
                          camera_factory=None, ffprobe_runner=None, resume_after_photos=False):
    """Three photos, one <=10s silent video, cancel/off/reopen and cleanup.

    An injected SDK is marked OFFLINE_INJECTED, never a live acceptance PASS.
    This function never launches Desktop, microphones, voice or motor transport.
    Failure to obtain this camera blocks only this function; no service is stopped.
    """
    if allow_camera is not True:
        raise CameraAcceptanceError("explicit_camera_acceptance_required")
    if (type(photo_count) is not int or photo_count != 3 or type(covered) is not bool
            or type(resume_after_photos) is not bool
            or type(video_seconds) not in (int, float) or not math.isfinite(video_seconds)
            or not .5 <= video_seconds <= 10):
        raise CameraAcceptanceError("camera_acceptance_limit_invalid")
    root = _private_destination(directory, resume_after_photos=resume_after_photos)
    from ..features.camera.backends import _picamera_factory
    from ..features.camera.codec import validate_png
    from ..features.camera.live_codec import validate_rgb_avi
    from ..features.camera.jpeg_codec import validate_jpeg
    from ..features.camera.video import VideoConfig
    from ..features.host import FeatureHost, HostControl
    from ..features.reply import HostIntent
    from ..features.runtime import RuntimePolicy
    from ..integration_profile import integration_config
    from ..integration_voice import IntegrationVoice
    injected = camera_factory is not None
    evidence = _SdkEvidence(camera_factory or _picamera_factory, clock)
    began = clock()
    denied_calls = {"api": 0, "voice": 0, "motion": 0}
    stages = []
    report = dict(version=1, status="RUNNING", source="OFFLINE_INJECTED" if injected else "LIVE_CAMERA",
        covered=covered, photo_limit=3, video_limit_seconds=video_seconds, photos=[], video=None,
        tracking="NOT_RUN", reopen="NOT_RUN", cancelled_countdown="NOT_RUN",
        native_timeout_guaranteed=False, cleanup={"resources_cleaned": False}, stages=stages,
        human_image_review="NOT_RUN", preview_displayed=False, preview_validation=[],
        public_image_content=False, resume_after_photos=resume_after_photos)
    host = None
    def deny(kind):
        def blocked(*_args, **_kwargs):
            denied_calls[kind] += 1
            raise FeatureError("acceptance_forbidden_"+kind)
        return blocked
    class NoCloud:
        def __init__(self):
            self.responses = self
            self.audio = SimpleNamespace(transcriptions=self, speech=self)
        create = deny("api")
        def close(self): pass
    def tick():
        if clock()-began > 60:
            raise CameraAcceptanceError("camera_acceptance_deadline")
        host.heartbeat();host.poll()
    def wait_until(predicate, seconds):
        deadline = min(began+60, clock()+seconds)
        while not predicate():
            if clock() >= deadline:
                raise CameraAcceptanceError("camera_acceptance_timeout")
            tick();sleeper(.05)
        tick()
    def require(result, allowed=("succeeded", "running")):
        if result.status not in allowed:
            raise CameraAcceptanceError(result.code)
    def stage(name):
        evidence.phase = name
        stages.append(dict(stage=name, at_ms=round((clock()-began)*1000, 3)))
    def acknowledge_preview(request, label, photo_index=None):
        # This component tool has no Desktop or display. Consume the bounded
        # latest frame without claiming JPEG pixel decode or GUI review.
        def preview_ready():
            state = host.snapshot()
            if state.get('photo_phase') == 'failed':
                raise CameraAcceptanceError((state.get('last_result') or {}).get('code', 'photo_preview_unavailable'))
            return (state.get('photo_request_id') == request
                and bool(state.get('photo_preview_token'))
                and state['camera']['preview_available'])
        wait_until(preview_ready, 5)
        preview = host.preview_frame()
        if preview is None:
            raise CameraAcceptanceError('photo_preview_unavailable')
        mime = preview['mime']
        if mime == 'image/jpeg':
            width, height = validate_jpeg(preview['payload'])
        elif mime == 'image/png':
            width, height = validate_png(preview['payload'])
        else:
            raise CameraAcceptanceError('invalid_camera_preview')
        state = host.snapshot()
        stage('preview_validated_'+label)
        report['preview_validation'].append(dict(photo_index=photo_index, probe=label,
            structure_validated=True, decoded=mime == 'image/png', encoding=mime,
            width=width, height=height, displayed=False, countdown_seconds=5))
        require(host.control(HostControl('photo_preview_ready', {
            'generation': state['mode_generation'], 'request_id': request,
            'preview_token': state['photo_preview_token']}), 'acceptance_preview_'+label),
            allowed=('succeeded', 'simulated'))
        tick()
        if host.snapshot()['photo_phase'] != 'countdown':
            raise CameraAcceptanceError('photo_countdown_not_started')
    try:
        if resume_after_photos:
            stage('verify_prior_three_photos')
            report['photos'] = _resume_photos(root)
            report['cancelled_countdown'] = 'NOT_RERUN_PRIOR_BATCH_REPORTED_PASS'
        stage("construct_candidate_host")
        config, client = integration_config(), NoCloud()
        voice = IntegrationVoice(config, client, simulate=False, emit=lambda *_a, **_kw: None,
            fish_synthesizer=deny("voice"), piper_synthesizer=deny("voice"), player=deny("voice"))
        voice.listen = deny("voice")
        host = FeatureHost(config=config, client=client, voice=voice, clock=clock,
            runtime=RuntimePolicy(simulate=False), camera_supervised=True,
            photo_directory=root/"photos", camera_video_supervised=True, video_directory=root/"videos",
            photo_feedback=False)
        host.camera.broker.factory.camera_factory = evidence
        host.motion.submit = deny("motion")
        host.video.config = VideoConfig(max_duration=float(video_seconds), fps=2)
        for index in (() if resume_after_photos else range(3)):
            stage("photo_"+str(index+1))
            require(host.execute(HostIntent("take_photo"), request_id="acceptance_photo_"+str(index+1)))
            request = "acceptance_photo_"+str(index+1)
            acknowledge_preview(request, str(index+1), photo_index=index+1)
            stage('photo_'+str(index+1))
            def photo_finished():
                state = host.snapshot()
                if state.get('photo_phase') == 'focus_review':
                    # Quality override belongs to the user, never this tool.
                    raise CameraAcceptanceError('photo_focus_uncertain')
                return (state.get('photo') or {}).get('request_id') == request
            wait_until(photo_finished, 11)
            photo = host.snapshot()["photo"]
            if photo["status"] != "succeeded" or not photo["completed"]:
                raise CameraAcceptanceError(photo["code"])
            payload = host.photo_bytes(photo["photo_id"])
            width, height = validate_png(payload)
            if hashlib.sha256(payload).hexdigest() != photo["sha256"]:
                raise CameraAcceptanceError("photo_hash_mismatch")
            report["photos"].append(dict(photo_id=photo["photo_id"], bytes=len(payload),
                sha256=photo["sha256"], width=width, height=height, decoded=True,
                simulated=photo["simulated"], completed=photo["completed"]))
            require(host.execute(HostIntent("return_to_face"), request_id="acceptance_return_"+str(index+1)),
                    allowed=("succeeded", "simulated"))
        if not resume_after_photos:
            stage("cancel_photo_countdown")
            stills_before = evidence.stills
            require(host.execute(HostIntent("take_photo"), request_id="acceptance_cancelled_photo"))
            acknowledge_preview('acceptance_cancelled_photo', 'cancelled_photo')
            stage('cancel_photo_countdown')
            host.control(HostControl("cancel"), "acceptance_cancel")
            tick()
            if host.camera.broker.opened or evidence.stills != stills_before:
                raise CameraAcceptanceError("cancelled_photo_not_cleaned")
            report["cancelled_countdown"] = "PASS"
        stage("tracking_target_check")
        try:
            require(host.execute(HostIntent("start_face_tracking"), request_id="acceptance_tracking"))
            def tracking_ready():
                error = host.camera.snapshot()['error']
                if error:
                    raise CameraAcceptanceError(error)
                return len([r for r in evidence.sources if r['phase'] == 'tracking_target_check']) >= 3
            wait_until(tracking_ready, 4)
            target = host.camera.snapshot()["target"]
            visible = target["status"] in ("tracking", "grace")
            report["tracking"] = ("UNEXPECTED_TARGET_ON_COVERED_CAMERA" if covered and visible else
                "TARGET_NOT_VISIBLE" if not visible else "POSITION_TARGET_OBSERVED_USER_REVIEW_REQUIRED")
        except (CameraAcceptanceError, FeatureError) as error:
            report['tracking'], report['tracking_code'] = 'BLOCKED', error.code
        finally:
            require(host.execute(HostIntent("stop_face_tracking"), request_id="acceptance_tracking_off"))
            if host.camera.broker.opened or host.camera.broker.cleanup_uncertain:
                raise CameraAcceptanceError("tracking_off_not_cleaned")
            report['gaze_after_tracking_off'] = host.gaze.resolve(
                phase=host.session.snapshot()['phase'], generation=host.modes.generation)
        stage("preview_reopen")
        try:
            require(host.control(HostControl("preview", True), "acceptance_reopen"))
            wait_until(lambda: host.camera.preview_bytes() is not None, 3)
            report["reopen"] = "PASS"
        except (CameraAcceptanceError, FeatureError) as error:
            report['reopen'], report['reopen_code'] = 'BLOCKED', error.code
        finally:
            host.control(HostControl("preview", False), "acceptance_reopen_off")
            if host.camera.broker.opened or host.camera.broker.cleanup_uncertain:
                raise CameraAcceptanceError("preview_off_not_cleaned")
        stage("video")
        require(host.control(HostControl("video_start", {"consent": True}), "acceptance_video"))
        wait_until(lambda: not host.video.snapshot()["recording"], video_seconds+5)
        video = host.video.snapshot()["result"]
        if video is None or video["status"] != "succeeded" or not video["completed"]:
            raise CameraAcceptanceError(video["code"] if video else "video_result_missing")
        payload = host.video_bytes(video["video_id"])
        decoded = validate_rgb_avi(payload)
        if hashlib.sha256(payload).hexdigest() != video["sha256"]:
            raise CameraAcceptanceError("video_hash_mismatch")
        probe = (ffprobe_runner or _ffprobe)(root/"videos"/(video["video_id"]+".avi"))
        report["video"] = dict(video_id=video["video_id"], sha256=video["sha256"], bytes=len(payload),
            **decoded, ffprobe=probe, simulated=video["simulated"], completed=video["completed"])
        if probe.get("status") == "FAIL":
            raise CameraAcceptanceError("video_external_decode_failed")
        timestamps = [r["sensor_timestamp_ns"] for r in evidence.sources if r["phase"] == "video"]
        report["video"]["source_timestamp_progress"] = ("PASS" if len(timestamps) >= 2
            and all(b > a for a, b in zip(timestamps, timestamps[1:])) else "NOT_ADVANCING_OR_UNMEASURED")
        if report["video"]["source_timestamp_progress"] != "PASS":
            raise CameraAcceptanceError("video_source_timestamp_unverified")
        report["identical_photo_payloads"] = len({p["sha256"] for p in report["photos"]}) < len(report["photos"])
        report["pixel_hash_alone_indicates_frozen"] = False
        partial = report['tracking'] == 'BLOCKED' or report['reopen'] == 'BLOCKED'
        report["status"] = ('OFFLINE_PARTIAL' if injected else 'PARTIAL') if partial else 'OFFLINE_PASS' if injected else 'PASS'
        report["code"] = "camera_acceptance_partial" if partial else "camera_acceptance_completed"
    except (CameraAcceptanceError, FeatureError) as error:
        report["status"], report["code"] = "BLOCKED", error.code
    except KeyboardInterrupt:
        report["status"], report["code"] = "INTERRUPTED", "camera_acceptance_interrupted"
    except Exception:
        report["status"], report["code"] = "FAIL", "camera_acceptance_failed"
    finally:
        stage("cleanup_candidate_host")
        if host is not None:
            try:
                host.close()
                cleanup = host.cleanup_snapshot()
                report["cleanup"] = dict(resources_cleaned=cleanup.get("resources_cleaned") is True,
                    **{key: value for key, value in cleanup.get("camera", {}).items()
                       if key in ("sensor_closed", "photo_active", "close_unknown", "physical_release_verified")})
            except Exception:
                report["cleanup"] = {"resources_cleaned": False, "code": "camera_cleanup_failed"}
        report["sdk"] = evidence.summary()
        report["forbidden_calls"] = denied_calls
        report["elapsed_seconds"] = round(clock()-began, 4)
        if any(denied_calls.values()):
            report["status"], report["code"] = "FAIL", "acceptance_barrier_triggered"
        if host is not None and not report["cleanup"]["resources_cleaned"]:
            report["status"], report["code"] = "UNKNOWN", "camera_cleanup_unverified"
        try:
            _persist_report(root, report)
        except Exception:
            report['report_persisted'] = False
            report['status'], report['code'] = 'UNKNOWN', 'camera_report_write_failed'
    return report
