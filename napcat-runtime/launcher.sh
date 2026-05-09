#!/bin/bash
set -euo pipefail

RUNTIME_DIR="$(cd "$(dirname "$0")" && pwd)"
BOOTSTRAP_DIR="$RUNTIME_DIR/bootstrap"
QQ_EXECUTABLE="/usr/bin/qq"

mkdir -p "$BOOTSTRAP_DIR"

export DISPLAY="${DISPLAY:-:1}"
export XAUTHORITY="${XAUTHORITY:-$HOME/.Xauthority}"
export NAPCAT_BOOTMAIN="$RUNTIME_DIR"
export NAPCAT_WORKDIR="$RUNTIME_DIR"
export NAPCAT_DISABLE_MULTI_PROCESS=1
export NAPCAT_DISABLE_PIPE=1
export NAPCAT_DISABLE_PACKET_HOOK=1
export NAPCAT_WEBUI_PREFERRED_PORT="${NAPCAT_WEBUI_PREFERRED_PORT:-6099}"

cd "$BOOTSTRAP_DIR"
xvfb-run -a env LD_PRELOAD="$RUNTIME_DIR/libnapcat_launcher.so" "$QQ_EXECUTABLE" \
  --no-sandbox \
  --disable-gpu \
  --disable-software-rasterizer \
  --enable-unsafe-swiftshader \
  "$@"
