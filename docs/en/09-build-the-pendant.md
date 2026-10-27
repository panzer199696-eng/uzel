# Building the pendant

*Русский → [../09-сборка-кулона.md](../09-сборка-кулона.md)*

The UZEL pendant is a **Seeed XIAO nRF52840 Sense** board, a battery and a
switch in a case based on the Omi Triangle v1. The microphone, the LED and the
charger are already on the board, so the only soldering is power: the battery
through the switch.

Status as of 27.10.2026:
- firmware 0.1.x works on the author's pendant, over-the-air updates are
  verified from the PC and from the phone
  ([firmware/CHANGELOG.md](../../firmware/CHANGELOG.md));
- case **v2.4** (removable clip, a second-colour emblem) is computed and
  checked on the PC, but **not printed or fitted** on a board yet. If you need
  proven mechanics, print the original Omi Triangle v1 from
  `hardware/case-v2/omi_triangle_v1/`.

---

## 1. What to buy

| Part | Qty | Notes |
|---|---|---|
| Seeed Studio XIAO nRF52840 **Sense** | 1 | It must be the Sense: it has the PDM microphone. The plain XIAO nRF52840 has no microphone |
| LiPo **502030**, 3.7 V, ~250 mAh, **with a built-in protection board (PCM)** | 1 | 5 × 20 × 30 mm. The XIAO charger gives 50/100 mA, so a bigger battery would charge too slowly |
| Switch, miniature slide | 1 | For the slot in the Omi Triangle v1 case. Check its size against the cut-out in `omi_triangle_v1/base.STEP` before buying |
| Thin stranded wire (28–30 AWG) | ~10 cm | Two colours: "+" and "−" |
| Kapton tape or heat-shrink | — | To insulate the joints from the board and the battery |
| **PLA** filament in two colours | ~10 g | The main colour and the emblem colour |
| USB-C cable | 1 | Charging and flashing by cable |

Tools: a soldering iron with a fine tip, flux, side cutters, a 3D printer (the
author uses a Creality K2, 0.4 nozzle). If the board has no bootloader (see
section 4), you also need an SWD programmer: J-Link, CMSIS-DAP/DAPLink or a
Raspberry Pi Pico running debugprobe.

## 2. Wiring

Only power needs soldering. The switch breaks the battery **plus**:

| From | To |
|---|---|
| battery "+" (usually the red wire) | an outer pin of the switch |
| the middle pin of the switch | the **BAT+** pad on the back of the XIAO |
| battery "−" (usually the black wire) | the **BAT−** pad on the back of the XIAO |

With the switch "off", the pendant is fully powered down, but the battery
**does not charge** from USB either: the switch cuts it off from the charger.
To charge, switch the pendant on and plug in the cable.

Everything else is routed on the board itself. Pins used by the firmware:

| Function | nRF52840 pin | Where it is set |
|---|---|---|
| PDM microphone, clock | P1.00 | `firmware/src/config.h` |
| PDM microphone, data | P0.16 | `firmware/src/config.h` |
| Microphone power | P1.10 | `firmware/src/config.h` |
| LED red / green / blue | aliases `led0` / `led1` / `led2` of the `xiao_ble` board in Zephyr | `firmware/src/led.h` |
| Battery voltage divider enable | P0.14 (active low) | `firmware/src/lib/battery/battery.c` |
| Battery voltage, ADC | AIN7 (P0.31), divider ×3 | `firmware/src/lib/battery/battery.c` |
| Charge current 50 / 100 mA | P0.13 | `firmware/src/lib/battery/battery.c` |
| Charge control | P0.17 | `firmware/src/lib/battery/battery.c` |
| USB presence | VBUS detector (`POWER.USBREGSTATUS`) | `firmware/src/power.c` |

## 3. Soldering

1. Switch "off". The battery is not connected to anything.
2. Tin the BAT+ and BAT− pads on the back of the XIAO. Work quickly: the SWD
   pads and board components are right next to them.
3. Solder a wire from the middle pin of the switch to BAT+, and a wire to
   BAT−. Choose the length to fit the case: USB-C and the switch come out of
   the same wall, next to each other.
4. Shorten the battery leads and solder "+" to the outer pin of the switch,
   "−" to the BAT− wire. Insulate every joint.
5. Check the polarity with a multimeter **before** switching on. Reverse
   polarity destroys the board's charger.
6. Cover the back of the board with Kapton where the battery touches it.

⚠️ Do not overheat, puncture or bend a LiPo. A swollen battery is not usable.

## 4. Bootloader

The UZEL firmware needs the **Adafruit nRF52 Bootloader** (UF2) and
**SoftDevice S140 7.3.0**. The application starts at address `0x27000`. That
is the memory layout for S140 7.x; with S140 6.1.1 the firmware does not
start. The bootloader needs the SoftDevice for over-the-air updates.

The update protocol was checked against the sources of bootloader **0.9.0**;
the author's pendant runs **0.11.0** since 26.10.2026. Both versions work.

**Check what is on the board.** Plug in USB and quickly double-press RST: a
`XIAO-SENSE` drive appears. Its `INFO_UF2.TXT` file shows the bootloader and
SoftDevice versions.

