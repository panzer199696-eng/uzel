#!/usr/bin/env bash
# Тесты переносимых модулей прошивки на ПК: wsl -- bash firmware/tests/run.sh
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
OUT="$(mktemp -d)"
trap 'rm -rf "$OUT"' EXIT
gcc -std=c11 -O2 -Wall -Wextra -Werror -o "$OUT/test_host" \
  "$HERE/test_host.c" "$HERE/../src/dsp.c" "$HERE/../src/frame_queue.c" "$HERE/../src/audio_packet.c" -lm
"$OUT/test_host"
