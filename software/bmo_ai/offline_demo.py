"""Finite synthetic conversation demo. No SDK client, microphone or player.

Use --headless for a device-free run without Pygame or PNGs. The optional desktop
view reuses display_app and PygameFace; it must be started manually by the user.
"""
from __future__ import annotations

import argparse
from collections import deque
import json
from threading import get_ident
import time

from .app import ConversationApp
from .audio import AudioPlaybackError, AudioValidationError
from .cloud import CloudError
from .config import BMOConfig
from .conversation import ConversationWorker, TurnCancelled
from .hardware import FaceState, NullFace


SCENARIOS = ("success", "slow", "authentication", "rate_limit", "empty_stt",
             "broken_tts", "playback_error", "cancel", "timeout")


def _pause(cancellation, seconds):
    if cancellation.wait(seconds):
        raise TurnCancelled()


class FakeSTT:
    def __init__(self, scenario, cancellation, delay):
        self.scenario, self.cancellation, self.delay = scenario, cancellation, delay

    def transcribe(self):
        _pause(self.cancellation, self.delay)
        return "" if self.scenario == "empty_stt" else "Hello BMO."


class FakeBrain:
    def __init__(self, scenario, cancellation, delay):
        self.scenario, self.cancellation, self.delay = scenario, cancellation, delay

    def ask(self, message):
        _pause(self.cancellation, self.delay * (8 if self.scenario in ("slow", "timeout", "cancel") else 1))
        if self.scenario in ("authentication", "rate_limit"):
            raise CloudError(self.scenario, "Synthetic provider failure.")
        return "Hello! This is an offline demonstration."


class FakeTTS:
    def __init__(self, scenario, cancellation, delay):
        self.scenario, self.cancellation, self.delay = scenario, cancellation, delay

    def synthesize(self, text):
        _pause(self.cancellation, self.delay)
        if self.scenario == "broken_tts":
            raise AudioValidationError("Synthetic invalid audio.", "invalid_wav")
        return b"synthetic playback token; not an audio file"


class FakePlayer:
    def __init__(self, scenario, cancellation, delay):
        self.scenario, self.cancellation, self.delay = scenario, cancellation, delay

    def play(self, token, *, on_start):
        if self.cancellation.is_set():
            raise TurnCancelled()
        if self.scenario == "playback_error":
            raise AudioPlaybackError("Synthetic player failure.", "device_unavailable")
        on_start()
        _pause(self.cancellation, self.delay * 2)


class FakeVoice:
    def __init__(self, scenario, cancellation, delay=0.06):
        self.stt = FakeSTT(scenario, cancellation, delay)
        self.tts = FakeTTS(scenario, cancellation, delay)
        self.player = FakePlayer(scenario, cancellation, delay)

    def listen(self):
        return self.stt.transcribe()

    def speak(self, text, *, on_playback_start):
        token = self.tts.synthesize(text)
        self.player.play(token, on_start=on_playback_start)


def offline_app(scenario, face, cancellation, *, delay=0.06):
    return ConversationApp(FakeBrain(scenario, cancellation, delay), None, BMOConfig(),
                           face=face, voice=FakeVoice(scenario, cancellation, delay))


