#!/usr/bin/env python3
"""
Scrambles (encrypts) private output files before they go to the public repo.
Key comes from the DATA_KEY environment variable (a GitHub secret). Without the key nothing private is written.

  python3 collector/vault.py encrypt <file>   -> writes <file>.enc and deletes <file>
  python3 collector/vault.py decrypt <file>   -> prints the plain content of <file>.enc
  python3 collector/vault.py seal <file>      -> writes <file>.enc using the PUBLIC lock (config/note_pub.pem),
                                                 no secret needed; only the app can open it. Deletes <file>.
"""
import base64, os, sys

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

MAGIC = b"V1"


class NoKey(Exception):
    pass


def key():
    k = os.environ.get("DATA_KEY", "").strip()
    if not k:
        raise NoKey("DATA_KEY is not set - private files are not written")
    return base64.b64decode(k)


def enc(data: bytes) -> bytes:
    n = os.urandom(12)
    return MAGIC + n + AESGCM(key()).encrypt(n, data, None)


def dec(blob: bytes) -> bytes:
    assert blob[:2] == MAGIC, "not a vault file"
    return AESGCM(key()).decrypt(blob[2:14], blob[14:], None)


def write(path: str, text: str):
    """Write `path`.enc (encrypted). Never leaves a plain copy."""
    blob = enc(text.encode("utf-8"))  # encrypt FIRST: with no key this raises before the old file is touched
    with open(path + ".enc", "wb") as f:
        f.write(blob)
    if os.path.exists(path):
        os.remove(path)


def save_or_print(path: str, text: str):
    """With the key: save scrambled. Without it (phone-message task): just print, save nothing."""
    if os.environ.get("DATA_KEY", "").strip():
        write(path, text)
    else:
        print("RESULT_JSON " + text)


def read(path: str, default=None):
    if os.path.exists(path + ".enc"):
        with open(path + ".enc", "rb") as f:
            return dec(f.read()).decode("utf-8")
    if os.path.exists(path):  # old plain file: read once, it will be re-written encrypted
        with open(path, encoding="utf-8-sig") as f:
            return f.read()
    return default


def seal(path: str):
    """Public-key lock: anyone can lock, only the app (private key inside the password vault) can unlock."""
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(here, "config", "note_pub.pem"), "rb") as f:
        pub = serialization.load_pem_public_key(f.read())
    with open(path, "rb") as f:
        data = f.read()
    k, n = AESGCM.generate_key(256), os.urandom(12)
    wrapped = pub.encrypt(k, padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None))
    with open(path + ".enc", "wb") as f:
        f.write(b"P1" + len(wrapped).to_bytes(2, "big") + wrapped + n + AESGCM(k).encrypt(n, data, None))
    os.remove(path)


if __name__ == "__main__":
    cmd, p = sys.argv[1], sys.argv[2]
    if cmd == "encrypt":
        with open(p, encoding="utf-8") as f:
            write(p, f.read())
        print("encrypted ->", p + ".enc")
    elif cmd == "seal":
        seal(p)
        print("sealed ->", p + ".enc")
    elif cmd == "decrypt":
        print(read(p))
