"""Probe what POSIX tools are installed in the Trove container, then try
common multimodal operations across image / PDF / CSV / text files."""
from __future__ import annotations

import io
import os
import struct
import zlib
from pathlib import Path

from trove_sdk import TroveClient


def load_env(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        env[k.strip()] = v.strip()
    return env


# ── Tiny synthetic files ──────────────────────────────────────────────────────

def tiny_png() -> bytes:
    """Build a valid 4×4 red PNG without external deps."""
    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))
    sig    = b"\x89PNG\r\n\x1a\n"
    ihdr   = struct.pack(">IIBBBBB", 4, 4, 8, 2, 0, 0, 0)  # 4x4 RGB
    raw    = b"".join(b"\x00" + b"\xff\x00\x00" * 4 for _ in range(4))  # red rows
    idat   = zlib.compress(raw, 9)
    return sig + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")


def tiny_pdf() -> bytes:
    """Minimal one-page PDF with the text 'Hello'."""
    return (
        b"%PDF-1.1\n%\xc7\xec\x8f\xa2\n"
        b"1 0 obj <</Type/Catalog/Pages 2 0 R>> endobj\n"
        b"2 0 obj <</Type/Pages/Kids [3 0 R]/Count 1>> endobj\n"
        b"3 0 obj <</Type/Page/Parent 2 0 R/MediaBox [0 0 612 792]/Resources <</Font <</F1 4 0 R>>>>/Contents 5 0 R>> endobj\n"
        b"4 0 obj <</Type/Font/Subtype/Type1/BaseFont/Helvetica>> endobj\n"
        b"5 0 obj <</Length 44>>stream\nBT /F1 24 Tf 100 700 Td (Hello Trove) Tj ET\nendstream endobj\n"
        b"xref\n0 6\n0000000000 65535 f \n0000000018 00000 n \n0000000063 00000 n \n0000000112 00000 n \n0000000220 00000 n \n0000000280 00000 n \n"
        b"trailer <</Size 6/Root 1 0 R>>\nstartxref\n373\n%%EOF\n"
    )


# ── Probe runner ──────────────────────────────────────────────────────────────

PROBES = [
    # Group, command, comment
    ("Filesystem",    "ls -lh workspace/", "list files with sizes"),
    ("Filesystem",    "du -sh workspace/", "directory size"),
    ("Filesystem",    "stat workspace/img.png", "file metadata"),
    ("Identify",      "file workspace/img.png workspace/doc.pdf workspace/data.csv workspace/notes.md", "MIME sniffing"),
    ("Hashing",       "md5sum workspace/*", "MD5 hash all"),
    ("Hashing",       "sha256sum workspace/img.png", "SHA-256 hash one"),
    ("Bytes/text",    "wc -c workspace/img.png", "byte count"),
    ("Bytes/text",    "head -c 16 workspace/img.png | xxd", "hex of header"),
    ("Bytes/text",    "head -c 8 workspace/img.png | base64", "base64 first bytes"),
    ("Text process",  "head -n 3 workspace/data.csv", "first lines"),
    ("Text process",  "wc -l workspace/data.csv workspace/notes.md", "line counts"),
    ("Text process",  "cut -d, -f1 workspace/data.csv", "extract column"),
    ("Text process",  "awk -F, 'NR>1{s+=$2} END{print s}' workspace/data.csv", "aggregate column"),
    ("Text process",  "grep -c 'name' workspace/data.csv", "search count"),
    ("Compression",   "gzip -k workspace/data.csv && ls -lh workspace/data.csv*", "gzip"),
    ("Compression",   "tar -cf workspace/all.tar workspace/img.png workspace/doc.pdf && ls -lh workspace/all.tar", "tar archive"),
    # Optional / multimedia tools — most likely missing on bookworm-slim base
    ("Image (opt)",   "which identify convert exiftool 2>/dev/null; identify workspace/img.png 2>&1 || true", "ImageMagick / exiftool"),
    ("PDF (opt)",     "which pdftotext pdfinfo 2>/dev/null; pdftotext workspace/doc.pdf - 2>&1 || true", "poppler"),
    ("Audio (opt)",   "which ffprobe ffmpeg 2>/dev/null", "ffmpeg"),
    ("JSON (opt)",    "which jq 2>/dev/null", "jq"),
    ("Container",     "uname -a; cat /etc/os-release | head -3", "host info"),
    ("Container",     "command -v sh bash dash python python3", "shells / python"),
    ("Cleanup",       "rm -rf workspace/*", "delete all"),
]


def main() -> int:
    env = load_env(Path(__file__).resolve().parents[2] / ".env")
    api_key = env["TROVE_API_KEY"]
    namespace = "multimodal-probe"

    with TroveClient(api_key=api_key, namespace=namespace) as client:
        # Seed files
        client.upload("workspace/img.png", tiny_png())
        client.upload("workspace/doc.pdf", tiny_pdf())
        client.write("workspace/data.csv", "name,score\nalice,0.9\nbob,0.7\ncarol,0.85\n")
        client.write("workspace/notes.md", "# notes\n\n- learn posix\n- ship trove\n")

        for group, cmd, label in PROBES:
            print(f"\n── {group}: {label} ──")
            print(f"$ {cmd}")
            try:
                out = client.exec(cmd)
            except Exception as e:
                out = f"[error] {e}"
            print(out.rstrip() if out else "(no output)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
