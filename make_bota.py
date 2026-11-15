"""Build a BOTA image that makes the LK rewrite the bootloader from an
unsigned partition.

_sbl_bota_update (FUN_f486d3c8) reads the BOTA partition, checks
magic 0xbb5574aa and count in 1..100, then for each entry
{char name[32]; u32 size; u8 data[size]} looks the name up in the PIT and
writes the data to that partition.  Only these names get a signature
check: BOOTLOADER KEYSTORAGE TZSW VBMETA HARX VDDCAL_FW FLD TZAR RECOVERY.
EPBL is NOT among them -- and EPBL shares blocks with BOOTLOADER
(dev 8, lun 0, start 0x2800, 0xa00 blocks).

Usage: python make_bota.py <epbl.bin> <out.img> [name] [--pad 0x7C0000]
"""
import struct
import sys
from pathlib import Path

MAGIC = 0xBB5574AA
PAD = 0x7C0000          # BOTA partition: 0x3e00 blocks * 512


def build(name: str, payload: bytes, total: int = PAD) -> bytes:
    nb = name.encode() + b"\0"
    assert len(nb) <= 32, "name too long"
    nb = nb.ljust(32, b"\0")

    size = len(payload)
    entry = nb + struct.pack("<I", size) + payload
    entry += b"\0" * (-size % 4)              # loop advances by 0x24 + (size & ~3)

    body = struct.pack("<II", MAGIC, 1) + entry
    if len(body) > total:
        raise SystemExit(f"image too big: {len(body):#x} > {total:#x}")
    return body.ljust(total, b"\0")


def main():
    src = Path(sys.argv[1])
    dst = Path(sys.argv[2])
    name = sys.argv[3] if len(sys.argv) > 3 else "EPBL"
    payload = src.read_bytes()
    img = build(name, payload)
    dst.write_bytes(img)
    print(f"{dst}: {len(img):#x}  entry name={name!r} size={len(payload):#x}")
    # self-check
    magic, cnt = struct.unpack_from("<II", img, 0)
    nm = img[8:40].split(b"\0")[0].decode()
    sz = struct.unpack_from("<I", img, 40)[0]
    print(f"check: magic={magic:#x} count={cnt} name={nm!r} size={sz:#x}")
    assert magic == MAGIC and cnt == 1 and nm == name and sz == len(payload)


if __name__ == "__main__":
    main()
