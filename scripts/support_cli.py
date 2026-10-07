"""Local command-line administration; Git credentials never enter model requests."""
import argparse
import getpass
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
from urllib.parse import urlparse

import httpx
from dotenv import dotenv_values, set_key

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
API = 'http://127.0.0.1:7862/api/support'
REPO_PATTERN = re.compile(r'(?:git@github\.com:|https://github\.com/)([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?')


def github_url(value):
    match = REPO_PATTERN.fullmatch(value.strip())
    if not match or any(part in ('.', '..') for part in match.groups()):
        raise ValueError('仅接受 github.com 的 SSH 或 HTTPS 仓库地址，不接受带令牌的 URL。')
    return f'git@github.com:{match[1]}/{match[2]}.git'


def output(value):
    # JSON escapes terminal control characters in untrusted group messages.
    print(json.dumps(value, ensure_ascii=False, indent=2))


def request(path='', body=None, method='POST', transport=None):
    try:
        with httpx.Client(timeout=300, trust_env=False, follow_redirects=False, transport=transport) as client:
            response = client.request('GET' if body is None else method, API + path,
                                      headers={'X-ChatWeave-UI': '1'}, json=body)
    except httpx.HTTPError:
        raise ValueError('无法连接本机群助手服务。先执行 bash scripts/deploy-linux.sh service，并检查 logs。') from None
    try:
        data = response.json()
    except ValueError:
        raise ValueError('本机服务返回了无效数据。') from None
    if response.status_code >= 300:
        raise ValueError(str(data.get('detail', '本机服务请求失败。')))
    return data


def save_env(values):
    path = ROOT / '.env'
    if path.is_symlink():
        raise ValueError('.env 不能是符号链接。')
    path.touch(mode=0o600, exist_ok=True)
    path.chmod(0o600)
    for name, value in values.items():
        set_key(str(path), name, value, quote_mode='always')
    path.chmod(0o600)


def configure():
    values = dotenv_values(ROOT / '.env')
    result = {}
    for name, default in [('API_KEY', ''), ('MODEL', 'deepseek-flash'),
                          ('REPLY_ONEBOT_URL', 'http://127.0.0.1:3000'), ('REPLY_ONEBOT_TOKEN', ''),
                          ('REPLY_ONEBOT_WS_URL', 'ws://127.0.0.1:3001'), ('REPLY_ONEBOT_WS_TOKEN', '')]:
        current = values.get(name) or default
        secret = name.endswith('KEY') or name.endswith('TOKEN')
        hint = '已设置，回车保留' if current else '未设置'
        text = getpass.getpass(f'{name} [{hint}]: ') if secret else input(f'{name} [{current}]: ')
        result[name] = text.strip() or current
    if not result['API_KEY']:
        raise ValueError('API_KEY 不能为空，未保存配置。')
    # Reuse the price allowlist, so an unsupported model cannot bypass the ledger.
    from chatlocal.support_budget import RATES
    if result['MODEL'] not in RATES:
        raise ValueError('模型没有本地价格记录，未保存配置。')
    for name, schemes in [('REPLY_ONEBOT_URL', ('http', 'https')), ('REPLY_ONEBOT_WS_URL', ('ws', 'wss'))]:
        url = urlparse(result[name])
        if (url.scheme not in schemes or url.hostname not in ('localhost', '127.0.0.1', '::1')
                or url.username or url.password or url.query or url.fragment):
            raise ValueError(f'{name} 必须是无凭据、无查询参数的本机地址，未保存配置。')
        try:
            url.port
        except ValueError:
            raise ValueError(f'{name} 端口无效，未保存配置。') from None
    result['API_BASE'] = 'https://api.deepseek.com'
    save_env(result)
    print('配置已保存，密钥未回显。执行 systemctl --user restart tulpa-support.service 使连接配置生效。')


def key_path():
    return Path.home() / '.ssh' / 'tulpa_knowledge_ed25519'


def run_command(args, env=None):
    try:
        result = subprocess.run(args, env=env, text=True, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, timeout=300, check=False)
    except FileNotFoundError:
        raise ValueError(f'缺少 {args[0]}，请先安装 git / openssh-client。') from None
    except subprocess.TimeoutExpired:
        raise ValueError('操作超时。检查 GitHub 网络和 SSH 配置。') from None
    if result.returncode:
        raise ValueError(f'操作失败：{result.stderr.strip()[:2000]}')
    return result.stdout.strip()


