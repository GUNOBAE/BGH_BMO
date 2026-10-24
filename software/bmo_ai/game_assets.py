"""Review an already-owned local game ZIP. Default mode never writes files."""
import argparse
import json
from pathlib import Path

from .features.contracts import FeatureError
from .features.games.assets import inventory_known_assets, review_archive, install_reviewed


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--sha256")
    parser.add_argument("--content-root", type=Path)
    parser.add_argument("--pack-id")
    parser.add_argument("--license-id")
    parser.add_argument("--license-reviewed", action="store_true")
    parser.add_argument("--web-content-reviewed", action="store_true")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--install", action="store_true")
    parser.add_argument("--replace-existing", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.archive is None:
            if args.install:
                raise FeatureError("game_review_required")
            report = inventory_known_assets()
        else:
            review = review_archive(args.archive, expected_sha256=args.sha256)
            report = review.public()
            if args.install:
                if not args.sha256 or args.content_root is None or args.pack_id is None:
                    raise FeatureError("game_review_required")
                report = install_reviewed(review, args.content_root, args.pack_id,
                    license_id=args.license_id, license_reviewed=args.license_reviewed,
                    web_content_reviewed=args.web_content_reviewed, replace_existing=args.replace_existing)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    except FeatureError as error:
        print(json.dumps({"status": "REJECTED", "code": error.code, "runtime": "NOT_RUN"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
