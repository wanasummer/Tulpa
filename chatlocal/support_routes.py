"""Same-origin UI controls; budget and knowledge settings are not model tools."""
from contextlib import asynccontextmanager
from urllib.parse import urlparse

import anyio
from fastapi import HTTPException, Request

from .support_bot import SupportBot


def install_support_routes(app, bot=None):
    bot = bot or SupportBot(app.state.tulpa_mcp)
    app.state.asmr_support = bot
    previous = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(application):
        async with previous(application):
            await anyio.to_thread.run_sync(bot.resume)
            try:
                yield
            finally:
                await anyio.to_thread.run_sync(lambda: bot.stop(permanent=False))
    app.router.lifespan_context = lifespan

    def local(request, write=False):
        if request.client and request.client.host not in ('127.0.0.1', '::1', 'testclient'):
            raise HTTPException(403)
        if urlparse(str(request.base_url)).hostname not in ('127.0.0.1', 'localhost', '::1', 'testserver'):
            raise HTTPException(403)
        if request.headers.get('origin') not in (None, str(request.base_url).rstrip('/')) or request.headers.get('sec-fetch-site') == 'cross-site':
            raise HTTPException(403)
        if write and (request.headers.get('x-chatweave-ui') != '1' or request.headers.get('content-type', '').split(';')[0] != 'application/json'):
            raise HTTPException(403)

    def invoke(fn):
        try:
            return fn()
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None

    @app.get('/api/support')
    def status(request: Request):
        local(request)
        return bot.status()

    @app.get('/api/support/groups')
    def groups(request: Request):
        local(request)
        return dict(groups=invoke(bot.groups))

    @app.post('/api/support/project')
    def project(request: Request, body: dict):
        local(request, True)
        if set(body)-{'project_root'} or ('project_root' in body and not isinstance(body['project_root'], str)):
            raise HTTPException(400, '项目路径字段无效。')
        return invoke(lambda: bot.refresh_project(body.get('project_root')))

    def cid(body):
        if set(body) != {'conversation_id'} or not isinstance(body['conversation_id'], str) or len(body['conversation_id']) > 120:
            raise HTTPException(400, '请选择一个 QQ 群。')
        return body['conversation_id']

    @app.post('/api/support/documents')
    def documents(request: Request, body: dict):
        local(request, True)
        target = cid(body)
        return invoke(lambda: bot.refresh_group(target))

    @app.post('/api/support/start')
    def start(request: Request, body: dict):
        local(request, True)
        target = cid(body)
        return invoke(lambda: bot.start(target))

    @app.post('/api/support/stop')
    def stop(request: Request, body: dict):
        local(request, True)
        if body:
            raise HTTPException(400, '停止参数无效。')
        return invoke(bot.stop)

    @app.post('/api/support/preview')
    def preview(request: Request, body: dict):
        local(request, True)
        if set(body)-{'question','conversation_id'} or not isinstance(body.get('question'), str) or not 1 <= len(body['question'].strip()) <= 6000:
            raise HTTPException(400, '请输入最多6000字的使用问题。')
        target = body.get('conversation_id', '')
        if not isinstance(target, str) or len(target) > 120:
            raise HTTPException(400, '群范围无效。')
        # Preview costs the same fixed budget, but has no send side effect.
        return invoke(lambda: bot.preview(body['question'], target))

    @app.put('/api/support/bugs/{bug_id}')
    def bug(request: Request, bug_id: int, body: dict):
        local(request, True)
        if set(body) != {'status'} or not isinstance(body['status'], str):
            raise HTTPException(400, '问题状态字段无效。')
        return invoke(lambda: bot.set_bug_status(bug_id, body['status']))