def keygen():
    key = key_path()
    key.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    key.parent.chmod(0o700)
    if key.is_symlink() or key.with_suffix('.pub').is_symlink():
        raise ValueError('专用密钥路径不能是符号链接。')
    if not key.exists():
        # Dedicated read-only repository credential for unattended updates.
        run_command(['ssh-keygen', '-t', 'ed25519', '-N', '', '-C', 'tulpa-knowledge', '-f', str(key)])
    key.chmod(0o600)
    public = Path(str(key) + '.pub')
    if not public.is_file():
        raise ValueError('私钥已存在但缺少公钥，不会覆盖，请自行恢复 .pub 文件。')
    print('将下面的公钥加入 GitHub 仓库 Settings → Deploy keys，不勾选 Allow write access：')
    print(public.read_text(encoding='utf-8').strip())
    print('\n首次使用前，请核对 GitHub 主机指纹并建立 SSH 信任：')
    print(f'ssh -i {shlex.quote(str(key))} -o IdentitiesOnly=yes -T git@github.com')
    print('GitHub 显示认证成功且不提供 shell 是正常结果（通常退出码为 1）。')


def git(args):
    key = key_path()
    if not key.is_file() or key.is_symlink():
        raise ValueError('缺少专用密钥。先执行 repo-keygen 并在 GitHub 添加只读公钥。')
    env = dict(os.environ)
    env['GIT_TERMINAL_PROMPT'] = '0'
    env['GIT_SSH_COMMAND'] = f'ssh -i {shlex.quote(str(key))} -o IdentitiesOnly=yes -o BatchMode=yes -o StrictHostKeyChecking=yes'
    return run_command(['git', '-c', 'core.hooksPath=/dev/null', '-c', 'submodule.recurse=false',
                        '-c', 'protocol.file.allow=never', *args], env=env)


def authorize_repo(url):
    repo = github_url(url).removeprefix('git@github.com:').removesuffix('.git')
    public = Path(str(key_path()) + '.pub')
    if not public.is_file() or public.is_symlink():
        raise ValueError('先执行 repo-keygen 生成专用公钥。')
    token = getpass.getpass('GitHub Token（仅用于添加只读 Deploy Key，不保存）: ').strip()
    if not token:
        raise ValueError('Token 不能为空。')
    try:
        with httpx.Client(timeout=30, trust_env=False, follow_redirects=False) as client:
            response = client.post(f'https://api.github.com/repos/{repo}/keys',
                headers={'Authorization': f'Bearer {token}', 'Accept': 'application/vnd.github+json'},
                json={'title': 'Tulpa Linux knowledge (read only)', 'key': public.read_text(encoding='utf-8').strip(), 'read_only': True})
    except httpx.HTTPError:
        raise ValueError('GitHub API 连接失败，Token 未保存。') from None
    if response.status_code != 201:
        raise ValueError(f'GitHub 添加公钥失败（HTTP {response.status_code}）。检查仓库权限、Token 或公钥是否已添加；Token 未保存。')
    print('仓库只读 Deploy Key 已添加，Token 未保存。请核对 GitHub SSH 指纹后执行 repo-bind。')


def binding():
    path = ROOT / 'data' / 'support-github.json'
    if not path.is_file():
        raise ValueError('还未绑定仓库。先执行 repo-bind GitHub地址。')
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
        if github_url(data['url']) != data['url'] or data['path'] != str(ROOT / 'knowledge' / 'github'):
            raise ValueError()
    except (KeyError, ValueError, TypeError):
        raise ValueError('仓库绑定记录无效，不会执行 Git 操作。') from None
    return data


def bind_repo(url, branch=None):
    url = github_url(url)
    target = ROOT / 'knowledge' / 'github'
    if target.is_symlink() or target.parent.is_symlink():
        raise ValueError('知识库目录不能是符号链接。')
    if target.exists():
        raise ValueError('知识库 Git 目录已存在。请用 repo-update 更新，不会覆盖现有文件。')
    if branch and (branch.startswith('-') or not re.fullmatch(r'[A-Za-z0-9_./-]+', branch)):
        raise ValueError('分支名无效。')
    target.parent.mkdir(parents=True, exist_ok=True)
    args = ['clone', '--depth', '1', '--single-branch']
    if branch:
        args += ['--branch', branch]
    git([*args, '--', url, str(target)])
    selected = git(['-C', str(target), 'symbolic-ref', '--short', 'HEAD'])
    if not selected:
        raise ValueError('必须绑定实际分支，不能绑定游离的 tag。')
    path = ROOT / 'data' / 'support-github.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({'url': url, 'path': str(target), 'branch': selected}, ensure_ascii=False), encoding='utf-8')
    path.chmod(0o600)
    save_env({'ASMR_PROJECT_ROOT': str(target)})
    print('私有仓库已拉取并绑定。启动本机服务后执行 project-update 建立知识索引。')


