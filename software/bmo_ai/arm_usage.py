"""Record the operator's completed B1 first test for ordinary Candidate use.

Default status reads private metadata only. --record-first-test is a historical
attestation after the supervised test, not permission to move a servo now. This
helper checks existing source and dedicated USB identity, but never opens the
serial port, sends a gesture/reset, changes firmware, or enables preferences.
"""
import argparse
import json
from pathlib import Path

from .features.contracts import FeatureError
from .features.motion.daily_usage import ArmUsageStore, DailyArmController, CHECKS
from .features.motion.legacy_host import LegacyArmError


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path)
    parser.add_argument('--record-first-test', action='store_true')
    parser.add_argument('--reviewed-code-sha')
    parser.add_argument('--test-session-id')
    for check in CHECKS:
        parser.add_argument('--'+check.replace('_','-'), action='store_true')
    args = parser.parse_args(argv)
    try:
        store = ArmUsageStore(args.directory)
        if args.record_first_test:
            store.commission(reviewed_sha=args.reviewed_code_sha, test_session_id=args.test_session_id,
                checks={key:getattr(args,key) for key in CHECKS})
        elif args.reviewed_code_sha or args.test_session_id or any(getattr(args,key) for key in CHECKS):
            parser.error('first-test evidence requires explicit --record-first-test')
        report = DailyArmController(store=store).snapshot()
        report.update(status='FIRST_TEST_RECORDED' if args.record_first_test else 'READ_ONLY',
            transport_opened=False, motion_requested=False, firmware_changed=False,
            next_action='Restart BMO Candidate, open Move arms, and choose Use arms.' if args.record_first_test
                        else 'Record the completed supervised first test before daily arm use.')
        print(json.dumps(report, sort_keys=True))
        return 0
    except (FeatureError, LegacyArmError, OSError):
        print(json.dumps(dict(status='REJECTED', code='arm_first_test_record_not_saved',
                              transport_opened=False, motion_requested=False, firmware_changed=False)))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
