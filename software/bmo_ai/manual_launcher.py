"""Explicit manual launcher with optional private, hidden credential setup.

This module never copies credentials into the environment, CLI arguments,
effective configuration or logs. Simulation/help/config remain credential-free.
"""
import sys

from . import integration
from .private_credentials import CredentialError, resolve_credentials


def main(argv=None, *, lock_path=None):
    arguments = list(sys.argv[1:] if argv is None else argv)
    try:
        if "--setup-credentials" in arguments:
            if arguments != ["--setup-credentials"]:
                print("Use --setup-credentials on its own; no application was started.", file=sys.stderr)
                return 2
            resolve_credentials(force_prompt=True)
            print("Private credentials saved. No API request was made.")
            return 0
        return integration.main(arguments, lock_path=lock_path,
                                credential_resolver=resolve_credentials)
    except CredentialError as error:
        print(f"Credential setup failed ({error.code}): {error.safe_message}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
