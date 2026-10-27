#!/usr/bin/env bash
# Сборка прошивки УЗЛА в WSL2 (nRF Connect SDK v2.7.0).
#
#   wsl -- bash firmware/build.sh [release|debug] [каталог-вывода]
#
# Исходники лежат в репозитории на диске Windows, а собирать оттуда нельзя:
# путь с кириллицей и пробелами, плюс /mnt/c в разы медленнее. Поэтому дерево
# копируется в ~/uzel-fw/app (rsync, только изменения) и собирается там.
# Результат — firmware/out/uzel-fw-<версия>[-debug].uf2 рядом с этим скриптом.
set -euo pipefail

VARIANT="${1:-release}"
HERE="$(cd "$(dirname "$0")" && pwd)"
WORK="$HOME/uzel-fw"
APP="$WORK/app"
BUILD="$WORK/build-$VARIANT"
OUT="${2:-$HERE/out}"

export ZEPHYR_TOOLCHAIN_VARIANT=zephyr
export ZEPHYR_SDK_INSTALL_DIR="$HOME/zephyr-sdk-0.16.8"
export ZEPHYR_BASE="$HOME/ncs/zephyr"
WEST="$HOME/ncs-venv/bin/west"

case "$VARIANT" in
  release) CONF="prj_uzel.conf"; OVERLAY="boards/xiao_ble_sense.overlay" ;;
  debug)   CONF="prj_uzel.conf;prj_uzel_debug.conf"
           OVERLAY="boards/xiao_ble_sense.overlay;boards/debug_console.overlay" ;;
  *) echo "вариант: release или debug" >&2; exit 2 ;;
esac

mkdir -p "$APP" "$OUT"
rsync -a --delete --exclude out --exclude tests/build "$HERE/" "$APP/"

cd "$HOME/ncs"
"$WEST" build -p auto -b xiao_ble_sense -d "$BUILD" "$APP" -- \
  -DCONF_FILE="$CONF" \
  -DDTC_OVERLAY_FILE="$OVERLAY"

VERSION="$(sed -n 's/^CONFIG_BT_DIS_FW_REV_STR="\(.*\)"/\1/p' "$APP/prj_uzel.conf")"
SUFFIX=""
[ "$VARIANT" = debug ] && SUFFIX="-debug"
UF2="$BUILD/zephyr/zephyr.uf2"
[ -f "$UF2" ] || UF2="$BUILD/app/zephyr/zephyr.uf2"
cp "$UF2" "$OUT/uzel-fw-$VERSION$SUFFIX.uf2"
cp "$BUILD/zephyr/zephyr.hex" "$OUT/uzel-fw-$VERSION$SUFFIX.hex" 2>/dev/null \
  || cp "$BUILD/app/zephyr/zephyr.hex" "$OUT/uzel-fw-$VERSION$SUFFIX.hex"
# Пакет для обновления по воздуху с телефона (с ПК хватает .hex)
python3 "$HERE/../bridge/uzel_bridge/dfu.py" package   "$OUT/uzel-fw-$VERSION$SUFFIX.hex" "$OUT/uzel-fw-$VERSION$SUFFIX-dfu.zip" >/dev/null
echo "готово: $OUT/uzel-fw-$VERSION$SUFFIX.uf2 (+ .hex, -dfu.zip)"
