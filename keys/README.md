# Update signing key

`update-signing.pub` is the Ed25519 public key that installed AlvaOS systems use
to verify updates. Without it, systems refuse to install AlvaOS updates.

Create it once with:

```
python3 scripts/release/sign_update.py keygen
```

Commit `update-signing.pub`, and store the printed private key as the GitHub
Actions secret `ALVAOS_UPDATE_SIGNING_KEY` (plus an offline backup). Never
commit the private key. See `docs/RELEASE.md`.
