"""Local dashboard for the resident bot's SQLite feedback."""
from datetime import datetime, timezone
from contextlib import closing
from pathlib import Path
import sqlite3

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware

ROOT = Path(__file__).resolve().parent
DATABASE = ROOT / 'data' / 'asmr-support.sqlite3'


def snapshot(path=DATABASE):
    # A read transaction keeps totals and rows consistent, including WAL databases.
    with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=5)) as db:
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')
        counts = [dict(r) for r in db.execute('SELECT status,count(*) count FROM support_bugs GROUP BY status')]
        groups = [dict(r) for r in db.execute('SELECT cid,count(*) count FROM support_bugs GROUP BY cid ORDER BY count DESC')]
        days = [dict(r) for r in db.execute('SELECT day,count(*) count FROM support_bugs GROUP BY day ORDER BY day DESC LIMIT 14')][::-1]
        bugs = [dict(r) for r in db.execute('''SELECT b.*,coalesce(r.sender,'') sender,
            coalesce(r.question,'') question FROM support_bugs b
            LEFT JOIN support_replies r ON r.cid=b.cid AND r.mid=b.mid
            ORDER BY b.id DESC LIMIT 500''')]
        total = sum(r['count'] for r in counts)
        resolved = sum(r['count'] for r in counts if r['status'] == '已解决')
        return dict(total=total, resolved=resolved, open=total-resolved, groups=groups,
                    statuses=counts, days=days, bugs=bugs, shown=len(bugs),
                    updated_at=datetime.now(timezone.utc).isoformat())


def create_app(database=DATABASE):
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=['127.0.0.1','localhost','[::1]','testserver'])

    @app.middleware('http')
    async def local_only(request, call_next):
        if request.client and request.client.host not in ('127.0.0.1','::1','testclient'):
            return JSONResponse({'detail': 'Local access only'}, status_code=403)
        response = await call_next(request)
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'"
        return response

    @app.get('/')
    def index():
        return FileResponse(ROOT / 'web' / 'bug-dashboard.html')

    @app.get('/assets/{name}')
    def asset(name: str):
        if name not in ('bug-dashboard.css','bug-dashboard.js','bug-dashboard.svg'):
            raise HTTPException(404)
        return FileResponse(ROOT / 'web' / name)

    @app.get('/api/bugs')
    def data():
        try:
            return snapshot(database)
        except sqlite3.Error:
            raise HTTPException(503, '暂时无法读取反馈数据库，请稍后刷新。') from None

    @app.get('/healthz')
    def health():
        try:
            snapshot(database)
            return {'status': 'ok'}
        except sqlite3.Error:
            raise HTTPException(503, '反馈数据库不可用。') from None

    @app.delete('/api/bugs/{bug_id}')
    def delete_bug(bug_id: int, request: Request):
        # Require a same-origin browser action; cross-site forms cannot set this header.
        if (request.headers.get('X-Dashboard-Action') != 'delete'
                or request.headers.get('Sec-Fetch-Site') == 'cross-site'
                or (request.headers.get('Origin') is not None
                    and request.headers['Origin'] != str(request.base_url).rstrip('/'))):
            raise HTTPException(403, '请从本地看板删除反馈。')
        try:
            with closing(sqlite3.connect(database.resolve().as_uri() + '?mode=rw', uri=True, timeout=5)) as db:
                with db:
                    deleted = db.execute('DELETE FROM support_bugs WHERE id=?', (bug_id,)).rowcount
            if not deleted:
                raise HTTPException(404, '这条反馈已不存在，请刷新列表。')
            return {'deleted': bug_id}
        except sqlite3.Error:
            raise HTTPException(503, '暂时无法删除反馈，请稍后重试。') from None

    return app


if __name__ == '__main__':
    import uvicorn
    uvicorn.run(create_app(), host='127.0.0.1', port=7863, access_log=False)
