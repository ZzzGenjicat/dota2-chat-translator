#!/bin/bash
set -euo pipefail
APP_DIR="$(cd -- "$(dirname -- "$0")" && pwd)"
PYTHON="$APP_DIR/.venv/bin/python"
if [[ ! -x "$PYTHON" ]]; then
  echo '请先双击 install_offline.command 安装一次本地运行环境。'
  if [[ -t 0 ]]; then read -r -p '按回车关闭…' _; fi
  exit 1
fi
cd -- "$APP_DIR"
exec "$PYTHON" -X utf8 "$APP_DIR/run.py"
