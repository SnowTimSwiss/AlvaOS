#!/usr/bin/env python3
"""
Sign AlvaOS release packages.

  # once: create the key pair
  python3 scripts/release/sign_update.py keygen
  #   -> prints the private key (store it as the GitHub secret
  #      ALVAOS_UPDATE_SIGNING_KEY, and offline as a backup)
  #   -> writes keys/update-signing.pub (commit this file)

  # for each release (CI does this automatically)
  ALVAOS_UPDATE_SIGNING_KEY=... python3 scripts/release/sign_update.py sign build/package/*.deb
  #   -> writes <package>.deb.sig next to each package

  # check a signature
  python3 scripts/release/sign_update.py verify alvaos-system_1.0.0_amd64.deb
"""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

import update_signing  # noqa: E402

PUBLIC_KEY_FILE = ROOT / "keys" / "update-signing.pub"


def keygen():
    if PUBLIC_KEY_FILE.exists():
        print(f"{PUBLIC_KEY_FILE} already exists. Delete it first if you really want a new key;")
        print("installed systems will then only accept updates signed with the new key.")
        return 1
    private_b64, public_b64 = update_signing.generate_keypair()
    PUBLIC_KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
    PUBLIC_KEY_FILE.write_bytes(public_b64 + b"\n")
    print(f"Public key written to {PUBLIC_KEY_FILE.relative_to(ROOT)} (commit it).")
    print()
    print("PRIVATE KEY (store as GitHub secret ALVAOS_UPDATE_SIGNING_KEY, never commit it):")
    print(private_b64.decode())
    return 0


def sign(paths):
    key = os.environ.get("ALVAOS_UPDATE_SIGNING_KEY", "").strip()
    if not key:
        print("ALVAOS_UPDATE_SIGNING_KEY is not set")
        return 1
    for path in paths:
        signature = update_signing.sign_file(path, key.encode())
        sig_path = update_signing.signature_path_for(path)
        Path(sig_path).write_bytes(signature + b"\n")
        # Verify with the committed public key so a mismatched secret fails the build.
        update_signing.verify_installed_key(path, sig_path, str(PUBLIC_KEY_FILE))
        print(f"signed {path}")
    return 0


def verify(paths):
    for path in paths:
        digest = update_signing.verify_installed_key(
            path, update_signing.signature_path_for(path), str(PUBLIC_KEY_FILE)
        )
        print(f"OK {path} sha256={digest}")
    return 0


def main(argv):
    if len(argv) >= 1 and argv[0] == "keygen":
        return keygen()
    if len(argv) >= 2 and argv[0] == "sign":
        return sign(argv[1:])
    if len(argv) >= 2 and argv[0] == "verify":
        return verify(argv[1:])
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
