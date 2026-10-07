#!/bin/bash
set -euo pipefail
APP_DIR="$(cd -- "$(dirname -- "$0")" && pwd)"
if bash "$APP_DIR/tools/install_offline_macos.sh"; then
  echo '安装完成。以后双击 launch_app.command 即可离线使用。'
else
  echo '安装未完成，请根据上方提示重试。'
  if [[ -t 0 ]]; then read -r -p '按回车关闭…' _; fi
  exit 1
fi
if [[ -t 0 ]]; then read -r -p '按回车关闭…' _; fi
