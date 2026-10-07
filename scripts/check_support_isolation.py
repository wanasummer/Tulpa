"""Offline checks for duplicate processes and QQ login isolation."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app_support import acquire_instance_lock
from chatlocal.onebot import Client, OneBotError


class Checks(unittest.TestCase):
    def test_identity_checks_never_log_in_or_restart_qq(self):
        seen = []

        def respond(request):
            seen.append(request.url.path)
            return httpx.Response(200, json={
                'status': 'ok', 'retcode': 0, 'data': {'user_id': 123456}})

        client = Client({'url': 'http://127.0.0.1:3000', 'token': ''},
                        transport=httpx.MockTransport(respond))
        for _ in range(3):
            self.assertEqual(client.login(), '123456')
        for action in ('login', 'logout', 'set_restart', 'SetQuickLogin',
                       'RestartNapCat', '/api/QQLogin/SetQuickLogin'):
            for approved in (False, True):
                with self.assertRaises(OneBotError):
                    client.call(action, {}, approved=approved)
        self.assertEqual(seen, ['/get_login_info'] * 3)

    def test_second_process_excluded_and_exit_releases_lock(self):
        code = '''from pathlib import Path
import sys
from app_support import acquire_instance_lock
try:
    lock = acquire_instance_lock(Path(sys.argv[1]))
except RuntimeError:
    sys.exit(17)
'''
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'instance.lock'
            lock = acquire_instance_lock(path)
            try:
                second = subprocess.run([sys.executable, '-c', code, str(path)],
                                        cwd=ROOT, timeout=20)
                self.assertEqual(second.returncode, 17)
            finally:
                lock.close()
            next_process = subprocess.run([sys.executable, '-c', code, str(path)],
                                          cwd=ROOT, timeout=20)
            self.assertEqual(next_process.returncode, 0)


if __name__ == '__main__':
    unittest.main()
