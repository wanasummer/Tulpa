#!/usr/bin/env bash
# Run NapCat as a persistent systemd service instead of a manual screen session.
# Usage: sudo bash scripts/install-napcat-service.sh [QQ号]
set -euo pipefail

QQ="${1:-2169207153}"
NAPCAT_DIR=/root/Napcat
QQ_BIN="$NAPCAT_DIR/opt/QQ/qq"
UNIT=/etc/systemd/system/napcat.service

[ "$(id -u)" = 0 ] || { echo "请用 sudo 运行：sudo bash $0 [QQ号]" >&2; exit 1; }
[[ "$QQ" =~ ^[0-9]{5,12}$ ]] || { echo "QQ 号无效：$QQ" >&2; exit 1; }
[ -x "$QQ_BIN" ] || { echo "未找到 NapCat QQ：$QQ_BIN" >&2; exit 1; }
command -v xvfb-run >/dev/null || { echo "缺少 xvfb-run，请先 apt install xvfb" >&2; exit 1; }

cat > "$UNIT" <<EOF
[Unit]
Description=NapCat QQ (OneBot for Tulpa)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
WorkingDirectory=$NAPCAT_DIR
ExecStart=$(command -v xvfb-run) -a $QQ_BIN --no-sandbox -q $QQ
Restart=always
RestartSec=10
KillMode=mixed
TimeoutStopSec=30

[Install]
WantedBy=multi-user.target
EOF
echo "已写入 $UNIT（快速登录 QQ：$QQ）"

# Two QQ instances on one account would fight over the login.
if screen -ls 2>/dev/null | grep -q '\.napcat\b'; then
  screen -S napcat -X quit || true
  echo "已停止原 screen 会话 napcat"
fi
for _ in $(seq 1 15); do pgrep -f "$QQ_BIN" >/dev/null || break; sleep 1; done
if pgrep -f "$QQ_BIN" >/dev/null; then
  pkill -f "$QQ_BIN" || true
  sleep 2
fi

systemctl daemon-reload
systemctl enable --now napcat.service
echo "等待 NapCat 启动..."
for _ in $(seq 1 60); do
  ss -ltn | grep -qE ':3001\b' && break
  sleep 2
done
systemctl --no-pager --lines=0 status napcat.service | head -5
if ss -ltn | grep -qE ':3001\b'; then
  echo "OneBot 端口已就绪，Tulpa 会自动重连。"
else
  echo "OneBot 端口还没起来，可能需要扫码登录：sudo journalctl -u napcat -f 或打开 http://<服务器地址>:6099"
fi
