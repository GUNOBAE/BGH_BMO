"""Review user-owned PCM WAVs locally. Default mode never writes or plays."""
import argparse
import json
from pathlib import Path

from .features.contracts import FeatureError
from .features.media.library import install_wav, load_library, retire_import, review_wav


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audio", type=Path)
    parser.add_argument("--sha256")
    parser.add_argument("--content-root", type=Path)
    parser.add_argument("--media-id")
    parser.add_argument("--title")
    parser.add_argument("--source", default="user_owned")
    parser.add_argument("--license-id")
    parser.add_argument("--license-reviewed", action="store_true")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--install", action="store_true")
    mode.add_argument("--retire", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.retire:
            if args.content_root is None or not args.media_id or not args.sha256 or args.audio is not None:
                raise FeatureError("media_review_required")
            report = retire_import(args.content_root, args.media_id, expected_sha256=args.sha256)
        elif args.audio is not None:
            review = review_wav(args.audio, expected_sha256=args.sha256)
            report = review.public()
            if args.install:
                if args.content_root is None or not args.sha256 or not args.title:
                    raise FeatureError("media_review_required")
                report = install_wav(review, args.content_root, args.media_id, title=args.title,
                    source=args.source, license_id=args.license_id, license_reviewed=args.license_reviewed)
        elif args.install:
            raise FeatureError("media_review_required")
        else:
            items = load_library(args.content_root) if args.content_root is not None else ()
            report = {"status": "AVAILABLE_FOR_REVIEW" if items else "ASSET_PENDING", "items": len(items),
                      "runtime_enabled": False, "playback": "NOT_RUN", "cue_kind": "none"}
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    except FeatureError as error:
        print(json.dumps({"status": "REJECTED", "code": error.code, "playback": "NOT_RUN"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
