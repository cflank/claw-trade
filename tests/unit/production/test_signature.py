from __future__ import annotations

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from claw_trade.production.signature import verify_ed25519_signature


def test_verify_ed25519_signature_accepts_matching_signature() -> None:
    private_key = Ed25519PrivateKey.generate()
    public_key_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    data = b"release bytes"
    signature = private_key.sign(data)

    assert verify_ed25519_signature(public_key_pem=public_key_pem, data=data, signature=signature) is True
    assert verify_ed25519_signature(public_key_pem=public_key_pem, data=b"tampered", signature=signature) is False


def test_verify_ed25519_signature_rejects_non_ed25519_key() -> None:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_key_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )

    with pytest.raises(ValueError, match="Ed25519"):
        verify_ed25519_signature(public_key_pem=public_key_pem, data=b"release bytes", signature=b"signature")
