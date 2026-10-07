#!/usr/bin/env bash
# Run from any directory. Never source .env as shell code.
set -euo pipefail
umask 077
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
VENV="$ROOT/.venv-linux"
UNIT="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/tulpa-support.service"
COMMAND="${1:-install}"

die() { printf '%s\n' "$*" >&2; exit 1; }
[[ "$(uname -s)" == Linux ]] || die '此脚本只能在 Linux 上运行。'
[[ $# -le 1 ]] || die '用法：bash scripts/deploy-linux.sh [install|run|service|status|logs|stop|uninstall-service]'

check_install() {
  [[ -x "$VENV/bin/python" ]] || die '请先执行：bash scripts/deploy-linux.sh install'
  [[ -f "$ROOT/.env" ]] || die '缺少 .env，请先执行 install。'
}

case "$COMMAND" in
  install)
    [[ $EUID -ne 0 ]] || die '请使用普通用户安装和运行，勿使用 sudo。'
    PYTHON="${PYTHON_BIN:-python3}"
    command -v "$PYTHON" >/dev/null || die '请安装 Python 3.11+ 和 python3-venv。'
    "$PYTHON" -c 'import sys; assert sys.version_info >= (3,11), "需要 Python 3.11+"'
    [[ -d "$VENV" ]] || "$PYTHON" -m venv "$VENV"
    "$VENV/bin/python" -m pip install -r "$ROOT/requirements-support.txt"
    if [[ ! -e "$ROOT/.env" ]]; then
      cat > "$ROOT/.env" <<'ENV'
API_BASE=https://api.deepseek.com
API_KEY=
MODEL=deepseek-flash
REPLY_ONEBOT_URL=http://127.0.0.1:3000
REPLY_ONEBOT_TOKEN=
REPLY_ONEBOT_WS_URL=ws://127.0.0.1:3001
REPLY_ONEBOT_WS_TOKEN=
# Linux 本机的 ASMRTranslator 目录，必须使用绝对路径。
ASMR_PROJECT_ROOT=
ENV
    fi
    chmod 600 "$ROOT/.env"
    mkdir -p "$ROOT/data" "$ROOT/.tmp" "$ROOT/.cache" "$ROOT/imports"
    chmod 700 "$ROOT/data" "$ROOT/.tmp" "$ROOT/.cache" "$ROOT/imports"
    printf '%s\n' '安装完成。执行 bash scripts/support.sh configure，隐藏输入密钥并配置 OneBot。' \
      '私有仓库接入：bash scripts/support.sh repo-keygen，然后 repo-bind GitHub地址' \
      '前台运行：bash scripts/deploy-linux.sh run' \
      '安装后台服务：bash scripts/deploy-linux.sh service' \
      '纯命令管理：bash scripts/support.sh --help'
    ;;
  run)
    check_install
    cd -- "$ROOT"
    exec "$VENV/bin/python" "$ROOT/app_support.py"
    ;;
  service)
    [[ $EUID -ne 0 ]] || die '用户服务必须由普通用户安装，勿使用 sudo。'
    check_install
    command -v systemctl >/dev/null || die '此系统没有 systemd；请使用 run。'
    # systemd paths use quoting plus specifier escaping, not shell interpolation.
    [[ "$ROOT" != *$'\n'* && "$ROOT" != *$'\r'* ]] || die '项目路径不能包含换行。'
    mkdir -p -- "$(dirname -- "$UNIT")"
    "$VENV/bin/python" - "$ROOT" "$UNIT" <<'PY'
import pathlib, sys
root, unit = sys.argv[1:]
def quote(s):
    return '"' + s.replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%') + '"'
pathlib.Path(unit).write_text(f'''[Unit]
Description=Tulpa ASMRTranslator QQ support bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory={quote(root)}
ExecStart={quote(root + '/.venv-linux/bin/python')} {quote(root + '/app_support.py')}
Restart=on-failure
RestartSec=5
TimeoutStopSec=90
UMask=0077
NoNewPrivileges=true
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=default.target
''', encoding='utf-8')
PY
    systemctl --user daemon-reload
    systemctl --user enable --now tulpa-support.service
    printf '%s\n' '用户服务已启动。需要退出 SSH 后仍运行、开机自动启动，请执行：' \
      "sudo loginctl enable-linger $(id -un)" \
      '命令管理：bash scripts/support.sh status / groups / bot-start 群会话ID' \
      '可选管理页面：http://127.0.0.1:7862（通过 SSH 隧道访问）'
    ;;
  status) systemctl --user status tulpa-support.service ;;
  logs) journalctl --user -u tulpa-support.service -f ;;
  stop) systemctl --user stop tulpa-support.service ;;
  uninstall-service)
    systemctl --user disable --now tulpa-support.service
    # Remove only this script's exact unit; retain .env and all SQLite data.
    rm -f -- "$UNIT"
    systemctl --user daemon-reload
    ;;
  *) die '用法：bash scripts/deploy-linux.sh [install|run|service|status|logs|stop|uninstall-service]' ;;
esac
