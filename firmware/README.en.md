# UZEL firmware

*Русский → [README.md](README.md)*

Pendant: Seeed XIAO nRF52840 **Sense** (Triangle v1 case), Adafruit nRF52
Bootloader (UF2) with SoftDevice S140 7.3.0 in flash (the bootloader needs it
for over-the-air updates). Zephyr / nRF Connect SDK v2.7.0, the application
starts at `0x27000`. How to build the pendant itself and put a bootloader on a
blank board is described in
[docs/en/09-build-the-pendant.md](../docs/en/09-build-the-pendant.md).

The firmware grew out of Omi DK2 2.0.10 (BasedHardware/omi, MIT). Third-party
components and their licenses are listed in [NOTICE](../NOTICE).

## Toolchain (once)

The project builds in **WSL2** (Ubuntu 22.04/24.04) or on any Linux x86-64.
`build.sh` expects the toolchain at these paths:

| What | Path |
|---|---|
| nRF Connect SDK v2.7.0 (west workspace) | `~/ncs` |
| Zephyr SDK 0.16.8 | `~/zephyr-sdk-0.16.8` |
| Python environment with `west` | `~/ncs-venv` |

```bash
sudo apt update
sudo apt install --no-install-recommends git cmake ninja-build gperf ccache \
  dfu-util device-tree-compiler wget xz-utils file make gcc python3-dev \
  python3-venv python3-pip rsync

# west and the nRF Connect SDK 2.7.0 sources (~4 GB)
python3 -m venv ~/ncs-venv
~/ncs-venv/bin/pip install west
~/ncs-venv/bin/west init -m https://github.com/nrfconnect/sdk-nrf --mr v2.7.0 ~/ncs
cd ~/ncs && ~/ncs-venv/bin/west update && ~/ncs-venv/bin/west zephyr-export
~/ncs-venv/bin/pip install -r zephyr/scripts/requirements.txt \
  -r nrf/scripts/requirements.txt -r bootloader/mcuboot/scripts/requirements.txt

# Zephyr SDK 0.16.8, ARM compiler only
cd ~
wget https://github.com/zephyrproject-rtos/sdk-ng/releases/download/v0.16.8/zephyr-sdk-0.16.8_linux-x86_64_minimal.tar.xz
tar xf zephyr-sdk-0.16.8_linux-x86_64_minimal.tar.xz
~/zephyr-sdk-0.16.8/setup.sh -t arm-zephyr-eabi -c
```

Other paths can be used by editing the variables at the top of `build.sh`.

## Build

```bash
bash firmware/build.sh release   # → firmware/out/uzel-fw-<version>.uf2, .hex, -dfu.zip
bash firmware/build.sh debug     # the same with logs over USB CDC: uzel-fw-<version>-debug.*
bash firmware/tests/run.sh       # dsp / frame_queue / audio_packet tests on the PC (gcc)
```

From Windows the same commands run via `wsl -- bash firmware/build.sh release`.
`build.sh` copies the tree to `~/uzel-fw/app` (rsync, changes only) and builds
there: the repository path on the Windows drive may contain non-ASCII
characters and spaces, and building through `/mnt/c` is many times slower.

Configuration:

| File | What it sets |
|---|---|
| `prj_uzel.conf` | the release build: BLE, pairing, services, logs off; **version** — `CONFIG_BT_DIS_FW_REV_STR` |
| `prj_uzel_debug.conf` | the `debug` add-on: console and logs over USB CDC |
| `boards/xiao_ble_sense.overlay` | device tree on top of the `xiao_ble/nrf52840/sense` board |
| `boards/debug_console.overlay` | USB console for `debug` |

The version grows with every build that goes onto a pendant. Changes are
described in `CHANGELOG.md` (Russian).

## Flashing

- **By cable:** double-press RST → the `XIAO-SENSE` drive → copy the `.uf2`.
- **Over the air from the PC:** `uzel-bridge --dfu firmware/out/uzel-fw-<version>.hex`.
- **Over the air from the phone:** Android client → Settings → "Pendant
  firmware" → `uzel-fw-<version>-dfu.zip`.

For an over-the-air update the pendant must already be paired: the bootloader
can be entered only over an encrypted link. ⚠️ The DFU package itself is not
signed, the bootloader checks only the CRC. Details are in
[docs/en/09-build-the-pendant.md](../docs/en/09-build-the-pendant.md#5-firmware).

## BLE protocol

| What | UUID | Access |
|---|---|---|
| Audio, service | `19b10000-e8f2-537e-4f6c-d104768a1214` | |
| Opus frames | `…0001` | notify, subscription requires encryption |
| codec code | `…0002` | read, 20 = Opus 16 kHz |
| DFU (Nordic Legacy) | `00001530-1212-efde-1523-785feabcd123` | |
| control point | `00001531-…` | encrypted write: `01 04` → bootloader |
| UZEL statistics | `5a0e1000-7a3e-4b8e-9f2a-0d1e2f3a4b5c` | |
| counters | `5a0e1001-…` | encrypted read, format in `src/stats.h` |
| Battery, DIS | standard | |

Audio packet: `uint16 LE` number + `0` + one whole 20 ms Opus frame (CELT, 16 kHz).

## Pairing

Just Works. A new device is accepted only while there are no paired devices
**or the pendant is connected to USB**. A phone or PC that lost its keys pairs
again while the pendant is charging. Without encryption the pendant drops the
connection after 30 s.

## LEDs

| Light | Meaning |
|---|---|
| red–green–blue on start | booting |
| green on | on USB |
| blue flash every 3 s | connected to the bridge |
| red flash every 3 s | no connection, audio is buffered (up to 2 s) |
| red bursts of N, 10 s, reboot | start-up failure at step N (1 BLE, 2 codec, 3 microphone) |
