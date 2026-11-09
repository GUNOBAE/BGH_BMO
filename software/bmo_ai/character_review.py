"""One operator-driven local review of prepared voice, PTT and wake assets.

Default is a plan. Interactive actions never generate, approve, upload, contact
a paid service, open cameras/serial, or change the existing operating BMO.
Each audition/finite capture requires a separate typed operator action.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

from .integration_profile import integration_config
from .voice_bank import VoiceBank, BankPlayer, default_voice_bank_directory


class CharacterReview:
    def __init__(self, bank, *, manifest=None, directory=None, player=None,
                 capture=None, infer=None):
        self.bank, self.manifest = bank, Path(manifest) if manifest else None
        self.directory = Path(directory).expanduser().absolute() if directory else None
        self.player = player
        self.capture, self.infer = capture, infer
        self.plays = self.captures = 0
        self.last_wav = None
        self._cleanup_failed = False

    def plan(self):
        clips = [dict(id=row["clip_id"], model=row["model"], status=row["status"])
                 for row in self.bank.inventory()
                 if row["status"] in ("LISTENING_NOT_APPROVED", "READY")]
        return dict(mode="PLAN_ONLY", samples=clips, microphone_opened=False, playback_started=False,
                    wake_manifest_present=bool(self.manifest and self.manifest.is_file()),
                    vad_mode="webrtc", maximum_captures=3, maximum_seconds_each=10,
                    maximum_manual_plays=2, api_calls=0, automatic_approval=False,
                    camera_or_motor_commands=0, human_acceptance="NOT_RUN")

    def action(self, command):
        words = command.strip().split()
        if self._cleanup_failed and words[:1] in (["play"], ["record"], ["wake"]):
            return dict(status="BLOCKED", reason_code="previous_capture_cleanup_failed")
        if words[:1] == ["play"] and len(words) == 2:
            if self.plays >= 2:
                return dict(status="LIMIT_REACHED", kind="manual_plays")
            available = {r["id"] for r in self.plan()["samples"]}
            if words[1] not in available:
                return dict(status="SAMPLE_UNAVAILABLE")
            self.plays += 1  # Failed starts also consume the conservative limit.
            if self.player is None:
                self.player = BankPlayer(self.bank.config, self.bank)
            result = self.player.play(words[1], emit=lambda *_a, **_kw: None, allow_unapproved=True)
            return dict(status="AUDITION_FINISHED", clip_id=words[1],
                        completed=result.get("completed") is True, approved=False,
                        listening_decision="OPERATOR_REVIEW_REQUIRED", api_calls=0)
        if words in (["record"], ["wake"]):
            if words == ["wake"] and not (self.manifest and self.manifest.is_file()):
                return dict(status="MODEL_MISSING", microphone_opened=False)
            if self.captures >= 3:
                return dict(status="LIMIT_REACHED", kind="finite_captures")
            if self.directory is None:
                return dict(status="PRIVATE_DIRECTORY_REQUIRED")
            if self.capture is None:
                from .device_acceptance.microphone import run_microphone_acceptance
                self.capture = run_microphone_acceptance
            self.captures += 1
            result = self.capture(self.directory, live_acceptance=True, trial="normal",
                                  max_seconds=10., vad_mode="webrtc")
            self._cleanup_failed = result.get("resources_cleaned") is not True
            wav = result.get("wav", {})
            name = wav.get("file")
            if (wav.get("decoded") is True and type(name) is str
                    and Path(name).name == name and name.startswith("mic-normal-") and name.endswith(".wav")):
                self.last_wav = self.directory/name
            else:
                self.last_wav = None
            safe = dict(status=result["status"], ptt=result.get("ptt_status"),
                        raw_pcm_seconds=result.get("pcm", {}).get("stream_seconds"),
                        resources_cleaned=result.get("resources_cleaned"), api_calls=0,
                        human_accuracy="OPERATOR_REVIEW_REQUIRED")
            if words == ["wake"] and self.last_wav is not None:
                safe["wake"] = self._infer()
            return safe
        if words == ["infer"]:
            return self._infer()
        if words in (["list"], ["plan"]):
            return self.plan()
        return dict(status="UNKNOWN_ACTION", commands=["list", "play <clip_id>", "record", "wake", "infer", "quit"])

    def _infer(self):
        if not (self.manifest and self.manifest.is_file()):return dict(status="MODEL_MISSING")
        if self.last_wav is None:return dict(status="NO_OPERATOR_RECORDING")
        if self.infer is None:
            from .wake_assets import infer_wav
            self.infer = infer_wav
        result = self.infer(self.manifest, self.last_wav)
        if result.get("status") == "BLOCKED":
            detector = result.get("detector", {})
            return dict(status="BLOCKED", detector_status=detector.get("status"),
                        reason_code=detector.get("reason_code", "wake_inference_blocked"),
                        human_accuracy="NOT_RUN", audio_uploaded=False)
        return {key: result.get(key) for key in ("status", "peak_score", "activations", "supported_phrases",
                                                "unverified_phrases", "execution_providers")} | {
                    "human_accuracy": "OPERATOR_REVIEW_REQUIRED", "audio_uploaded": False}

    def close(self):
        player_closed = self.player.close() if self.player is not None else True
        return player_closed is not False and not self._cleanup_failed


def main(argv=None, *, read=input, write=print):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--review", action="store_true", help="interactive operator menu; never auto-record/play")
    parser.add_argument("--operator-present", action="store_true")
    parser.add_argument("--voice-bank-directory", type=Path)
    parser.add_argument("--wake-model-manifest", type=Path)
    parser.add_argument("--output-directory", type=Path)
    args = parser.parse_args(argv)
    if args.review and not args.operator_present:
        parser.error("--review requires --operator-present")
    directory = args.output_directory
    if args.review and directory is None:
        directory = Path.home()/".local/state/bmo/operator-review"/datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        directory.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    session = CharacterReview(VoiceBank(args.voice_bank_directory or default_voice_bank_directory(), integration_config()),
                              manifest=args.wake_model_manifest, directory=directory)
    code = 0
    try:
        write(json.dumps(session.plan(), ensure_ascii=False))
        if args.review:
            write("각 동작을 직접 선택하세요. record/wake: 최대10초 로컬 녹음, play <id>: 1회 청취. API/자동 승인 없음. quit: 종료.")
        while args.review:
            try: command = read("BMO review> ")
            except EOFError:break
            if command.strip() in ("quit", "q", "exit"):break
            try:write(json.dumps(session.action(command), ensure_ascii=False))
            except Exception as error:
                reason = getattr(error, "code", "local_review_failed")
                write(json.dumps(dict(status="REVIEW_FAILED", code=reason)))
    except KeyboardInterrupt:
        code = 130
    finally:
        try:cleaned = session.close() is not False
        except Exception:cleaned = False
        if not cleaned:
            write(json.dumps(dict(status="CLEANUP_FAILED", reason_code="local_review_cleanup_failed")))
            code = 2
    return code


if __name__ == "__main__":raise SystemExit(main())
