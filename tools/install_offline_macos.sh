#!/bin/bash
set -euo pipefail
APP_DIR="$(cd -- "$(dirname -- "$0")/.." && pwd)"
if [[ "$(uname -s)" != Darwin ]]; then
  echo '此安装器需要在 Mac 上运行。' >&2
  exit 1
fi
PYTHON="${DOTA2_TRANSLATOR_PYTHON:-/Library/Frameworks/Python.framework/Versions/3.12/bin/python3.12}"
if [[ ! -x "$PYTHON" ]]; then
  echo '源码安装需要 python.org 的 Python 3.12（含 Tk）。也可设置 DOTA2_TRANSLATOR_PYTHON 指定路径。'
  echo '普通用户请下载 GitHub Release 的 Mac 安装包，无需安装 Python。' >&2
  exit 1
fi
"$PYTHON" -c 'import sys, tkinter; assert sys.platform == "darwin" and sys.version_info[:2] == (3, 12), "需要 Mac Python 3.12"'
"$PYTHON" -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/python" -m pip install --only-binary=:all: -r "$APP_DIR/requirements.txt"
"$APP_DIR/.venv/bin/python" "$APP_DIR/tools/install_offline_model.py"
