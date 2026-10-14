# -*- coding: utf-8 -*-
"""
Simple, dependency-free symmetric encryption (stdlib only).
Used to store Zabbix server passwords encrypted in the database.

Method: a key derived from SECRET_KEY drives a SHA-256 based CTR keystream (XOR)
plus an HMAC-SHA256 integrity check. Not as strong as AES, but clearly safer than
storing plaintext, and it requires no external package.
"""
import os
import hmac
import struct
import base64
import hashlib


def _derive_key(secret: str) -> bytes:
    return hashlib.sha256(("zbx-secretbox:" + (secret or "")).encode("utf-8")).digest()


def _keystream(key: bytes, nonce: bytes, length: int) -> bytes:
    out = bytearray()
    counter = 0
    while len(out) < length:
        out += hashlib.sha256(key + nonce + struct.pack(">I", counter)).digest()
        counter += 1
    return bytes(out[:length])


def encrypt_secret(plaintext: str, secret: str) -> str:
    key = _derive_key(secret)
    nonce = os.urandom(16)
    pt = (plaintext or "").encode("utf-8")
    ks = _keystream(key, nonce, len(pt))
    ct = bytes(a ^ b for a, b in zip(pt, ks))
    mac = hmac.new(key, nonce + ct, hashlib.sha256).digest()[:16]
    return base64.b64encode(nonce + ct + mac).decode("ascii")


def decrypt_secret(token: str, secret: str) -> str:
    key = _derive_key(secret)
    raw = base64.b64decode(token.encode("ascii"))
    if len(raw) < 32:
        raise ValueError("invalid ciphertext")
    nonce, body, mac = raw[:16], raw[16:-16], raw[-16:]
    expected = hmac.new(key, nonce + body, hashlib.sha256).digest()[:16]
    if not hmac.compare_digest(mac, expected):
        raise ValueError("integrity check failed (wrong key?)")
    ks = _keystream(key, nonce, len(body))
    return bytes(a ^ b for a, b in zip(body, ks)).decode("utf-8")
