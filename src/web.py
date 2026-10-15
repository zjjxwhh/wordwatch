import asyncio
from contextlib import asynccontextmanager
import hmac
from pathlib import Path
import time
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
import httpx

from .db import Store
from .models import AdminLogin, AdminSetup, AdminUpdate, Channel, LoginCode, LoginVerify, Monitor, Settings, PositiveID
from .push import deliver
from .runtime import Runtime

STATIC = Path(__file__).parent / 'static'


def create_app(directory='data', background=True):
    store = Store(directory)
    runtime = Runtime(store)

    @asynccontextmanager
    async def lifespan(app):
        if background:
            await runtime.start()
        try:
            yield
        finally:
            await runtime.stop()
            store.db.close()

    app = FastAPI(title='观言 · WordWatch', lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.store, app.state.runtime = store, runtime
    failures = {}
    session_cookie = 'relay_session'

    def check_origin(request):
        origin = request.headers.get('origin')
        if origin and urlsplit(origin).netloc != request.headers.get('host'):
            raise HTTPException(403, '不允许跨站请求')

    async def auth(request: Request):
        if not store.setting('admin'):
            raise HTTPException(503, '请先在首页初始化管理员账户')
        if not store.valid_session(request.cookies.get(session_cookie)):
            raise HTTPException(401, '登录已失效，请重新登录')
        if request.method not in ('GET', 'HEAD'):
            check_origin(request)

    @app.post('/api/auth/login')
    async def login(model: AdminLogin, request: Request, response: Response):
        check_origin(request)
        password_record = store.setting('admin')
        if not password_record:
            raise HTTPException(409, '请先初始化管理员账户')
        peer = request.client.host if request.client else 'unknown'
        count, until = failures.get(peer, (0, 0))
        if count >= 10 and until > time.time():
            raise HTTPException(429, '登录尝试过多，请稍后再试')
        if until <= time.time():
            count = 0
        # Reserve the attempt before yielding for password hashing. Otherwise
        # concurrent requests all see the same old count and bypass the limit.
        failures[peer] = (count + 1, until if until > time.time() else time.time() + 60)
        valid = hmac.compare_digest(model.username.encode(), password_record.get('username', 'admin').encode())
        # Only password hashing runs in a worker thread. Keep SQLite access on
        # the event-loop thread so auth requests cannot race transactions.
        password_valid = await asyncio.to_thread(store.check_password, model.password, password_record)
        valid = valid and password_valid
        if not valid:
            raise HTTPException(401, '账户或密码不正确')
        failures.pop(peer, None)
        token = store.create_session(password_record)
        if not token:
            raise HTTPException(409, '账户信息已变化，请重新登录')
        store.delete_session(request.cookies.get(session_cookie))
        response.set_cookie(session_cookie, token, max_age=43200, httponly=True,
                            secure=request.url.scheme == 'https', samesite='strict', path='/')
        return {'ok': True}

    @app.post('/api/auth/logout')
    async def logout(request: Request, response: Response):
        check_origin(request)
        store.delete_session(request.cookies.get(session_cookie))
        response.delete_cookie(session_cookie, path='/')
        return {'ok': True}

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        # Pydantic's default response includes submitted secrets in `input`.
        return JSONResponse(status_code=422, content={'detail': '; '.join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())})

    @app.middleware('http')
    async def secure_headers(request, call_next):
        response = await call_next(request)
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'self'"
        return response

    @app.get('/healthz')
    async def health():
        healthy = not background or runtime.status()['worker_ok']
        return JSONResponse({'ok': healthy}, status_code=200 if healthy else 503)

    @app.get('/')
    async def index(request: Request):
        if not store.setting('admin'):
            return FileResponse(STATIC / 'setup.html')
        if not store.valid_session(request.cookies.get(session_cookie)):
            return FileResponse(STATIC / 'login.html')
        return FileResponse(STATIC / 'index.html')

    @app.get('/login')
    async def login_page(request: Request):
        if not store.setting('admin') or store.valid_session(request.cookies.get(session_cookie)):
            return RedirectResponse('/', status_code=303)
        return FileResponse(STATIC / 'login.html')

    @app.get('/api/setup')
    async def setup_status():
        return {'initialized': bool(store.setting('admin'))}

    @app.post('/api/setup')
    async def setup(model: AdminSetup, request: Request):
        origin = request.headers.get('origin')
        if origin and urlsplit(origin).netloc != request.headers.get('host'):
            raise HTTPException(403, '不允许跨站请求')
        if store.setting('admin'):
            raise HTTPException(409, '管理员账户已初始化，请登录')
        record = await asyncio.to_thread(store.password_record, model.password, model.username)
        if not store.initialize_admin(record):
            raise HTTPException(409, '管理员账户已初始化，请登录')
        return {'ok': True}

    api_auth = [Depends(auth)]

    @app.get('/api/admin', dependencies=api_auth)
    async def admin_info():
        return {'username': store.setting('admin').get('username', 'admin')}

    @app.put('/api/admin', dependencies=api_auth)
    async def update_admin(model: AdminUpdate, response: Response):
        old_record = store.setting('admin')
        if not await asyncio.to_thread(store.check_password, model.current_password, old_record):
            raise HTTPException(403, '当前密码不正确')
        if model.password:
            record = await asyncio.to_thread(store.password_record, model.password, model.username)
        else:
            record = old_record | {'username': model.username}
        if not store.update_admin(old_record, record):
            raise HTTPException(409, '账户信息已发生变化，请重新登录后再修改')
        response.delete_cookie(session_cookie, path='/')
        return {'ok': True}

    @app.get('/api/status', dependencies=api_auth)
    async def status():
        counts = {name: store.db.execute(f'SELECT COUNT(*) FROM {name}').fetchone()[0] for name in ('monitors', 'channels', 'messages')}
        counts['failed'] = store.db.execute("SELECT COUNT(*) FROM deliveries WHERE status='failed'").fetchone()[0]
        return runtime.status() | {'counts': counts}

    def public_channel(channel):
        result = dict(channel)
        c = dict(channel['config'])
        for key in ('token', 'device_key'):
            if key in c:
                c[key + '_configured'] = bool(c[key])
                c[key] = ''
        result['config'] = c
        return result

    @app.get('/api/channels', dependencies=api_auth)
    async def channels():
        return [public_channel(c) for c in store.channels()]

    @app.post('/api/channels', dependencies=api_auth)
    async def add_channel(model: Channel):
        return {'id': store.save_channel(model)}

    @app.put('/api/channels/{item_id}', dependencies=api_auth)
    async def edit_channel(item_id: PositiveID, request: Request):
        old = store.channel(item_id)
        if not old:
            raise HTTPException(404, '渠道不存在')
        try:
            body = await request.json()
        except ValueError:
            raise HTTPException(422, '请求正文必须为有效 JSON') from None
        if isinstance(body, dict) and isinstance(body.get('config'), dict) and body.get('kind') == old['kind']:
            for key in ('token', 'device_key'):
                if not body['config'].get(key) and old['config'].get(key):
                    body['config'][key] = old['config'][key]
        try:
            model = Channel.model_validate(body)
        except ValueError:
            raise HTTPException(422, '渠道参数无效，请检查必填字段及 Bark 额外参数') from None
        store.save_channel(model, item_id)
        return {'id': item_id}

    @app.delete('/api/channels/{item_id}', dependencies=api_auth)
    async def delete_channel(item_id: PositiveID):
        store.delete('channels', item_id)
        return {'ok': True}

    @app.post('/api/channels/{item_id}/test', dependencies=api_auth)
    async def test_channel(item_id: PositiveID):
        channel = store.channel(item_id)
        if not channel:
            raise HTTPException(404, '渠道不存在')
        try:
            async with httpx.AsyncClient(timeout=20, proxy=store.settings()['http_proxy'] or None, trust_env=False) as http:
                await deliver(http, channel, {'title': '观言 · WordWatch', 'body': '推送通道测试成功', 'url': ''})
        except Exception as exc:
            raise HTTPException(502, f'测试推送失败：{type(exc).__name__}') from None
        return {'ok': True}

    @app.get('/api/monitors', dependencies=api_auth)
    async def monitors():
        return store.monitors()

    @app.post('/api/monitors', dependencies=api_auth)
    async def add_monitor(model: Monitor):
        try:
            return {'id': store.save_monitor(model)}
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None

    @app.put('/api/monitors/{item_id}', dependencies=api_auth)
    async def edit_monitor(item_id: PositiveID, model: Monitor):
        if not any(m['id'] == item_id for m in store.monitors()):
            raise HTTPException(404, '监控项不存在')
        try:
            return {'id': store.save_monitor(model, item_id)}
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None

    @app.delete('/api/monitors/{item_id}', dependencies=api_auth)
    async def delete_monitor(item_id: PositiveID):
        store.delete('monitors', item_id)
        return {'ok': True}

    @app.get('/api/messages', dependencies=api_auth)
    async def messages(monitor_id: PositiveID | None = None, before: PositiveID | None = None, limit: int = Query(default=50, ge=1, le=100)):
        return store.messages(monitor_id, before, limit)

    @app.post('/api/deliveries/{item_id}/retry', dependencies=api_auth)
    async def retry(item_id: PositiveID):
        with store.db:
            changed = store.db.execute("UPDATE deliveries SET status='pending',attempts=0,due=0,error=NULL WHERE id=? AND status='failed' AND channel_id IS NOT NULL", (item_id,)).rowcount
        if not changed:
            raise HTTPException(409, '只能重试渠道仍存在的失败任务')
        return {'ok': True}

    @app.get('/api/settings', dependencies=api_auth)
    async def settings():
        value = store.settings()
        for key in ('api_hash', 'proxy_password', 'http_proxy'):
            value[key + '_configured'] = bool(value[key])
            value[key] = ''
        return value

    @app.put('/api/settings', dependencies=api_auth)
    async def save_settings(model: Settings):
        value, old = model.model_dump(), store.settings()
        clear_http_proxy = value.pop('clear_http_proxy')
        for key in ('api_hash', 'proxy_password', 'http_proxy'):
            if not value[key]:
                value[key] = old[key]
        if clear_http_proxy:
            value['http_proxy'] = ''
        store.set_setting('telegram', value)
        await runtime.reset()
        return {'ok': True}

    @app.post('/api/telegram/code', dependencies=api_auth)
    async def code(model: LoginCode):
        try:
            return await runtime.request_code(model.phone)
        except Exception as exc:
            raise HTTPException(400, str(exc) if isinstance(exc, ValueError) else type(exc).__name__) from None

    @app.post('/api/telegram/verify', dependencies=api_auth)
    async def verify(model: LoginVerify):
        try:
            return await runtime.verify(model.code, model.password)
        except Exception as exc:
            raise HTTPException(400, str(exc) if isinstance(exc, ValueError) else type(exc).__name__) from None

    @app.get('/api/telegram/dialogs', dependencies=api_auth)
    async def dialogs():
        if not runtime.authorized:
            raise HTTPException(409, '请先登录 Telegram')
        try:
            return [{'id': d.id, 'name': d.name} async for d in runtime.client.iter_dialogs() if d.is_group]
        except Exception as exc:
            raise HTTPException(502, type(exc).__name__) from None

    app.mount('/static', StaticFiles(directory=STATIC), name='static')
    return app
