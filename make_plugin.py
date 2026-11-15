"""Build a GREENTEA-format H-Arx plugin for the DRM_PLUGIN partition.

Format recovered from FUN_f4950cb4 (the LK's plugin header parser):
    0x00  "GREENTEA"          8-byte magic
    0x08  u32 size
    0x48  u32 header size     -> plugin entry = base + header_size
and the LK registers (base, size, entry) with HARX in EL3 via SMC 0xc6000010.

The entry stub delays briefly, restores x9/x10, and returns, so the boot
continues normally if the plugin is called.

Usage: python make_plugin.py <out.bin> [iters]
"""
import struct
import sys
from pathlib import Path

PART = 0x80000          # DRM_PLUGIN: 0x400 blocks * 512
HDR = 0x1000             # plugin entry = base + HDR
MAGIC = b"GREENTEA"


def build(iters: int) -> bytes:
    img = bytearray(PART)
    img[0:8] = MAGIC
    struct.pack_into("<I", img, 8, PART - HDR)      # plugin body size
    struct.pack_into("<I", img, 0x48, HDR)          # header size == entry offset

    code = b""
    code += struct.pack("<I", 0xA9BF2BE9)            # stp x9, x10, [sp, #-0x10]!
    code += struct.pack("<I", 0xD2A00009 | ((iters >> 16) & 0xFFFF) << 5)  # movz x9, hi, lsl16
    code += struct.pack("<I", 0xF1000529)            # subs x9, x9, #1
    loop = HDR + len(code) - 4
    off = (HDR - (HDR + 8)) >> 2
    code += struct.pack("<I", 0x54000000 | ((off & 0x7FFFF) << 5) | 1)     # b.ne
    code += struct.pack("<I", 0xA8C12BE9)            # ldp x9, x10, [sp], #0x10
    code += struct.pack("<I", 0xD65F03C0)            # ret
    img[HDR:HDR + len(code)] = code
    return bytes(img)


def main():
    dst = Path(sys.argv[1])
    iters = int(sys.argv[2], 0) if len(sys.argv) > 2 else 0x40000000
    img = build(iters)
    dst.write_bytes(img)
    print(f"{dst}: {len(img):#x}  magic={img[:8]!r} size={struct.unpack_from('<I',img,8)[0]:#x}"
          f" hdr={struct.unpack_from('<I',img,0x48)[0]:#x} entry_off={HDR:#x} iters={iters:#x}")


if __name__ == "__main__":
    main()
