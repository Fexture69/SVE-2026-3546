# SVE-2026-3546 - Unsigned code loading for S-LK

This PoC uses it to fuse Secure Boot KEY2 and flash re-signed bootloader files on
s5e8825, s5e9925 and s5e9945.

The `s5e9945` profile targets the SM-S926B (Exynos 2400) on `S926BXXUIEZJB`.
Its part map is verified against the stock tee/ree keys: every signed chunk of
every BL member validates, and the resolver derives `out_base = 0xc8000000`
(the decompression accumulator, *not* the `0xd1800000` parallel staging area).

## Demo

![Demo](AUTH-preview.gif)

## Requirements

- Python 3
- `clang` + `ld.lld` + `llvm-objcopy` (LLVM) **or** `aarch64-linux-gnu` binutils
- libusb access to the phone (`libusb-package` is enough on Windows)

Install the Python packages:

```sh
python3 -m pip install -r requirements.txt
```

Build the Android resigner:

```sh
cargo build --release --manifest-path android-resigner/Cargo.toml
```

Android signer commands: [android-resigner/README.md](android-resigner/README.md).

## 1. Sign BL and PIT

```sh
./sign_bl.py --soc s5e9945 BL.tar.md5 device.pit
```

Outputs:

```text
BL_RESIGNED.tar.md5
device_RESIGNED.pit
```

## 2. Find offsets

```sh
./resolve.py --soc s5e9945 BL_RESIGNED.tar.md5
```

## 3. Build the loader

```sh
./build_loader.sh --soc s5e9945 BL_RESIGNED.tar.md5 device_RESIGNED.pit
```

On Windows use PowerShell with LLVM instead of the bash script:

```powershell
powershell -File build_loader.ps1 -Soc s5e9945 -Bl BL_RESIGNED.tar.md5 -Pit device_RESIGNED.pit
```

Outputs:

```text
loader.bin
loader.elf
loader/bundle.bin
```

## 4. Run

Put the phone in download mode (Odin), then:

```sh
./flash_and_fuse.py --soc s5e9945
```

The payload writes 24.3 MiB across 12 partitions on LUN 0/1/4 and finally the
PIT, verifying every partition by read-back SHA-256.

## Disclaimer

This repo is for research purposes. No one else than you is responsible for what you do to your phone, especially if you're modifying it with LLMs.
