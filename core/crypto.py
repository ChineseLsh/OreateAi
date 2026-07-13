"""RSA 加密模块 — 用 getticket 返回的 PKCS#1 公钥加密密码"""

import base64
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.serialization import load_der_public_key


def _pkcs1_pem_to_der(pk_pem: str) -> bytes:
    lines = pk_pem.strip().splitlines()
    b64 = "".join(l for l in lines if not l.startswith("-----"))
    return base64.b64decode(b64)


def parse_public_key(pk_pem: str):
    der = _pkcs1_pem_to_der(pk_pem)
    if b"BEGIN RSA PUBLIC KEY" in pk_pem.encode() or True:
        from cryptography.hazmat.primitives.serialization import load_pem_public_key
        try:
            return load_pem_public_key(pk_pem.encode())
        except Exception:
            pass
        # PKCS#1 DER → 包装为 PKCS#8 DER
        # RSAPublicKey PKCS#1 包在 SubjectPublicKeyInfo 里
        from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
        from cryptography.hazmat.backends import default_backend
        import struct

        # 手动构造 PKCS#8 wrapper
        # SEQUENCE { SEQUENCE { OID rsaEncryption, NULL }, BIT STRING { pkcs1_der } }
        oid_rsa = bytes([0x06, 0x09, 0x2a, 0x86, 0x48, 0x86, 0xf7, 0x0d, 0x01, 0x01, 0x01])
        null_param = bytes([0x05, 0x00])

        def make_seq(contents: bytes) -> bytes:
            return _asn1_seq(contents)

        def _asn1_seq(data: bytes) -> bytes:
            return bytes([0x30]) + _asn1_len(len(data)) + data

        def _asn1_len(length: int) -> bytes:
            if length < 0x80:
                return bytes([length])
            enc = []
            tmp = length
            while tmp > 0:
                enc.insert(0, tmp & 0xFF)
                tmp >>= 8
            return bytes([0x80 | len(enc)] + enc)

        def _asn1_bitstring(data: bytes) -> bytes:
            content = bytes([0x00]) + data  # 0 unused bits
            return bytes([0x03]) + _asn1_len(len(content)) + content

        algo_seq = _asn1_seq(oid_rsa + null_param)
        spki = _asn1_seq(algo_seq + _asn1_bitstring(der))

        return load_der_public_key(spki)


def encrypt_password(password: str, pk_pem: str) -> str:
    pub_key = parse_public_key(pk_pem)
    encrypted = pub_key.encrypt(password.encode(), padding.PKCS1v15())
    return base64.b64encode(encrypted).decode()
