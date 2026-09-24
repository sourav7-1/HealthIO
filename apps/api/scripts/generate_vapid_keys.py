"""Generate a VAPID key pair for Web Push.

Usage (from apps/api):
    uv run python -m scripts.generate_vapid_keys

Put the output in apps/api/.env. The private key is a secret; keep it out of git.
"""

import base64

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec


def main() -> None:
    key = ec.generate_private_key(ec.SECP256R1())
    private = key.private_numbers().private_value.to_bytes(32, "big")
    public = key.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    b64 = lambda b: base64.urlsafe_b64encode(b).rstrip(b"=").decode()  # noqa: E731
    print(f"HIO_WEBPUSH_VAPID_PUBLIC_KEY={b64(public)}")
    print(f"HIO_WEBPUSH_VAPID_PRIVATE_KEY={b64(private)}")


if __name__ == "__main__":
    main()