class OfflineDemo:
    """UI-thread coordinator; bounded history, finite script, no background UI IO."""

    def __init__(self, scenarios=SCENARIOS, *, cycles=1, delay=0.06,
                 clock=time.monotonic, output_fn=print):
        if not scenarios or any(s not in SCENARIOS for s in scenarios):
            raise ValueError("unknown or empty scenario list")
        if type(cycles) is not int or not 1 <= cycles <= 20:
            raise ValueError("cycles must be from 1 to 20")
        if type(delay) not in (int, float) or not 0 < delay <= 0.1:
            raise ValueError("delay must be from above zero to 0.1 seconds")
        self._script = tuple(scenarios) * cycles
        self._index = 0
        self._clock, self._output = clock, output_fn
        self._owner = get_ident()
        self._started = clock()
        self._next_at = self._started
        self._delay = delay
        self._closed = False
        self._worker = None
        self._state = FaceState.IDLE
        self._reset_at = None
        self._cancel_at = None
        self.completed = deque(maxlen=64)
        self.completed_count = 0
        self.updates = 0
        self.failed = False

    @property
    def worker(self):
        return self._worker

    def handle_event(self, event, pygame):
        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_c and self._worker is not None:
                self._worker.cancel()
            elif event.key == pygame.K_SPACE and self._worker is not None:
                # Repeated inputs cannot queue another turn while this one runs.
                if self._worker.busy:
                    self._worker.start()

    def _show(self, face, state):
        if state != self._state:
            self._state = state
            face.set_state(state)

    def update(self, face):
        if get_ident() != self._owner:
            raise RuntimeError("Demo updates belong to the UI thread")
        if self._closed:
            return True
        self.updates += 1
        now = self._clock()
        if now - self._started > 120:
            self.failed = True
            self._output("Offline demo exceeded its 120-second limit.")
            self.close()
            return True
        if self._reset_at is not None and now >= self._reset_at:
            self._show(face, FaceState.IDLE)
            self._reset_at = None
        if self._worker is not None:
            if self._cancel_at is not None and now >= self._cancel_at:
                self._worker.cancel()
                self._cancel_at = None
            for item in self._worker.poll():
                if item.state is not None:
                    self._show(face, item.state)
                if item.kind == "cancel_requested":
                    self._output(json.dumps({"turn": item.turn, "event": item.kind,
                                             "code": item.code, "synthetic": True}))
                if item.kind == "state" and item.state == FaceState.THINKING and self._script[self._index] == "cancel":
                    self._cancel_at = now + self._delay
                if item.kind == "finished":
                    scenario = self._script[self._index]
                    self.completed.append(item)
                    self.completed_count += 1
                    self._output(json.dumps({"scenario": scenario, "code": item.code,
                        "synthetic": True, "timings_seconds": dict(item.timings)}))
                    self._worker.close()
                    self._worker = None
                    self._index += 1
                    self._cancel_at = None
                    self._reset_at = now + self._delay
                    self._next_at = now + 2 * self._delay
        if self._worker is None and now >= self._next_at:
            if self._index >= len(self._script):
                return True
            scenario = self._script[self._index]
            self._worker = ConversationWorker(
                lambda face, cancel: offline_app(scenario, face, cancel, delay=self._delay),
                timeout=self._delay * 4 if scenario == "timeout" else 10,
                clock=self._clock, synthetic=True)
            self._worker.start()
        return False

    def close(self):
        self._closed = True
        stopped = self._worker is None or self._worker.close()
        if not stopped:
            self._output("Cancellation requested; worker has not stopped yet (daemon, no UI wait).")
        return stopped


def main(argv=None, *, output_fn=print):
    parser = argparse.ArgumentParser(description="Finite offline BMO demo; fake STT/LLM/TTS/player only.")
    parser.add_argument("--headless", action="store_true", help="no Pygame, assets or display")
    parser.add_argument("--windowed", action="store_true", help="use an 800x480 desktop window")
    parser.add_argument("--scenario", choices=("all", *SCENARIOS), default="all")
    parser.add_argument("--cycles", type=int, choices=range(1, 21), default=1)
    options = parser.parse_args(argv)
    scenarios = SCENARIOS if options.scenario == "all" else (options.scenario,)
    demo = OfflineDemo(scenarios, cycles=options.cycles, output_fn=output_fn)
    try:
        if options.headless:
            face = NullFace()
            output_fn("Offline headless demo: synthetic stages, no face assets or devices.")
            while not demo.update(face):
                time.sleep(0.005)
            return 1 if demo.failed else 0
        from .display_app import main as display_main
        output_fn("Offline demo: C requests cancel, ESC / Q quits; finite automatic scenarios.")
        result = display_main(["--windowed"] if options.windowed else [],
                              session=demo, output_fn=output_fn)
        return 1 if demo.failed else result
    except KeyboardInterrupt:
        return 0
    finally:
        demo.close()


if __name__ == "__main__":
    raise SystemExit(main())
