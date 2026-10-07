"""Linux-friendly support service, without desktop readers or Gradio."""
import os
from contextlib import asynccontextmanager

import anyio
from dotenv import dotenv_values
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware

from chatlocal.config import ROOT
from chatlocal.mcp_access import MCPAccess
from chatlocal.mcp_server import MCPService
from chatlocal.store import Store
from chatlocal.support_bot import SupportBot
from chatlocal.support_routes import install_support_routes


def acquire_instance_lock(path=None):
    """Exclude duplicate Linux support processes before creating any receivers."""
    import fcntl

    path = path or ROOT / 'data' / 'support-service.lock'
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(path, 'a', encoding='utf-8')
    os.chmod(path, 0o600)
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        raise RuntimeError('Tulpa 群助手已在运行，拒绝启动重复实例。') from None
    # Keep the file and descriptor until process exit; unlinking breaks exclusion.
    return handle


def create_app():
    service = MCPService(MCPAccess(Store()))

    @asynccontextmanager
    async def lifespan(app):
        await anyio.to_thread.run_sync(service.start)
        try:
            yield
        finally:
            await anyio.to_thread.run_sync(service.stop)

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=['localhost', '127.0.0.1', '[::1]', 'testserver'])
    app.state.tulpa_mcp = service
    values = {**dotenv_values(ROOT / '.env'), **os.environ}
    install_support_routes(app, SupportBot(service, project_root=values.get('ASMR_PROJECT_ROOT') or None))

    @app.get('/')
    def index():
        return HTMLResponse((ROOT / 'web' / 'support-home.html').read_text(encoding='utf-8'), headers={
            'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer',
            'Content-Security-Policy': "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
        })

    @app.get('/healthz')
    def health():
        return {'status': 'ok'}

    @app.get('/ui/{name}')
    def asset(name: str):
        if name not in {'app.css', 'desktop.css', 'support.js', 'support-home.css'}:
            raise HTTPException(404)
        return FileResponse(ROOT / 'web' / name, headers={'Cache-Control': 'no-cache'})

    return app


if __name__ == '__main__':
    try:
        instance_lock = acquire_instance_lock()
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from None
    import uvicorn
    # A single worker owns the receiver and daily budget reservations.
    uvicorn.run(create_app(), host='127.0.0.1', port=7862, access_log=False)
