"""Run with: python -m bmo_ai [--text]"""
import argparse
import os
from pathlib import Path
from openai import OpenAI
from .audio import record_wav, transcribe, speak
from .core import BMOBrain

def main():
    parser = argparse.ArgumentParser(description="BMO cloud voice MVP")
    parser.add_argument("--text", action="store_true", help="Text-only testing; no ALSA device required")
    parser.add_argument("--seconds", type=int, default=5, help="Push-to-talk recording length (1-20)")
    parser.add_argument("--mic-device", default=os.getenv("BMO_MIC_DEVICE"))
    parser.add_argument("--speaker-device", default=os.getenv("BMO_SPEAKER_DEVICE"))
    parser.add_argument("--mute", action="store_true", help="Disable TTS output")
    args = parser.parse_args()
    if not 1 <= args.seconds <= 20:
        parser.error("--seconds must be between 1 and 20")
    if not os.getenv("OPENAI_API_KEY"):
        parser.error("OPENAI_API_KEY is missing (set it as an environment variable)")
    client = OpenAI(timeout=30.0, max_retries=1)
    brain = BMOBrain(client)
    print("BMO ready! Type /quit to exit.")
    print("Text mode." if args.text else "Voice mode: Enter = record, /quit = exit.")
    while True:
        try:
            if args.text:
                message = input("You > ").strip()
            else:
                command = input("Press Enter to record, or /quit > ").strip()
                if command.lower() in ("/quit", "/exit"):
                    break
                path = record_wav(args.seconds, args.mic_device)
                try:
                    message = transcribe(client, path)
                finally:
                    Path(path).unlink(missing_ok=True)
                print("You >", message)
            if message.lower() in ("/quit", "/exit"):
                break
            if not message:
                continue
            answer = brain.ask(message)
            print("BMO >", answer)
            if not args.text and not args.mute:
                speak(client, answer, device=args.speaker_device)
        except (KeyboardInterrupt, EOFError):
            print("\nBye!")
            break
        except Exception as error:
            # Keep running so an intermittent audio/network error does not kill BMO.
            print(f"[error] {type(error).__name__}: {error}")

if __name__ == "__main__":
    main()
