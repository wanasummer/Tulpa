# Linux 部署群助手

Linux 入口是 `app_support.py`，不安装 Windows 聊天读取器或桌面界面。每日固定 ¥5，回答与独立安全审核共用额度；审核通过后自动回复，疑似 Bug 写入 SQLite。可完全通过命令行选择群并启动，之后服务重启会恢复已启用的群。管理页面是可选入口。

## 全程命令行：私有 GitHub 仓库与自动回复

下面命令在 Linux 服务器的 Tulpa 源码目录执行。QQ 网关仍需要另外登录机器人账号并开启 OneBot HTTP / 正向 WebSocket 服务，详见后文。

```bash
sudo apt-get update
sudo apt-get install -y python3 python3-venv python3-pip git openssh-client
bash scripts/deploy-linux.sh install
bash scripts/support.sh configure
bash scripts/support.sh repo-keygen
```

`configure` 隐藏输入 DeepSeek 密钥和 OneBot 令牌，回车保留旧值，不需手工修改 `.env`。`repo-keygen` 创建 `~/.ssh/tulpa_knowledge_ed25519` 专用密钥，已有密钥不覆盖。私钥不带口令以支持无人值守 Git 拉取，请保护服务器账户；私钥不会进入知识索引或模型请求。

给私有仓库添加只读公钥，有两种方式：在 GitHub Settings → Deploy keys 粘贴输出的公钥；或通过下面命令隐藏输入已有的 GitHub Token：

```bash
bash scripts/support.sh repo-authorize https://github.com/OWNER/REPO
```