**A factory board.** Seeed ships the XIAO nRF52840 Sense with an Adafruit-based
UF2 bootloader together with S140 7.3.0. It is usually enough for flashing by
cable. For over-the-air updates, update the bootloader: take the
`update-…_nosd.uf2` file for the `xiao_nrf52840_ble_sense` board from the
[Adafruit_nRF52_Bootloader releases](https://github.com/adafruit/Adafruit_nRF52_Bootloader/releases)
page and copy it to the `XIAO-SENSE` drive. The `nosd` file updates only the
bootloader, the SoftDevice stays as it is.

**A blank or "bricked" board.** Flash it over SWD: the SWDIO, SWCLK, GND and
3V3 pads on the back of the XIAO, the programmer from section 1. You need the
`…_bootloader-<version>_s140_7.3.0.hex` file for the same board; it contains
both the bootloader and the SoftDevice. Make sure the file name says
`s140_7.3.0`.

```bash
# J-Link (nRF Command Line Tools)
nrfjprog -f nrf52 --eraseall
nrfjprog -f nrf52 --program <file>_s140_7.3.0.hex --verify --reset

# or CMSIS-DAP / Pico debugprobe (pyOCD)
pyocd erase -t nrf52840 --chip
pyocd flash -t nrf52840 <file>_s140_7.3.0.hex
```

## 5. Firmware

Ready builds are in the repository **Releases**: `uzel-fw-<version>.uf2` (by
cable), `uzel-fw-<version>.hex` (over the air from the PC) and
`uzel-fw-<version>-dfu.zip` (over the air from the phone). How to build it
yourself is described in [firmware/README.en.md](../../firmware/README.en.md).

**By cable** (this is how you flash it the first time):
1. Plug in USB and double-press RST. The `XIAO-SENSE` drive appears.
2. Copy `uzel-fw-<version>.uf2` onto it. The drive disappears by itself, the
   pendant reboots and blinks red, green and blue on start.

**Pairing.** The pendant gives audio only to a paired device (Just Works). It
accepts a new device only while it is not paired with anyone **or while it is
connected to USB**. Keys are stored in flash, there are three slots.

**Over the air** (the pendant is already paired):
- from the phone: Android client → Settings → "Pendant firmware" → the
  `uzel-fw-<version>-dfu.zip` file;
- from the PC: `uzel-bridge --dfu firmware/out/uzel-fw-<version>.hex`.

⚠️ **Over-the-air updates are not signed.** The package is Nordic Legacy DFU,
the bootloader checks only the image CRC. What protects you is something
else: the pendant can be put into bootloader mode only over an **encrypted**
link from an already paired device. But in bootloader mode (it advertises as
`AdaDFU`) the pendant accepts any image from any device nearby, so update
where there are no strangers around. If an update breaks off, the pendant
stays in the bootloader. It is always recoverable by cable: double RST and
the `.uf2`.

## 6. Printing the case

The case is generated by a script from the original Omi Triangle v1 models
(MIT, license in `hardware/case-v2/omi_triangle_v1/LICENSE`). A ready 3MF may
be in Releases. If it is not, build it yourself. The scripts assume Windows
(paths with `\`).

```powershell
python -m pip install numpy trimesh manifold3d shapely
cd hardware\case-v2\scripts
python -I -X utf8 build_v2.py > ..\out\build_report.json
python -I -X utf8 mesh_check.py      # every part is closed, one body
python -I -X utf8 crack_check.py     # no gaps thinner than 0.03 mm
```

The build takes about 10 seconds and gives the same result on every run. The
output is `hardware/case-v2/out/uzel_case_v2.4_plate.3mf`: the whole plate in
one file, every part already lying the right side down.

| Part | Orientation |
|---|---|
| body (with a pocket for the clip) | back on the bed |
| two-part lid: lid + emblem | face on the bed; **assign the second filament to the emblem** |
| clip (removable) | outer side on the bed |

Settings the case is designed for (PLA, 0.4 nozzle):
- layer 0.12 mm, first layer 0.16 mm. The 0.4 mm emblem takes exactly three layers;
- 3 walls, 5 top, 4 bottom, 15 % gyroid infill. **The clip is 100 %**, solid
  infill direction 30° (along the tongue), do not rotate it when arranging;
- supports off, bridges on: the ceiling of the clip pocket prints as a ~19 mm bridge;
- elephant foot compensation 0.15 mm;
- **do not enable fuzzy skin in the slicer**. The texture is already baked
  into the mesh, and fuzzy skin would ruin the fits;
- PLA 215–220 °C, bed 60 °C, fan 100 % from the second layer.

## 7. Assembly

1. Put the board into the body so that USB-C comes out of its cut-out, insert
   the switch into the slot next to it, place the battery on top.
2. Press the lid on until it clicks: three latches hold it.
3. Slide the clip into the pocket on the back from below (the USB side) all
   the way. The pendant is worn USB down, and its weight presses the clip
   against the stop.

If the clip is too tight or loose, change `TAPER` in `build_v2.py` and reprint
the body. The clip does not need reprinting. PLA softens from about 55 °C, so
do not leave the pendant under a windscreen in summer.

The case calculations and version history are in
[hardware/case-v2/HANDOFF.md](../../hardware/case-v2/HANDOFF.md) (Russian).
