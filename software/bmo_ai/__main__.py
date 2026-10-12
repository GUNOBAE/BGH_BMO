"""Run from software: python -m bmo_ai --text / --doctor / --benchmark."""
import argparse
import json
import os

from .cloud import CloudError, create_client
from .config import BMOConfig, ConfigError
from .core import BMOBrain
from .telemetry import configure_logging, event, measure


def parser():
    result = argparse.ArgumentParser(description="BMO cloud conversation and safe diagnostics")
    mode = result.add_mutually_exclusive_group()
    mode.add_argument("--text", action="store_true", help="Text conversation, no microphone or playback")
    mode.add_argument("--doctor", action="store_true", help="Read-only hardware/audio inventory; no API key needed")
    mode.add_argument("--list-audio", action="store_true", help="List ALSA devices without recording or playback")
    mode.add_argument("--benchmark", action="store_true", help="Offline mock timing; no API or hardware operations")
    result.add_argument("--seconds", type=int, help="Recording length, 1-20 seconds")
    result.add_argument("--mic-device", help="ALSA microphone name; uses existing default when omitted")
    result.add_argument("--speaker-device", help="ALSA output name; uses existing default when omitted")
    result.add_argument("--language", choices=("en", "ko", "auto"), help="Reply language preference (default en)")
    result.add_argument("--mute", action="store_true", help="Skip speech synthesis and playback")
    result.add_argument("--log-file", help="Optional rotating JSON event log (no transcript or audio)")
    return result


def public_error(error):
    if isinstance(error, CloudError):
        return error.code, error.safe_message
    from .audio import AudioError
    from .core import BrainResponseError
    if isinstance(error, (AudioError, BrainResponseError)):
        return getattr(error, "code", "response"), str(error)
    if isinstance(error, ValueError):
        return "input", "Input exceeds the configured limit or is invalid. Use a shorter message."
    return "unexpected", "The turn failed. Check configuration and try again."


def main(argv=None, *, client_factory=create_client, input_fn=input, output_fn=print):
    arguments = parser()
    args = arguments.parse_args(argv)
    if args.doctor or args.list_audio:
        from .audio import discover_devices
        result = {"audio": discover_devices()}
        if args.doctor:
            from .diagnostics import hardware_inventory
            result["hardware"] = hardware_inventory()
        output_fn(json.dumps(result, indent=2, ensure_ascii=True))
        return 0
    if args.benchmark:
        from .benchmark import benchmark
        output_fn(json.dumps(benchmark(), indent=2))
        return 0
    try:
        settings = {key: value for key, value in os.environ.items() if key.startswith("BMO_")}
        for field in ("seconds", "mic_device", "speaker_device", "language", "log_file"):
            if getattr(args, field) is not None:
                settings[f"BMO_{field.upper()}"] = str(getattr(args, field))
        config = BMOConfig.from_env(settings)
    except (ConfigError, ValueError) as error:
        arguments.error(str(error))
    try:
        configure_logging(config.log_level, config.log_file)
    except OSError:
        output_fn("Cannot open the event log. Check BMO_LOG_FILE or --log-file.")
        return 2
    try:
        client = client_factory(config)
    except Exception as error:
        code, message = public_error(error)
        event("startup_failed", code=code)
        output_fn(message)
        return 2
    app = None
    try:
        brain = BMOBrain(client, model=config.chat_model, max_turns=config.max_turns,
                         language=config.language, max_input_chars=config.max_input_chars,
                         max_history_chars=config.max_history_chars,
                         max_output_tokens=config.max_output_tokens)
        from .app import ConversationApp
        app = ConversationApp(brain, client, config, muted=args.mute or args.text)
        last_answer = None
        output_fn("BMO ready. /quit exits; /reset clears local conversation; /repeat repeats the last reply.")
        if not args.text and not args.mute:
            output_fn("Voice replies use an AI-generated voice.")
        event("ready", mode="text" if args.text else "voice", language=config.language)
        while True:
            app.idle()
            try:
                prompt = "You > " if args.text else "Enter to record; /quit, /reset or /repeat > "
                command = input_fn(prompt).strip()
                if command.lower() in ("/quit", "/exit"):
                    break
                if command.lower() == "/reset":
                    brain.reset()
                    last_answer = None
                    event("conversation_reset")
                    output_fn("Conversation cleared.")
                    continue
                if command.lower() == "/repeat":
                    if last_answer is None:
                        output_fn("No completed reply to repeat.")
                    else:
                        output_fn("BMO > " + last_answer)
                        app.playback(last_answer)
                    continue
                if not args.text and command:
                    output_fn("Press Enter to record, or use /quit, /reset or /repeat.")
                    continue
                with measure("turn"):
                    message = command if args.text else app.voice_input()
                    if not message.strip():
                        if not args.text:
                            output_fn("No speech recognized. Press Enter to try again.")
                        continue
                    if not args.text:
                        output_fn("You > " + message)
                    answer = app.answer(message)
                    last_answer = answer
                    output_fn("BMO > " + answer)
                    app.playback(answer)
            except (KeyboardInterrupt, EOFError):
                output_fn("\nBye!")
                break
            except Exception as error:
                app.failed()
                code, message = public_error(error)
                event("turn_failed", code=code)
                output_fn("[error] " + message)
    finally:
        if app is not None:
            app.idle()
        close = getattr(client, "close", None)
        if close:
            try:
                close()
            except Exception:
                event("client_close_failed")
        event("shutdown")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
