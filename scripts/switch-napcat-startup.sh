#!/usr/bin/env bash
# Update the existing service's startup account without interrupting a live login.
set -euo pipefail
qq="${1:-2169207153}"
[[ "$qq" =~ ^[0-9]{5,12}$ ]] || { echo 'QQ 号无效' >&2; exit 1; }
[[ "$(id -u)" = 0 ]] || { echo "请执行：sudo bash $0 $qq" >&2; exit 1; }
[[ -f /etc/systemd/system/napcat.service ]] || { echo '未找到 napcat.service' >&2; exit 1; }
[[ -x /root/Napcat/opt/QQ/qq ]] || { echo '未找到 NapCat QQ 程序' >&2; exit 1; }
unit_dir=/etc/systemd/system/napcat.service.d
install -d -m 755 "$unit_dir"
if [[ -f "$unit_dir/account.conf" ]]; then
  cp -p "$unit_dir/account.conf" "$unit_dir/account.conf.backup-$(date +%Y%m%d-%H%M%S)"
fi
cat > "$unit_dir/account.conf" <<EOF
[Service]
ExecStart=
ExecStart=/usr/bin/xvfb-run -a /root/Napcat/opt/QQ/qq --no-sandbox -q $qq
EOF
chmod 644 "$unit_dir/account.conf"
systemctl daemon-reload
systemctl enable napcat.service
echo "已将 NapCat 开机启动账号设为 $qq；当前登录未中断。"
systemctl show napcat.service -p ExecStart -p UnitFileState -p ActiveState
