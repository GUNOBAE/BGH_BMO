#!/usr/bin/env python3
"""Reuse the actual launcher E2E through the credential-aware manual entry point."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bmo_ai.screen_profiles import SCREEN_PROFILES
import check_integration_end_to_end as baseline


def run(profile, chromium):
    record = SCREEN_PROFILES[profile]
    original_child, original_hash = baseline.CHILD, baseline.FACE_SHA256
    try:
        child = original_child.replace("from bmo_ai.integration import main",
                                       "from bmo_ai.manual_launcher import main")
        child = child.replace("('socket.socket.connect','bmo_ai.cloud.create_client',",
                              "('bmo_ai.manual_launcher.resolve_credentials',"
                              "'bmo_ai.private_credentials.resolve_credentials',"
                              "'socket.socket.connect','bmo_ai.cloud.create_client',")
        child = child.replace("['--simulate','--port','0'",
                              "['--simulate','--screen-profile'," + repr(profile) + ",'--port','0'")
        assert child != original_child and "manual_launcher.resolve_credentials" in child
        baseline.CHILD, baseline.FACE_SHA256 = child, record["sha256"]
        result = baseline.run(record["default_path"], chromium)
        result["checks"][0] = ("actual manual launcher serves hash-pinned " + profile
                                + " through its selected external FaceServer profile")
        result["screen_profile"] = profile
        result["approval_status"] = record["approval_status"]
        result["credential_resolver_calls"] = 0
        return result
    finally:
        baseline.CHILD, baseline.FACE_SHA256 = original_child, original_hash


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--screen-profile", choices=tuple(SCREEN_PROFILES), default="v13.2")
    parser.add_argument("--chromium", default="/usr/bin/chromium")
    args = parser.parse_args()
    print(json.dumps(run(args.screen_profile, args.chromium), indent=2))
