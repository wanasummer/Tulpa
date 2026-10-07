#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
case "${1:-status}" in
  start)
    "$ROOT/.venv-linux/bin/python" - "$ROOT" <<'PY'
from pathlib import Path
import sys
root=sys.argv[1]
def quote(value):
    if '\n' in value or '\r' in value: raise ValueError('Invalid path')
    return '"'+value.replace('\\','\\\\').replace('"','\\"').replace('%','%%')+'"'
unit=Path.home()/'.config/systemd/user/tulpa-adventure-playtest.service'
unit.parent.mkdir(parents=True,exist_ok=True)
unit.write_text(f'''[Unit]
Description=Tulpa local human adventure playtest
After=network.target
[Service]
Type=simple
WorkingDirectory={root.replace('%','%%')}
ExecStart={quote(root+'/.venv-linux/bin/python')} {quote(root+'/app_adventure.py')}
Restart=on-failure
RestartSec=5
UMask=0077
NoNewPrivileges=true
Environment=PYTHONUNBUFFERED=1
''')
unit.chmod(0o600)
PY
    systemctl --user daemon-reload
    systemctl --user start tulpa-adventure-playtest.service
    ;;
  status) systemctl --user status tulpa-adventure-playtest.service --no-pager ;;
  restart) systemctl --user restart tulpa-adventure-playtest.service ;;
  stop) systemctl --user stop tulpa-adventure-playtest.service ;;
  logs) journalctl --user -u tulpa-adventure-playtest.service -n 40 --no-pager ;;
  *) echo '用法：bash scripts/adventure-playtest.sh [start|status|restart|stop|logs]' >&2; exit 1 ;;
esac
