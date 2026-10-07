"""Local human playtest server; isolated SQLite, real DeepSeek, no QQ receiver."""
from pathlib import Path
import json
import hashlib
import re
import uuid

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from chatlocal.adventure import Adventure, AdventureError
from chatlocal.adventure_model import DeepSeekAdventure
from chatlocal.config import settings
from chatlocal.support_budget import SupportBudget

ROOT=Path(__file__).resolve().parent
SANDBOX=ROOT/'.tmp'/'adventure-playtest'


class Move(BaseModel):
    model_config=ConfigDict(extra='forbid')
    request_id: str=Field(min_length=1,max_length=160)
    action: str=Field(default='',max_length=1500)
    flee: bool=False
    final_boss: bool=False


def public_attempt(attempt):
    if not attempt:
        return None
    # Never expose the secret roll, private state snapshot or pre-award reward proposal.
    result=attempt.get('result')
    return dict(id=attempt['id'],phase=attempt['phase'],event=attempt['event'],
                action=attempt.get('action'),day=attempt['day'],
                result={k:v for k,v in result.items() if k!='roll'} if result else None)


def create_app(game=None):
    if game is None:
        ledger=SupportBudget(SANDBOX/'costs.sqlite3')
        game=Adventure(SANDBOX/'players.sqlite3',DeepSeekAdventure(ledger,settings))
    app=FastAPI(docs_url=None,redoc_url=None,openapi_url=None)
    app.state.game=game
    app.add_middleware(TrustedHostMiddleware,allowed_hosts=['localhost','127.0.0.1','[::1]','testserver'])

    @app.middleware('http')
    async def local(request,call_next):
        if request.client and request.client.host not in ('127.0.0.1','::1','testclient'):
            return JSONResponse({'detail':'仅限本机访问。'},status_code=403)
        if request.method not in ('GET','HEAD'):
            if (request.headers.get('X-Playtest-Action')!='play'
                or request.headers.get('Sec-Fetch-Site')=='cross-site'
                or request.headers.get('Origin') not in (None,str(request.base_url).rstrip('/'))):
                return JSONResponse({'detail':'请从本地试玩页面操作。'},status_code=403)
        identity=request.cookies.get('adventure_player','')
        fresh=not re.fullmatch(r'[0-9a-f]{32}',identity)
        if fresh:
            identity=uuid.uuid4().hex
        request.state.player='web-playtest:'+identity
        response=await call_next(request)
        if fresh:
            response.set_cookie('adventure_player',identity,max_age=365*86400,
                                httponly=True,samesite='strict')
        response.headers['Cache-Control']='no-store'
        response.headers['X-Content-Type-Options']='nosniff'
        response.headers['Referrer-Policy']='no-referrer'
        response.headers['Content-Security-Policy']="default-src 'self'; script-src 'self'; style-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        return response

    @app.get('/')
    def home():
        return FileResponse(ROOT/'web'/'adventure.html')

    @app.get('/assets/{name}')
    def asset(name:str):
        if name not in ('adventure.css','adventure.js','bug-dashboard.svg'):
            raise HTTPException(404)
        return FileResponse(ROOT/'web'/name)

    def state(player):
        snapshot=game.status(player)
        snapshot['chat_id']='adventure-chat:'+hashlib.sha256(player.encode()).hexdigest()[:24]
        snapshot['current']=public_attempt(snapshot['current'])
        with game.connect() as db:
            rows=db.execute('SELECT id FROM adventure_attempts WHERE player=? ORDER BY rowid DESC LIMIT 12',
                            (player,)).fetchall()
            snapshot['history']=[public_attempt(game._attempt(db,row['id'])) for row in rows]
        return snapshot

    @app.get('/api/state')
    def status(request:Request):
        return state(request.state.player)

    @app.post('/api/{command}')
    def move(command:str,body:Move,request:Request):
        player=request.state.player
        try:
            if command=='start':
                game.start(player,body.request_id,final_boss=body.final_boss)
            elif command=='act':
                game.act(player,body.request_id,body.action,flee=body.flee)
            elif command=='retry':
                game.retry(player)
            else:
                raise HTTPException(404)
            return state(player)
        except AdventureError as exc:
            raise HTTPException(409,str(exc)) from None

    @app.get('/healthz')
    def health():
        return {'status':'ok','mode':'real-deepseek-human-playtest'}

    return app


if __name__=='__main__':
    import uvicorn
    uvicorn.run(create_app(),host='127.0.0.1',port=7864,access_log=False)
