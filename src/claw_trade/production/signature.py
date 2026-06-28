from __future__ import annotations

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.hazmat.primitives import serialization


def verify_ed25519_signature(*, public_key_pem: bytes, data: bytes, signature: bytes) -> bool:
    public_key = serialization.load_pem_public_key(public_key_pem)
    if not isinstance(public_key, Ed25519PublicKey):
        raise ValueError("更新公钥必须是 Ed25519 公钥。")
    try:
        public_key.verify(signature, data)
    except InvalidSignature:
        return False
    return True
