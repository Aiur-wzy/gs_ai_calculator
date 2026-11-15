"""Read an RSA public PEM for the product's offline release verifier."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from genshin_app.release_pair_trust import ALGORITHM, build_trust_verifier


def export_public_key(pem: Path, *, openssl: str) -> dict:
    data = pem.read_bytes()
    if len(data) > 16 * 1024 or not data.startswith(b"-----BEGIN PUBLIC KEY-----"):
        raise ValueError("请提供通过 openssl pkey -pubout 导出的公钥 PEM")
    result = subprocess.run(
        [openssl, "rsa", "-pubin", "-noout", "-text"],
        input=data, capture_output=True, check=True, timeout=15,
    ).stdout.decode("ascii")
    match = re.search(r"^Modulus:\s*([0-9a-f:\s]+)^Exponent:\s*(\d+)", result, re.MULTILINE)
    if match is None:
        raise ValueError("无法识别 RSA 公钥的模数及指数")
    modulus = int(re.sub(r"[:\s]", "", match[1]), 16)
    exponent = int(match[2])
    key_id = hashlib.sha256(f"{modulus:x}:{exponent}".encode("ascii")).hexdigest()
    record = {"key_id": key_id, "algorithm": ALGORITHM, "modulus_hex": f"{modulus:x}", "exponent": exponent}
    build_trust_verifier((record,))
    return record


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pem", type=Path)
    parser.add_argument("--openssl", default="openssl")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        record = export_public_key(args.pem, openssl=args.openssl)
        with args.output.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(record, stream, indent=2)
            stream.write("\n")
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        parser.exit(1, f"公钥导出失败（{type(error).__name__}）。请检查 PEM、OpenSSL 和输出路径。\n")
    print(record["key_id"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
