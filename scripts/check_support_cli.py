"""Offline CLI checks: no GitHub, paid API or QQ connections."""
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import httpx
import support_cli as cli
from dotenv import dotenv_values


class Checks(unittest.TestCase):
    def test_private_urls_and_no_credential_urls(self):
        for value in ('https://github.com/owner/repo', 'https://github.com/owner/repo.git', 'git@github.com:owner/repo.git'):
            self.assertEqual(cli.github_url(value), 'git@github.com:owner/repo.git')
        for value in ('https://token@github.com/owner/repo', 'https://evil.test/owner/repo',
                      'file:///tmp/repo', '-x', 'https://github.com/../repo', 'git@github.com:owner/repo;whoami'):
            with self.assertRaises(ValueError): cli.github_url(value)

    def test_local_requests_send_only_expected_body(self):
        seen = []
        def respond(request):
            seen.append(request)
            return httpx.Response(200, json={'ok': True})
        transport = httpx.MockTransport(respond)
        cli.request('/start', {'conversation_id': '123:group:456'}, transport=transport)
        cli.request('/bugs/1', {'status': '已解决'}, method='PUT', transport=transport)
        self.assertEqual(seen[0].method, 'POST')
        self.assertEqual(seen[1].method, 'PUT')
        self.assertEqual(seen[0].url.host, '127.0.0.1')
        self.assertEqual(seen[0].headers['X-ChatWeave-UI'], '1')
        self.assertEqual(json.loads(seen[0].content), {'conversation_id': '123:group:456'})
        self.assertNotIn('authorization', seen[0].headers)

    def test_server_failure_does_not_look_like_success(self):
        with self.assertRaisesRegex(ValueError, '先停止'):
            cli.request('/project', {}, transport=httpx.MockTransport(lambda _: httpx.Response(400, json={'detail': '先停止'})))

    def test_config_hides_secrets_preserves_other_keys(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(cli, 'ROOT', Path(directory)):
            cli.save_env({'ASMR_PROJECT_ROOT': '/home/user/repo', 'API_KEY': 'old-secret'})
            out = io.StringIO()
            with patch('builtins.input', side_effect=['', '', '']), \
                 patch.object(cli.getpass, 'getpass', side_effect=['new-secret', 'http-secret', 'ws-secret']), \
                 contextlib.redirect_stdout(out):
                cli.configure()
            values = dotenv_values(Path(directory) / '.env')
            self.assertEqual(values['API_KEY'], 'new-secret')
            self.assertEqual(values['ASMR_PROJECT_ROOT'], '/home/user/repo')
            self.assertNotIn('new-secret', out.getvalue())
            self.assertNotIn('http-secret', out.getvalue())

    def test_live_bot_blocks_git_update(self):
        with patch.object(cli, 'binding', return_value={}), patch.object(cli, 'request', return_value={'running': True}), \
             patch.object(cli, 'git') as git:
            with self.assertRaisesRegex(ValueError, 'bot-stop'): cli.update_repo()
            git.assert_not_called()

    def test_dirty_repo_not_overwritten_or_indexed(self):
        data = {'url': 'git@github.com:owner/repo.git', 'path': '/tmp/repo', 'branch': 'main'}
        with patch.object(cli, 'binding', return_value=data), \
             patch.object(cli, 'request', return_value={'running': False}) as request, \
             patch.object(cli, 'git', side_effect=[data['url'], 'main', ' M README.md']) as git:
            with self.assertRaisesRegex(ValueError, '本地改动'): cli.update_repo()
            self.assertEqual(git.call_count, 3)
            self.assertEqual(request.call_count, 1)

    def test_update_fast_forwards_then_indexes(self):
        data = {'url': 'git@github.com:owner/repo.git', 'path': '/tmp/repo', 'branch': 'main'}
        with patch.object(cli, 'binding', return_value=data), \
             patch.object(cli, 'request', side_effect=[{'running': False}, {'sources': 1}]) as request, \
             patch.object(cli, 'git', side_effect=[data['url'], 'main', '', 'updated']) as git, \
             contextlib.redirect_stdout(io.StringIO()):
            cli.update_repo()
            self.assertIn('--ff-only', git.call_args.args[0])
            request.assert_called_with('/project', {'project_root': str(Path('/tmp/repo'))})

    def test_cli_no_qq_send_for_preview(self):
        with patch.object(cli, 'request', return_value={}) as request, contextlib.redirect_stdout(io.StringIO()):
            cli.main(['preview', '配音失败', '--group', '123:group:456'])
            request.assert_called_once_with('/preview', {'question': '配音失败', 'conversation_id': '123:group:456'})

    def test_git_uses_dedicated_key_no_shell_no_hooks(self):
        with tempfile.TemporaryDirectory() as directory:
            key = Path(directory) / 'deploy key'
            key.touch()
            with patch.object(cli, 'key_path', return_value=key), patch.object(cli, 'run_command', return_value='') as run:
                cli.git(['clone', '--', 'git@github.com:owner/repo.git', '/tmp/repo'])
                args = run.call_args.args[0]
                env = run.call_args.kwargs['env']
                self.assertIn('core.hooksPath=/dev/null', args)
                self.assertIn('StrictHostKeyChecking=yes', env['GIT_SSH_COMMAND'])
                self.assertIn('BatchMode=yes', env['GIT_SSH_COMMAND'])
                self.assertIn(str(key), env['GIT_SSH_COMMAND'])

    def test_github_authorization_readonly_token_not_saved(self):
        with tempfile.TemporaryDirectory() as directory:
            key = Path(directory) / 'key'
            Path(str(key) + '.pub').write_text('ssh-ed25519 TEST', encoding='utf-8')
            with patch.object(cli, 'key_path', return_value=key), \
                 patch.object(cli.getpass, 'getpass', return_value='temporary-token'), \
                 patch.object(cli.httpx, 'Client') as factory, \
                 patch.object(cli, 'save_env') as save, contextlib.redirect_stdout(io.StringIO()) as out:
                client = factory.return_value.__enter__.return_value
                client.post.return_value = httpx.Response(201)
                cli.authorize_repo('https://github.com/owner/repo')
                self.assertTrue(client.post.call_args.kwargs['json']['read_only'])
                self.assertEqual(client.post.call_args.args[0], 'https://api.github.com/repos/owner/repo/keys')
                save.assert_not_called()
                self.assertNotIn('temporary-token', out.getvalue())


if __name__ == '__main__':
    unittest.main(verbosity=2)
