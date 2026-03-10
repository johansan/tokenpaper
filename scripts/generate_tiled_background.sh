#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG_PATH="${1:-$ROOT_DIR/config/tiled_background.json}"

python3 "$ROOT_DIR/code/pattern_generation/generate_tiled_background.py" "$CONFIG_PATH"
