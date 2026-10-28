"""One explicit, quiet local tone using the existing sole-player boundary.

No synthesis, credentials, mixer change or automatic repeat. A successful
command is deliberately separate from acoustic loopback and human listening.
"""
import argparse
import json
import math
from pathlib import Path
import struct
import time
import wave

from .microphone import AcceptanceError, _private_directory, _private_file


def speaker_plan():
    return dict(source="local_test_tone", duration_seconds=.6, frequency_hz=1200,
                sample_rate=16000, channels=1, sample_width=2,
                input_peak=.08, fixed_gain=.25, maximum_output_peak=.02,
                maximum_player_commands=1, deadline_seconds=3.,
                acoustic_result="NOT_MEASURED", human_listening="NOT_APPROVED",
                paid_api_calls=0, mixer_changes=0)


def run_speaker_acceptance(output_directory=None, *, live_acceptance=False,
                           speaker_device="plughw:CARD=MAX98357A,DEV=0",
                           player=None, clock=time.monotonic):
    if live_acceptance is not True:
        return dict(status="PLAN_ONLY", **speaker_plan())
    from .. import audio
    from ..features.resources import AudioFocus
    from ..integration_profile import integration_config
    from ..integration_voice import IntegrationVoice
    from ..safe_playback import attenuated_copy
    root = _private_directory(output_directory)
    raw, gained, result_path = root/"tone-source.wav", root/"tone-gained.wav", root/"speaker_result.json"
    # Reserve the result first; another call cannot play again in this batch.
    with _private_file(result_path) as receipt:
        receipt.write(b'{"status":"STARTED_NOT_YET_VERIFIED"}\n')
    result = dict(speaker_plan(), status="NOT_RUN", player_commands=0, player_reaped=False,
                  audio_focus_released=False, started_monotonic=None, ended_monotonic=None)
    temporary = voice = lease = None
    focus = AudioFocus()
    began = clock()
    def check():
        if clock()-began > 3.:
            raise AcceptanceError("speaker_deadline")
    def bounded_player(command, **settings):
        check()
        if result["player_commands"]:
            raise AcceptanceError("speaker_repeat_forbidden")
        if command[0] != "aplay":
            raise AcceptanceError("speaker_command_invalid")
        result["player_commands"] += 1
        settings["timeout"] = min(3., settings["timeout"])
        (player or audio._run)(["/usr/bin/aplay", *command[1:]], **settings)
        result["player_reaped"] = True
    def emit(kind, **_fields):
        if kind == "started": result["started_monotonic"] = clock()
        if kind == "ended": result["ended_monotonic"] = clock()
    try:
        plan = speaker_plan()
        with _private_file(raw) as handle:
            with wave.open(handle, "wb") as stream:
                stream.setnchannels(1);stream.setsampwidth(2);stream.setframerate(16000)
                count = int(16000*.6)
                samples = []
                for index in range(count):
                    # A short envelope avoids a click; it never exceeds .08.
                    ramp = min(1., index/320, (count-1-index)/320)
                    samples.append(round(32767*.08*ramp*math.sin(2*math.pi*1200*index/16000)))
                stream.writeframes(struct.pack("<"+"h"*count, *samples))
        temporary = attenuated_copy(raw, .25)
        with _private_file(gained) as output:
            output.write(temporary.read_bytes())
        check()
        config = integration_config(speaker_device=speaker_device)
        voice = IntegrationVoice(config, None, emit=emit, simulate=False, player=bounded_player)
        lease = focus.acquire("acceptance_tone")
        voice._play(gained, "local_acceptance_tone", audio._wav_info(gained), (),
                    check, emit, None, dict(emotion="neutral", emotion_validation="none", notice=False))
        result["status"] = "SPEAKER_COMMAND_PASS"
    except Exception as error:
        result["status"] = "SPEAKER_COMMAND_FAILED"
        result["code"] = getattr(error, "code", "speaker_acceptance_failed")
    finally:
        if temporary is not None: audio._discard(temporary, suppress=True)
        if voice is not None:
            try: result["voice_closed"] = voice.close() is True
            except Exception: result["voice_closed"] = False
        focus.release(lease)
        result["audio_focus_released"] = focus.owner is None
        result["elapsed_seconds"] = round(clock()-began, 4)
        # Reserved private file, never a captured sound or arbitrary exception.
        result_path.write_text(json.dumps(result, indent=2))
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live-acceptance", action="store_true")
    parser.add_argument("--output-directory", type=Path)
    parser.add_argument("--speaker-device", default="plughw:CARD=MAX98357A,DEV=0")
    args = parser.parse_args(argv)
    try:
        result = run_speaker_acceptance(args.output_directory,
            live_acceptance=args.live_acceptance, speaker_device=args.speaker_device)
    except AcceptanceError as error:
        result = dict(status="REJECTED", code=error.code)
    print(json.dumps(result))
    return 0 if result["status"] in ("PLAN_ONLY", "SPEAKER_COMMAND_PASS") else 2


if __name__ == "__main__":
    raise SystemExit(main())
