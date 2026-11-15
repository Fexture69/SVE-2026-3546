# SVE-2026-3546 → SM-S926B (s5e9945, S926BXXUIEZJB) — port status

Everything below is read from this device's own firmware unless stated otherwise.

## What is built and verified

| Artifact | State |
|---|---|
| `s5e9945.json` profile | 13 images, part map ECDSA-validated against the stock tee/ree keys from `fld.bin` |
| `sign_bl.py --soc s5e9945` | re-signs BL + PIT; safety stubs + D2 patch verified inside the output tar |
| `loader/` bundle | 13 entries + PIT, sizes and SHA-256 verified by `verify_bundle.py` |
| `loader.bin` / `loader_probe.bin` | 0xee0 bytes, contract + entry verified, Unicorn emulation passes |
| `loader_ping_vis.bin` | prints "PACKET HIT / CTL LANDED" on the LK screen when hijacked |
| `flash_and_fuse.py` | USB path works: handshake, PIT read, staging, walk, packet, triggers |

**The only missing piece is a primitive that gets code execution in the LK.**

## Memory map (from the reservation function at 0xf488ae28)

```
sboot,rmem (heap)     0xf4800000 + 0x1800000  -> 0xf6000000
                       0xc1400000 + 0x200000   -> 0xc1600000
sboot,framebuffer      0xfa200000 + 0x1124000  -> 0xfb424000
sboot,mz (JPEG decode) 0xfb600000 + 0x1000     -> 0xfb601000   (highest)
SMC-derived            0x8ffff000 + 0x1000
```

Decompression buffers (64-bit alias form): packet `0x8_9801_0000`,
output `0x8_9A01_0000` (phys `0x9A010000`), parallel `0x8_9E01_0000`.

Send callback: `0xf4c798b8` (thunk at `0xf4817564` does `ldr x2,[x8,#0x8b8]`).

## Primitives examined, and why each fails

1. **Decompression accumulator** (`0xf48cca54`)
   `dst = 0x8_9A01_0000 + x22`, and `mov x22, xzr` resets per packet.
   Callback is `0xf4c798b8 - 0x9A010000 = 0x5AC798B8` (1.45 GB) away.
   The published exploit's `walk = write - out_base` assumes one accumulation.

2. **S24 FE comparison** (`S721BXXS7CYJ5`, on disk at `s24fe/sboot.bin`)
   Same `sboot.bin` size 0x899330, same buffers, same `mov x22, xzr`,
   and upstream's `comp_output` detector matches neither build.
   => the accumulator model does not hold on s5e9945 at all.

3. **Reply buffer / setter pair** — `0xf48452d8` is a bare vtable dispatch
   (`ldr x5,[x8,#0x38]; br x5`), no copy, no length check.

4. **Oversized command packet** — sweep: 64 B and 256 B accepted, 1024 B rejected;
   a 1280 B packet wedges the LK. The request size is fixed at 1 KB.

5. **`0xcf800000` staging copy** (`0xf48889e4`) — length is `0x200 - off`
   (`mov w10,#0x200` at `0xf48886c4`), so at most 512 B.

6. **SOD record packet** (`0xf486e0fc`) — `len = count*16 + 0x20` with
   `count = ldrb [x19,#8]` and an explicit `cmp w1,#0x3f; b.lo` guard.
   At count 62 the length is exactly the 1024-byte allocation. Bounded.

7. **SOD record offset stream** — genuinely unbounded and persistent:
   base `0xc8000000` (`[0xf4ce19b8]`), offset `[0xf4ce19c4]` advanced +0x10 per
   16-byte record (`0xf48a7c28..0xf48a7c44`), zeroed once per session
   (`0xf48a6494`). `0xf4c798b8 - 0xc8000000 = 0x2CC798B8` == the WALK_LEN
   already in `offsets.py`. But throughput is ~1 KB per USB round trip
   (<=62 records/command) and it must survive writing 750 MB through live DRAM.

## Second vulnerability pair (CVE-2026-21155), confirmed on this model

- `check_signature` fails open: allowlist at `0xf49a9b08`, 28 entries,
  terminator `cmp x22,#0xe0` at `0xf4898b8c`, no-match path `mov w20, wzr`.
  The 28 names are identical to the published S731 list and `UP_PARAM` is absent
  => an unsigned `up_param` (and UL_KEYS, SSP, ...) is flashable on a locked device.
- Boot-logo decoder has no output bound: decode loop at `0xf4856ce0`, bound is the
  JPEG SOF height only, destination `*(0xf4a03dd0)`, buffer `0xfb600000 + 0x1000`.
  The overrun runs upward into `0xfc900000` (DBGCORE shared block, 40 refs)
  and beyond; no function pointer has been found in that direction yet.

## Hardware blockers

- EUB/BootROM fused off (April 2025 change; an SM-S921B on the May-2025 patch
  already fails to enter it), so Houston and exynos-usbdl are unusable.
- No public s5e9945 S-LK exploit exists (repeated net sweeps).

## Next steps, in order of expected value

1. Enumerate every handler in the download sub-command dispatcher and look for a
   path with a host-controlled length that is not one of the six checked above.
2. If nothing, attempt the SOD offset stream: drive `[0xf4ce19c4]` to 0x2CC798B8
   with ~1 KB per command and place the ctl as the final record.
3. Fall back to the `up_param` primitive: find what is loaded at `0xfb600000`+
   before the logo is drawn (kernel/DTB/AVB) and whether the overrun can patch it.

## Safety notes

- Do not run `flash_and_fuse.py` without `--packet`/`--overflow`/ping payloads:
  the real loader calls `fuse_key2` (irreversible).
- Long walks write hundreds of MB through the `UL_KEYS` staging partition;
  reflash stock (AP+BL+CP+HOME_CSC) afterwards to clear boot errors.
