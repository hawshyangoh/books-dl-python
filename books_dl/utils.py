"""Cryptographic helpers - port of lib/books_dl/utils.rb.

The Books.com.tw viewer encrypts text resources with a simple repeating-key
XOR. The key is derived from the per-book ``download_token`` and the resource's
URL path via MD5 (to pick a split point) + SHA256.
"""

import hashlib
import random
import re
from urllib.parse import unquote_plus

# Mirrors the Ruby regex: %r{\Ahttps?://(.*?/){3}.*?(?<rest_part>/.+)\z}
_URL_RE = re.compile(r"https?://(.*?/){3}.*?(?P<rest_part>/.+)\Z")


class Utils:
    @staticmethod
    def _ruby_hex(pair):
        """Mimic Ruby String#hex: parse leading hex digits, 0 if none."""
        m = re.match(r"[0-9a-fA-F]+", pair)
        return int(m.group(0), 16) if m else 0

    @staticmethod
    def hex_to_byte(hex_str):
        """Convert a hex string into a list of byte integers (pairwise)."""
        if not isinstance(hex_str, str):
            return []
        return [
            Utils._ruby_hex(hex_str[i:i + 2])
            for i in range(0, len(hex_str) - 1, 2)
        ]

    @staticmethod
    def generate_key(url, download_token):
        """Derive the XOR key bytes for a given resource URL + download token."""
        if url is None:
            raise ValueError("url is nil")
        if not download_token:
            raise ValueError(f"download_token is nil for url={url!r}")

        if url.startswith(("http://", "https://")):
            match = _URL_RE.match(url)
            if not match or not match.group("rest_part"):
                raise ValueError(f"unexpected download url format: {url}")
            file_path = unquote_plus(match.group("rest_part"))
        else:
            file_path = unquote_plus(url if url.startswith("/") else f"/{url}")

        # MD5 of the path, summed in 4-char (16-bit) chunks, mod 64 -> split point.
        md5_chars = hashlib.md5(file_path.encode("utf-8")).hexdigest()
        partition = 0
        for i in range(0, len(md5_chars), 4):
            partition = (partition + int(md5_chars[i:i + 4], 16)) % 64

        decode_hex = hashlib.sha256(
            (
                download_token[:partition]
                + file_path
                + download_token[partition:]
            ).encode("utf-8")
        ).hexdigest()

        return Utils.hex_to_byte(decode_hex)

    @staticmethod
    def decode_xor(key, encrypted_content):
        """Repeating-key XOR decrypt; strips a leading UTF-8 BOM. Returns bytes."""
        if isinstance(encrypted_content, str):
            encrypted_content = encrypted_content.encode("utf-8")

        key_len = len(key)
        out = bytearray(len(encrypted_content))
        count = 0
        for idx, byte in enumerate(encrypted_content):
            out[idx] = byte ^ key[count]
            count += 1
            if count >= key_len:
                count = 0

        # Strip UTF-8 BOM (EF BB BF) if present.
        if len(out) >= 3 and out[0] == 0xEF and out[1] == 0xBB and out[2] == 0xBF:
            out = out[3:]

        return bytes(out)

    @staticmethod
    def img_checksum():
        """Random checksum sent with image requests (shuffled seed)."""
        # Original seed: %w[0 6 9 3 1 4 7 1 8 0 5 5 9 A A C]
        seed = list("0693147180559AAC")
        random.shuffle(seed)
        return "".join(seed)
