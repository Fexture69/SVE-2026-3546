# SVE-2026-3546 - Unsigned code loading for S-LK

This PoC uses it to fuse Secure Boot KEY2 and flash re-signed bootloader files on s5e8825 and s5e9925.

An offline `s5e9945` profile and resolver path are included for the SM-S926B
`S926BXXUIEZJB` firmware layout. The device-side `flash_and_fuse.py` path is
intentionally not enabled for `s5e9945`; the profile is only validated for
stock image parsing, signature checks, offset resolution, bundle generation,
and loader compilation.

## Demo

![Demo](AUTH-preview.gif)

## Requirements

- Python 3
- `clang`
- `aarch64-linux-gnu` binutils
- libusb access to the phone

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
./sign_bl.py --soc s5e9925 BL.tar.md5 device.pit
```

Outputs:

```text
BL_RESIGNED.tar.md5
device_RESIGNED.pit
```

## 2. Find offsets

```sh
./resolve.py --soc s5e9925 BL_RESIGNED.tar.md5
```

## 3. Build the loader

```sh
./build_loader.sh --soc s5e9925 BL_RESIGNED.tar.md5 device_RESIGNED.pit
```

Outputs:

```text
loader.bin
loader.elf
loader/bundle.bin
```

## 4. Run

```sh
./flash_and_fuse.py --soc s5e9925
```

## Disclaimer

This repo is for research purposes. No one else than you is responsible for what you do to your phone, especially if you're modifying it with LLMs.
