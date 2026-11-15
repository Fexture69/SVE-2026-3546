"""Patch epbl.bin so its entry branches into a stub before continuing normally.

EPBL layout (s5e9945, from sboot.bin[0:0x16000]):
  0x00  header (size 0xb0, "head" magic)
  0x10  entry: b #0x20
  0x20  real code
  0x13c26..0x15df0  zero padding (safe home for a stub)

Stub is a busy-wait so the boot takes measurably longer; it touches only x9
and then branches back to 0x24, so behaviour is otherwise unchanged.

Usage: python make_epbl.py <epbl.bin> <out.bin> [iterations]
"""
import struct
import sys
from pathlib import Path

ENTRY_BRANCH = 0x10          # instruction that jumps to the real entry
REAL_ENTRY = 0x20
STUB = 0x14000               # inside the zero run 0x13c26..0x15df0


def b_insn(pc, target):
    off = (target - pc) >> 2
    assert -(1 << 25) <= off < (1 << 25), "branch out of range"
    return 0x14000000 | (off & 0x3FFFFFF)


def build_stub(iters: int) -> bytes:
    code = b""
    hi = (iters >> 16) & 0xFFFF
    code += struct.pack("<I", 0x52A00000 | (hi << 5) | 9)      # movz w9, hi, lsl 16
    code += struct.pack("<I", 0x71000529)                       # subs w9, w9, #1
    loop_pc = STUB + 4
    off = (loop_pc - (loop_pc - 0)) >> 2                        # placeholder
    off = (STUB - (STUB + 8)) >> 2                              # back to subs
    code += struct.pack("<I", 0x54000000 | ((off & 0x7FFFF) << 5) | 0x1)   # b.ne
    code += struct.pack("<I", b_insn(STUB + 12, REAL_ENTRY))
    return code


def main():
    src, dst = Path(sys.argv[1]), Path(sys.argv[2])
    iters = int(sys.argv[3], 0) if len(sys.argv) > 3 else 0x60000000
    img = bytearray(src.read_bytes())
    assert len(img) == 0x16000, hex(len(img))

    orig = struct.unpack_from("<I", img, ENTRY_BRANCH)[0]
    assert orig == 0x14000004, f"unexpected entry branch {orig:#x}"

    stub = build_stub(iters)
    end = STUB + len(stub)
    assert all(b == 0 for b in img[STUB:end]), "stub area is not empty"
    assert end < 0x15df0

    img[STUB:end] = stub
    struct.pack_into("<I", img, ENTRY_BRANCH, b_insn(ENTRY_BRANCH, STUB))
    dst.write_bytes(img)

    print(f"stub at {STUB:#x} ({len(stub)} bytes), iters={iters:#x}")
    print(f"entry {ENTRY_BRANCH:#x}: {orig:#x} -> {b_insn(ENTRY_BRANCH, STUB):#x}")
    print(f"wrote {dst} ({len(img):#x})")


if __name__ == "__main__":
    main()