此命令只调用 GitHub API 添加 `read_only=true` 的公钥，不保存 Token，不把 Token 放进 URL、命令参数或 `.env`。Fine-grained Token 应只选该仓库，并具备 **Administration: write** 权限，否则无法创建 Deploy Key。权限要求见 [GitHub 官方 REST 文档](https://docs.github.com/en/rest/deploy-keys/deploy-keys#create-a-deploy-key)。GitHub 说明删除用于创建 Deploy Key 的 Personal Access Token 也会删除对应 Deploy Key；如要取消该 Token，请确认并重新配置仓库密钥。

首次连接需要核对 [GitHub 官方 SSH 指纹](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/githubs-ssh-key-fingerprints)，执行：

```bash
ssh -i ~/.ssh/tulpa_knowledge_ed25519 -o IdentitiesOnly=yes -T git@github.com
bash scripts/support.sh repo-bind https://github.com/OWNER/REPO
# 指定分支时：repo-bind https://github.com/OWNER/REPO --branch main
bash scripts/deploy-linux.sh service
sudo loginctl enable-linger "$(id -un)"
bash scripts/support.sh project-update
bash scripts/support.sh groups
```

将 OWNER / REPO 替换成真实地址。SSH 显示认证成功但不提供 shell 是正常结果，退出码通常为 1。程序的 Git 操作强制检查已知主机密钥，不关闭 SSH 校验。`repo-bind` 把远端克隆到 `knowledge/github` 并保存绑定；该目录在 `.gitignore` 中，不会作为 Tulpa 源码提交。没有执行仓库安装脚本、Git hooks 或自动下载子模块。

`groups` 返回群名与完整 `conversation_id`。把下列示例换成实际 ID（不要只填群号）：

```bash
bash scripts/support.sh documents-update '123456:group:987654'
bash scripts/support.sh bot-start '123456:group:987654'
bash scripts/support.sh status
bash scripts/support.sh bugs
bash scripts/support.sh reviews
```

`status` 显示连接、运行状态、北京时间当天的已用 / 预留 / 剩余额度；`bugs` 显示最近 100 条问题，`reviews` 显示最近 20 条审核。命令默认输出 JSON，便于终端查看和管道处理。机器人由唯一后台服务运行，命令行只是本机控制客户端，不会额外启动收发消费者。

后续手动同步 GitHub 与知识索引：

```bash
bash scripts/support.sh bot-stop
bash scripts/support.sh repo-update
bash scripts/support.sh documents-update '123456:group:987654'
bash scripts/support.sh bot-start '123456:group:987654'
```

`repo-update` 会验证远端、分支和本地改动，只执行快进更新，然后刷新索引。不覆盖本地改动，也不会自动恢复目标群；任一步失败先处理错误，再恢复。当前没有定时 Git 拉取。

其他命令：

```bash
bash scripts/support.sh bug-status 1 已解决
bash scripts/support.sh preview '字幕导出失败怎么办' --group '123456:group:987654'
bash scripts/support.sh project-update --path /home/user/asmrTranstor
bash scripts/support.sh --help
```

试答会调用回答和审核模型并使用每日额度，只在终端显示，不发送 QQ。不用 GitHub 时直接使用 `project-update --path` 指定本机目录。群文件更新需要机器人账号具有读取群文件权限。

## 1. 准备服务器与项目文件

需要 Python 3.11+、venv、pip。Ubuntu/Debian 可执行：

```bash
sudo apt-get update
sudo apt-get install -y python3 python3-venv python3-pip git openssh-client
```

把项目源码上传到普通用户可写的目录，例如 `~/Tulpa`。上传当前未提交的源码文件也要包含 `app_support.py`、`chatlocal/support_*.py`、`web/support*` 和 `scripts/deploy-linux.sh`。不要上传 Windows 的 `.venv`、本机 `.env`、`data`、`.cache`、`.tmp`、`imports` 或桌面打包文件；其中可能包含密钥和聊天资料。Linux 新建自己的数据库。

在服务器执行（无须给脚本加可执行权限）：

```bash
cd ~/Tulpa
bash scripts/deploy-linux.sh install
bash scripts/support.sh configure
```

不要用 sudo 执行部署脚本。脚本建立 `.venv-linux`，只在 `.env` 不存在时创建模板，已有数据库和配置会保留。重复 install 可安装代码更新后的依赖；更新运行中的代码后须重启服务。

`.env` 填写示例：

```dotenv
API_BASE=https://api.deepseek.com
API_KEY=填写自己的DeepSeek密钥
MODEL=deepseek-flash
REPLY_ONEBOT_URL=http://127.0.0.1:3000
REPLY_ONEBOT_TOKEN=填写HTTP服务令牌
REPLY_ONEBOT_WS_URL=ws://127.0.0.1:3001
REPLY_ONEBOT_WS_TOKEN=填写WebSocket服务令牌
ASMR_PROJECT_ROOT=/home/你的用户名/asmrTranstor
```

项目目录应包含 README；前后端源码可选，仅建立本地索引，不发送给线上模型。可以只上传公开的 README。不要把含密钥、私人业务说明的文档作为公开答疑资料。当前审核与过滤无法保证任意敏感信息都能被识别。

默认依赖支持 TXT、Markdown 和 PDF 群文档。若群文档还有 Word、Excel 或 PowerPoint，再安装 Office 解析依赖：

```bash
.venv-linux/bin/python -m pip install -r requirements-artifacts.txt
```

## 2. 接入 QQ

脚本部署群助手，不安装或登录 QQ 客户端。需另外准备兼容 OneBot v11 的 QQ 网关，使用机器人 QQ 账号登录并加入目标群。网关开启 **HTTP API 服务** 和 **正向 WebSocket 事件服务**，在 `.env` 填写网关实际端口与令牌。不要将反向 WebSocket 地址填入这里。群助手需要查询群列表、接收群消息、发送群消息；下载群文档还需群文件相关接口。

网关与群助手在同一 Linux 主机时，监听 `127.0.0.1` 即可。网关如在 Docker 内，映射到宿主机的端口也应绑定 `127.0.0.1`。

若 QQ 网关仍在 Windows 电脑上，从该 Windows 电脑建立到 Linux 的 SSH 反向隧道（把地址、账号和端口换成实际值）：

```powershell
ssh -N -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -R 127.0.0.1:3000:127.0.0.1:3000 -R 127.0.0.1:3001:127.0.0.1:3001 linuxuser@server
```

保持 Windows 网关和隧道运行，Linux `.env` 仍使用上述本机 URL。Linux SSH 服务须允许端口转发，转发端口不能被占用。隧道中断会影响收发消息；此脚本不负责 Windows 网关或隧道的开机启动。

## 3. 启动与管理

先前台运行检查：

```bash
bash scripts/deploy-linux.sh run
```

服务监听 `127.0.0.1:7862`，不需要开放服务器防火墙端口。另开本地终端建立管理隧道：

```bash
ssh -N -o ExitOnForwardFailure=yes -L 127.0.0.1:7862:127.0.0.1:7862 linuxuser@server
```

本机浏览器访问 `http://127.0.0.1:7862`，打开群助手，按顺序更新项目资料、读取群列表、更新所选群文档、启动所选群自动回复。试答会使用付费 API 并计入每日 ¥5，试答不会发到群里。机器人日常运行不用人工逐条审批。

前台检查结束按 Ctrl+C。切换后台运行：

```bash
bash scripts/deploy-linux.sh service
sudo loginctl enable-linger "$(id -un)"
```

使用 systemd 用户服务；enable-linger 使它退出 SSH 后继续运行并在开机时启动。不要同时运行前台与后台实例，也不要增加 uvicorn worker 数量，避免多个消费者。非 systemd 系统使用 run 配合该系统的进程管理器。

```bash
bash scripts/deploy-linux.sh status
bash scripts/deploy-linux.sh logs
bash scripts/deploy-linux.sh stop
systemctl --user restart tulpa-support.service
```

改 `.env` 或更新代码后执行 restart。服务停止会断开接收；在网页点击“停止自动回复”则会取消目标群的自动启动。卸载服务使用 `bash scripts/deploy-linux.sh uninstall-service`，配置与数据库保留。

健康检查：`curl --fail http://127.0.0.1:7862/healthz`。此检查只说明管理服务在运行；QQ 连接状态和剩余额度以面板为准。

## 4. 数据与边界

- `data/asmr-support.sqlite3`：知识索引、Bug、费用预留和审核记录。
- `data/chats.sqlite3`：消息及发送操作；`data/mcp-access.sqlite3`：本机授权。
- 群文档下载与解析缓存还会使用 `data`、`.cache`、`imports` 等目录。

迁移或备份前先 stop 服务，再复制整个 `data` 目录及必要的文档缓存，避免只复制 SQLite 主文件遗漏 WAL 数据。保管备份，它含群消息及问题资料。

管理页面没有公网账号登录功能，必须保留本机监听与 SSH 隧道。进程以普通用户运行，开启 systemd NoNewPrivileges；这不是完全隔离的代码执行沙箱。在线模型没有执行命令或读取源码的工具，源码也不进入公开问答上下文，回复还会经过独立审核，但不能承诺零泄露或完全防御提示词注入。

本次验证涵盖入口路由、访问限制、脚本 Bash 语法、10 项命令行隔离检查及群助手测试；未在真实 Linux 主机、GitHub 私有仓库或真实 QQ 群进行部署验证。