def update_repo():
    data = binding()
    state = request()
    if state['running'] or (state.get('run') or {}).get('active'):
        raise ValueError('先执行 bot-stop，再更新仓库；完成后用 bot-start 恢复目标群。')
    target = Path(data['path'])
    if target.is_symlink() or target.parent.is_symlink():
        raise ValueError('知识库目录不能是符号链接。')
    if git(['-C', str(target), 'remote', 'get-url', 'origin']) != data['url']:
        raise ValueError('远端地址与绑定记录不一致，不会更新。')
    if git(['-C', str(target), 'symbolic-ref', '--short', 'HEAD']) != data['branch']:
        raise ValueError('当前分支与绑定记录不一致，不会更新。')
    if git(['-C', str(target), 'status', '--porcelain']):
        raise ValueError('仓库存在本地改动，不会覆盖；请先自行处理。')
    git(['-C', str(target), 'pull', '--ff-only', '--no-recurse-submodules', 'origin', data['branch']])
    output(request('/project', {'project_root': str(target)}))


def main(argv=None):
    parser = argparse.ArgumentParser(description='Linux 群助手命令行：固定每日 ¥5，无需浏览器')
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('configure', 'repo-keygen', 'repo-update', 'status', 'groups', 'bot-stop', 'bugs', 'reviews'):
        commands.add_parser(name)
    repo = commands.add_parser('repo-bind', help='通过只读 SSH 密钥绑定私有 GitHub 仓库')
    repo.add_argument('url')
    repo.add_argument('--branch')
    commands.add_parser('repo-authorize', help='隐藏输入 GitHub Token，添加只读公钥，Token 不保存').add_argument('url')
    project = commands.add_parser('project-update')
    project.add_argument('--path', help='不用 GitHub 时指定本机项目路径')
    for name in ('documents-update', 'bot-start'):
        commands.add_parser(name).add_argument('conversation_id', help='groups 返回的完整 conversation_id')
    bug = commands.add_parser('bug-status')
    bug.add_argument('id', type=int)
    bug.add_argument('status', choices=['待确认', '待补充', '处理中', '已解决', '使用问题'])
    preview = commands.add_parser('preview', help='付费试答，计入每日额度，不发送 QQ')
    preview.add_argument('question')
    preview.add_argument('--group', default='')
    args = parser.parse_args(argv)
    if args.command == 'configure': configure()
    elif args.command == 'repo-keygen': keygen()
    elif args.command == 'repo-authorize': authorize_repo(args.url)
    elif args.command == 'repo-bind': bind_repo(args.url, args.branch)
    elif args.command == 'repo-update': update_repo()
    elif args.command == 'status':
        state = request()
        output({key: state[key] for key in ('running', 'run', 'receiver', 'budget', 'knowledge', 'today_bugs', 'database', 'error')})
    elif args.command == 'groups': output(request('/groups'))
    elif args.command in ('bugs', 'reviews'): output(request()[args.command])
    elif args.command == 'project-update':
        values = dotenv_values(ROOT / '.env')
        path = args.path or os.environ.get('ASMR_PROJECT_ROOT') or values.get('ASMR_PROJECT_ROOT')
        output(request('/project', {'project_root': str(Path(path).expanduser().resolve())} if path else {}))
    elif args.command == 'documents-update': output(request('/documents', {'conversation_id': args.conversation_id}))
    elif args.command == 'bot-start': output(request('/start', {'conversation_id': args.conversation_id}))
    elif args.command == 'bot-stop': output(request('/stop', {}))
    elif args.command == 'bug-status': output(request(f'/bugs/{args.id}', {'status': args.status}, method='PUT'))
    elif args.command == 'preview': output(request('/preview', {'question': args.question, 'conversation_id': args.group}))


if __name__ == '__main__':
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8')
    try:
        main()
    except (ValueError, OSError) as exc:
        print(f'错误：{exc}', file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print('\n已取消。', file=sys.stderr)
        sys.exit(130)
