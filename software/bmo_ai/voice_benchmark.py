"""Explicit one-sample Aaron emotion preparation/audition/approval, never boot.

Default --plan has no credentials, filesystem, synthesis or player IO. Real
preparation requires --allow-paid-api and is bounded to one fixed short sample
per invocation; verified existing assets are reused, never regenerated silently.
"""
from contextlib import contextmanager
from dataclasses import dataclass
import argparse
import fcntl
import os
from pathlib import Path

from . import audio, fish_tts
from .expressive_voice import ExpressivePolicy
from .greeting_cache import GreetingCache, GreetingError, GreetingSpec
from .integration_profile import integration_config
from .private_credentials import (
    CredentialError, _close, _open_directory, _private_file, resolve_credentials,
)
from .safe_playback import play_wav


SAMPLES = {
    "neutral": ("neutral", "none", "Hi, I'm BMO. I'm right here."),
    "happy": ("happy", "chuckle", "We did it! That was fun."),
    "sad": ("sad", "none", "I'm here with you. We can take our time."),
    "surprised": ("surprised", "gasp", "Oh! I did not expect that."),
    "playful": ("happy", "playful_puff", "That was my tiny robot grumble."),
    "acting_cry": ("crying", "sob", "Let's pretend: I lost my favorite game."),
}


@dataclass(frozen=True, slots=True)
class SampleSpec(GreetingSpec):
    sample: str

    @property
    def spoken(self):
        from .pronunciation import spoken_bmo_name
        emotion, reaction, text = SAMPLES[self.sample]
        plain = spoken_bmo_name(text, self.fish_name_mode)
        plan = ExpressivePolicy().plan(plain, {
            "emotion": emotion, "reaction": reaction, "emotion_validation": "validated",
        }, model=self.fish_model)
        prefix = fish_tts.SOFT_AARON_DIRECTION if self.fish_delivery == "soft" else ""
        return prefix + plan.text

    def public(self):
        emotion, reaction, text = SAMPLES[self.sample]
        return GreetingSpec.public(self) | dict(
            text=text, sample=self.sample, emotion=emotion, reaction=reaction,
            purpose="aaron_expression_ab_v1")


def sample_cache(directory, sample):
    if sample not in SAMPLES:
        raise GreetingError("greeting_spec_changed")
    config = integration_config()
    spec = SampleSpec(config.fish_model, config.fish_voice_id, config.fish_name_mode,
                      config.fish_delivery, config.fish_gain, sample)
    return GreetingCache(Path(directory) / sample, spec)


@contextmanager
def _prepare_lock(cache):
    """Prevent two explicit preparations from purchasing the same asset twice."""
    directory = lock = None
    try:
        directory = _open_directory(cache.directory, create=True)
        lock = os.open(".expression-prepare.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW
                       | os.O_CLOEXEC | os.O_NONBLOCK, 0o600, dir_fd=directory)
        _private_file(os.fstat(lock), size_limit=0)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise GreetingError("greeting_busy") from None
        yield
    except (CredentialError, OSError):
        raise GreetingError("greeting_unsafe") from None
    finally:
        _close(lock)
        _close(directory)


def prepare(cache, *, allow_paid_api=False, synthesizer=None, credential_resolver=None):
    if allow_paid_api is not True:
        raise GreetingError("greeting_prepare_denied")
    with _prepare_lock(cache):
        try:
            with cache.load(require_approved=False) as asset:
                return asset.public() | {"reused": True}
        except GreetingError as error:
            if error.code != "greeting_missing":
                raise  # Keep corrupt/changed evidence; never resynthesize automatically.
        resolver = credential_resolver or resolve_credentials
        credentials = resolver(interactive=False)
        result = cache.prepare(live_opt_in=True, fish_synthesizer=synthesizer,
                               api_key=credentials.fish_api_key)
        return result | {"reused": False}


def parser():
    value = argparse.ArgumentParser(description="Supervised Aaron expression A/B assets")
    action = value.add_mutually_exclusive_group()
    action.add_argument("--plan", action="store_true")
    action.add_argument("--prepare", choices=SAMPLES)
    action.add_argument("--audition", choices=SAMPLES)
    action.add_argument("--approve", nargs=2, metavar=("SAMPLE", "SHA256"))
    action.add_argument("--status", choices=SAMPLES)
    value.add_argument("--allow-paid-api", action="store_true")
    value.add_argument("--cache-directory")
    return value


def run(argv=None, *, output=print, synthesizer=None, player=None, credential_resolver=None):
    args = parser().parse_args(argv)
    if not any((args.prepare, args.audition, args.approve, args.status)):
        output("PLAN: Aaron Soft / Beemo / gain 0.25; six fixed short A/B samples.")
        output("ASSET_PENDING / USER_APPROVAL: " + ", ".join(SAMPLES))
        output("No synthesis, credentials, playback or camera/motor operations executed.")
        return 0
    if args.allow_paid_api and not args.prepare:
        output("voice_benchmark_invalid_operation")
        return 2
    try:
        sample = args.prepare or args.audition or args.status or args.approve[0]
        root = Path(args.cache_directory) if args.cache_directory else Path.home() / ".cache/bmo/aaron-expression-v1"
        cache = sample_cache(root, sample)
        if args.prepare:
            result = prepare(cache, allow_paid_api=args.allow_paid_api,
                             synthesizer=synthesizer, credential_resolver=credential_resolver)
        elif args.approve:
            result = cache.approve(args.approve[1])
        else:
            with cache.load(require_approved=False) as asset:
                result = asset.public()
                if args.audition:
                    # The cache already has gain0.25. Never apply gain a second time.
                    playback = player or play_wav
                    playback(asset.path, gain=1.0)
                    result = result | {"auditioned": True}
        output(sample + " " + " ".join(f"{key}={value}" for key, value in sorted(result.items())))
        return 0
    except (GreetingError, CredentialError, audio.AudioError) as error:
        output("voice_benchmark " + error.code)
        return 2
    except (OSError, ValueError, KeyError, TypeError):
        output("voice_benchmark_invalid_operation")
        return 2


if __name__ == "__main__":
    raise SystemExit(run())
