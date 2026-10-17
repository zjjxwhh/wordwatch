import asyncio
from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient
import httpx
import pytest
from telethon.tl.types import User
from telethon.errors import SessionPasswordNeededError

from src.db import Store
from src.models import Channel, LoginVerify, Monitor, Settings
from src.push import DeliveryError, deliver, truncate
from src.runtime import Runtime
from src.web import create_app


@pytest.fixture
def store(tmp_path):
    value = Store(tmp_path)
    yield value
    value.db.close()


def channel(store, name='Bark', kind='bark', enabled=True):
    config = {'server': 'https://api.day.app', 'device_key': 'secret', 'params': {'icon': 'https://example.com/icon.png', 'sound': 'minuet', 'volume': 5}}
    if kind == 'telegram':
        config = {'token': 'bot-secret', 'chat_id': '123'}
    return store.save_channel(Channel(name=name, kind=kind, enabled=enabled, config=config))


def rule(store, channels, name='测试监控', enabled=True):
    item_id = store.save_monitor(Monitor(name=name, chat_id=-100123, users=[42], channel_ids=channels, enabled=enabled))
    return next(r for r in store.monitors() if r['id'] == item_id)


def message():
    return {'chat_id': -100123, 'message_id': 7, 'sender_id': 42, 'sender_name': 'Alice', 'username': 'alice',
            'chat_title': '测试群', 'text': '<script>hello</script>😀', 'url': 'https://t.me/c/123/7',
            'sent_at': datetime.now(timezone.utc).isoformat()}


def test_reusable_multiple_channels_persistent_atomic_queue(store):
    ids = [channel(store, 'iPhone'), channel(store, 'iPad'), channel(store, 'Bot A', 'telegram'), channel(store, 'Bot B', 'telegram')]
    a, b = rule(store, ids), rule(store, [ids[0]], '第二个监控')
    payload = {'title': 'test', 'body': 'body', 'url': ''}
    store.record(a, message(), payload)
    store.record(a, message(), payload)
    store.record(b, message(), payload)
    assert len(store.messages()) == 2
    assert len(store.messages(a['id'])[0]['deliveries']) == 4
    assert store.db.execute('SELECT COUNT(*) FROM deliveries').fetchone()[0] == 5
    reopened = Store(store.directory)
    assert len(reopened.channels()) == 4
    assert len(reopened.monitors()) == 2
    assert len(reopened.messages()) == 2
    reopened.db.close()


def test_record_without_notifications_and_channel_pause_delete(store):
    cid = channel(store)
    item = rule(store, [cid])
    store.record(item, message(), {'body': 'test'})
    ch = store.channel(cid)
    store.save_channel(Channel(**{k: ch[k] for k in ('name', 'kind', 'config')} , enabled=False), cid)
    assert store.next_job() is None
    store.delete('channels', cid)
    assert store.messages()[0]['deliveries'][0]['status'] == 'cancelled'
    assert store.monitors()[0]['channel_ids'] == []
    store.delete('monitors', item['id'])
    assert store.messages()[0]['monitor_id'] is None
    assert store.messages()[0]['monitor_name'] == '测试监控'


def test_retry_persisted_and_isolated(store):
    cid = channel(store)
    store.record(rule(store, [cid]), message(), {'body': 'test'})
    job = store.next_job()
    assert store.finish_job(job, False, 3, 'DeliveryError', 60) == 'pending'
    assert store.next_job() is None
    assert store.db.execute('SELECT due FROM deliveries').fetchone()[0] > datetime.now(timezone.utc).timestamp() + 55
    job['attempts'] = 2
    assert store.finish_job(job, False, 3, 'DeliveryError') == 'failed'
    assert store.messages()[0]['deliveries'][0]['attempts'] == 3


def test_invalid_channel_reference_rolls_back(store):
    with pytest.raises(ValueError):
        rule(store, [999])
    assert not store.monitors()


async def test_runtime_filters_records_and_no_history(store):
    cid = channel(store)
    item = rule(store, [cid])
    runtime = Runtime(store)
    class Event:
        is_group = True
        chat_id = -100123
        id = 7
        sender_id = 42
        date = datetime.now(timezone.utc)
        raw_text = '服务启动后的新消息'
        async def get_sender(self):
            return User(id=42, first_name='Alice', username='alice')
        async def get_chat(self):
            return SimpleNamespace(title='测试群', username=None)
    event = Event()
    event.date = runtime.started_at - timedelta(seconds=1)
    await runtime.on_message(event)
    assert not store.messages()
    event.date = datetime.now(timezone.utc)
    event.sender_id = 99
    await runtime.on_message(event)
    assert not store.messages()
    event.sender_id = 42
    event.chat_id = -100999
    await runtime.on_message(event)
    assert not store.messages()
    event.chat_id = -100123
    await runtime.on_message(event)
    await runtime.on_message(event)
    assert len(store.messages(item['id'])) == 1
    assert store.messages()[0]['url'] == 'https://t.me/c/123/7'
    assert store.messages()[0]['text'] == event.raw_text
    assert store.next_job()['channel_id'] == cid


async def test_bark_post_push_parameters_and_telegram(store):
    bark = store.channel(channel(store))
    bot = store.channel(channel(store, 'Bot', 'telegram'))
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={'code': 200, 'ok': True})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        for ch in (bark, bot):
            await deliver(http, ch, {'title': '标题', 'body': '😀' * 5000, 'url': 'https://t.me/a/1'})
    assert str(requests[0].url) == 'https://api.day.app/push'
    assert requests[0].method == 'POST'
    body = json.loads(requests[0].content)
    assert body['icon'] == 'https://example.com/icon.png'
    assert body['sound'] == 'minuet'
    assert body['volume'] == 5
    assert body['device_key'] == 'secret'
    assert body['url'] == 'https://t.me/a/1'
    assert len(json.loads(requests[1].content)['text'].encode('utf-16-le')) <= 8000
    assert json.loads(requests[1].content)['text'].endswith('https://t.me/a/1')


async def test_delivery_failures(store):
    ch = store.channel(channel(store, 'Bot', 'telegram'))
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(429, json={'parameters': {'retry_after': 60}}))) as http:
        with pytest.raises(DeliveryError) as caught:
            await deliver(http, ch, {'body': 'test'})
        assert caught.value.retry_after == 60
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=[]))) as http:
        with pytest.raises(DeliveryError):
            await deliver(http, ch, {'body': 'test'})


@pytest.fixture
def client(tmp_path):
    app = create_app(tmp_path, background=False)
    app.state.store.set_password('test-password-123')
    with TestClient(app) as value:
        assert value.post('/api/auth/login', json={'username': 'admin', 'password': 'test-password-123'}).status_code == 200
        yield value


def test_api_auth_and_csrf(client):
    saved = dict(client.cookies)
    client.cookies.clear()
    response = client.get('/api/settings', auth=('admin', 'test-password-123'))
    assert response.status_code == 401 and 'www-authenticate' not in response.headers
    client.cookies.update(saved)
    assert client.get('/').status_code == 200
    assert client.get('/healthz').status_code == 200
    assert client.post('/api/channels', headers={'Origin': 'https://evil.example'}, json={}).status_code == 403


def test_api_channel_secret_retention_and_multiple_references(client):
    body = {'name': '我的 iPhone', 'kind': 'bark', 'config': {'server': 'https://api.day.app', 'device_key': 'private-secret', 'params': {'sound': 'minuet'}}}
    cid = client.post('/api/channels', json=body).json()['id']
    result = client.get('/api/channels').json()[0]
    assert result['config']['device_key'] == ''
    assert result['config']['device_key_configured'] is True
    assert 'private-secret' not in client.get('/api/channels').text
    body['config']['device_key'] = ''
    body['name'] = '重命名'
    assert client.put(f'/api/channels/{cid}', json=body).status_code == 200
    assert client.app.state.store.channel(cid)['config']['device_key'] == 'private-secret'
    monitor = {'name': 'A', 'chat_id': -100123, 'users': [42], 'channel_ids': [cid]}
    mid = client.post('/api/monitors', json=monitor).json()['id']
    monitor['name'] = 'B'
    assert client.post('/api/monitors', json=monitor).status_code == 200
    assert len(client.get('/api/monitors').json()) == 2
    assert client.delete(f'/api/channels/{cid}').status_code == 200
    assert client.get('/api/monitors').json()[0]['channel_ids'] == []
    assert client.put(f'/api/monitors/{mid}', json=monitor).status_code == 422


def test_settings_persist_secret_and_clear_proxy(client):
    value = {'api_id': 12345, 'api_hash': 'secret-hash', 'http_proxy': 'http://user:password@localhost:7890'}
    assert client.put('/api/settings', json=value).status_code == 200
    public = client.get('/api/settings')
    assert 'secret-hash' not in public.text and 'user:password' not in public.text
    value.update(api_hash='', http_proxy='', clear_http_proxy=True)
    assert client.put('/api/settings', json=value).status_code == 200
    actual = client.app.state.store.settings()
    assert actual['api_hash'] == 'secret-hash'
    assert actual['http_proxy'] == ''


def test_invalid_input_secret_not_reflected(client):
    result = client.post('/api/channels', json={'name': 'Broken', 'kind': 'telegram', 'config': {'token': 'do-not-leak'}})
    assert result.status_code == 422
    assert 'do-not-leak' not in result.text
    assert client.get('/api/messages?limit=1000').status_code == 422


def test_pagination_and_retry(client):
    store = client.app.state.store
    cid = channel(store)
    item = rule(store, [cid])
    for i in range(5):
        msg = message() | {'message_id': i}
        store.record(item, msg, {'body': 'test'})
    page1 = client.get('/api/messages?limit=2').json()
    page2 = client.get(f"/api/messages?limit=2&before={page1[-1]['id']}").json()
    assert {m['id'] for m in page1}.isdisjoint({m['id'] for m in page2})
    job = store.next_job()
    store.finish_job(job, False, 1, 'DeliveryError')
    assert client.post(f"/api/deliveries/{job['id']}/retry").status_code == 200
    assert store.next_job()['attempts'] == 0


def test_password_and_unicode(store):
    store.set_password('long-password-123')
    assert store.check_password('long-password-123')
    assert not store.check_password('incorrect')
    assert len(truncate('😀' * 5000).encode('utf-16-le')) <= 8000


async def test_concurrent_auth_and_writes_do_not_share_sqlite_threads(tmp_path):
    app = create_app(tmp_path, background=False)
    app.state.store.set_password('test-password-123')
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as http:
        assert (await http.post('/api/auth/login', json={'username': 'admin', 'password': 'test-password-123'})).status_code == 200
        async def request(i):
            if i % 3 == 0:
                return await http.post('/api/monitors', json={'name': f'Monitor {i}', 'chat_id': -100123, 'users': [42]})
            return await http.get('/api/channels' if i % 3 == 1 else '/api/monitors')
        results = await asyncio.gather(*(request(i) for i in range(40)))
        assert all(r.status_code == 200 for r in results)
    app.state.store.db.close()


async def test_login_code_and_two_factor_flow(store):
    runtime = Runtime(store)
    fake = SimpleNamespace(is_user_authorized=AsyncMock(return_value=False),
                           send_code_request=AsyncMock(return_value=SimpleNamespace(phone_code_hash='hash')),
                           sign_in=AsyncMock(side_effect=[SessionPasswordNeededError(None), None]))
    fake.is_user_authorized.side_effect = [False, True]
    runtime.get_client = AsyncMock(return_value=fake)
    assert (await runtime.request_code('+8613800000000'))['status'] == 'code_sent'
    assert runtime.phone_code_hash == 'hash'
    assert (await runtime.verify('12345', ''))['status'] == 'password_required'
    assert (await runtime.verify('', 'two-factor-password'))['status'] == 'authorized'
    assert runtime.authorized
    assert runtime.phone is None and runtime.phone_code_hash is None
    assert store.setting('telegram') is None


async def test_worker_channel_failures_do_not_block_other_channels(store, monkeypatch):
    c1, c2 = channel(store, 'Phone'), channel(store, 'Other phone')
    store.record(rule(store, [c1, c2]), message(), {'body': 'test', 'title': 'test'})
    async def fake_delivery(http, ch, payload):
        if ch['id'] == c1:
            raise DeliveryError(60)
    monkeypatch.setattr('src.runtime.deliver', fake_delivery)
    runtime = Runtime(store)
    task = asyncio.create_task(runtime.push_loop())
    try:
        async with asyncio.timeout(5):
            while store.db.execute("SELECT COUNT(*) FROM deliveries WHERE status='sent'").fetchone()[0] == 0:
                await asyncio.sleep(0.01)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    statuses = {row['channel_id']: row['status'] for row in store.db.execute('SELECT * FROM deliveries')}
    assert statuses == {c1: 'pending', c2: 'sent'}


def test_web_admin_setup_once_and_persisted(tmp_path):
    app = create_app(tmp_path, background=False)
    with TestClient(app) as http:
        assert http.get('/').status_code == 200
        assert 'setup-form' in http.get('/').text
        assert http.get('/api/setup').json() == {'initialized': False}
        assert http.get('/api/settings').status_code == 503
        body = {'username': 'my-admin', 'password': 'a-long-password-123', 'confirm_password': 'a-long-password-123'}
        assert http.post('/api/setup', json=body, headers={'Origin': 'https://evil.example'}).status_code == 403
        assert http.post('/api/setup', json=body | {'confirm_password': 'different-password'}).status_code == 422
        result = http.post('/api/setup', json=body)
        assert result.status_code == 200
        assert 'a-long-password' not in result.text
        assert http.get('/api/setup').json() == {'initialized': True}
        assert http.post('/api/setup', json=body | {'username': 'attacker'}).status_code == 409
        assert 'login-form' in http.get('/').text
        assert http.get('/api/status').status_code == 401
        assert http.post('/api/auth/login', json={'username': body['username'], 'password': body['password']}).status_code == 200
        assert 'monitor-list' in http.get('/').text
        assert http.get('/api/settings').status_code == 200
    reopened = Store(tmp_path)
    record = reopened.setting('admin')
    assert record['username'] == 'my-admin'
    assert 'password' not in record and 'confirm_password' not in record
    assert reopened.check_password(body['password'])
    reopened.db.close()


async def test_concurrent_setup_first_write_wins(tmp_path):
    app = create_app(tmp_path, background=False)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as http:
        async def setup(name):
            return await http.post('/api/setup', json={'username': name, 'password': 'a-long-password-123', 'confirm_password': 'a-long-password-123'})
        responses = await asyncio.gather(setup('owner-one'), setup('owner-two'))
        assert sorted(r.status_code for r in responses) == [200, 409]
        assert app.state.store.setting('admin')['username'] in ('owner-one', 'owner-two')
    app.state.store.db.close()


def test_existing_admin_without_username_remains_compatible(client):
    store = client.app.state.store
    record = store.setting('admin')
    record.pop('username')
    store.set_setting('admin', record)
    assert client.get('/api/status').status_code == 401
    assert client.post('/api/auth/login', json={'username': 'admin', 'password': 'test-password-123'}).status_code == 200
    assert client.get('/api/status').status_code == 200
    assert client.post('/api/setup', json={'username': 'new-owner', 'password': 'a-long-password-123', 'confirm_password': 'a-long-password-123'}).status_code == 409


def test_admin_username_and_password_update(client):
    assert client.get('/api/admin').json() == {'username': 'admin'}
    body = {'username': 'new-owner', 'current_password': 'test-password-123',
            'password': 'new-long-password-123', 'confirm_password': 'new-long-password-123'}
    assert client.put('/api/admin', json=body | {'current_password': 'wrong'}).status_code == 403
    assert client.put('/api/admin', json=body | {'confirm_password': 'does-not-match'}).status_code == 422
    assert client.put('/api/admin', json=body).json() == {'ok': True}
    assert client.get('/api/admin').status_code == 401
    assert client.post('/api/auth/login', json={'username': 'new-owner', 'password': 'new-long-password-123'}).status_code == 200
    assert client.get('/api/admin').json() == {'username': 'new-owner'}
    assert client.app.state.store.check_password('new-long-password-123')
    assert not client.app.state.store.check_password('test-password-123')


def test_admin_rename_without_changing_password(client):
    body = {'username': 'renamed-owner', 'current_password': 'test-password-123'}
    assert client.put('/api/admin', json=body).status_code == 200
    assert client.post('/api/auth/login', json={'username': 'renamed-owner', 'password': 'test-password-123'}).status_code == 200
    assert client.get('/api/admin').json() == {'username': 'renamed-owner'}
    record = client.app.state.store.setting('admin')
    assert 'current_password' not in record and 'password' not in record
    client.app.state.store.set_password('reset-password-123')
    assert client.app.state.store.setting('admin')['username'] == 'renamed-owner'


def test_admin_update_rejects_stale_record(store):
    store.set_password('initial-password-123')
    old = store.setting('admin')
    store.set_password('changed-password-123')
    assert not store.update_admin(old, old | {'username': 'stale-owner'})
    assert store.check_password('changed-password-123')


def test_cookie_login_logout_and_expiry(client):
    client.cookies.clear()
    assert 'login-form' in client.get('/').text
    bad = client.post('/api/auth/login', json={'username': 'admin', 'password': 'wrong'})
    assert bad.status_code == 401 and 'www-authenticate' not in bad.headers
    response = client.post('/api/auth/login', json={'username': 'admin', 'password': 'test-password-123'})
    assert response.status_code == 200
    cookie = response.headers['set-cookie'].lower()
    assert 'httponly' in cookie and 'samesite=strict' in cookie and 'max-age=1296000' in cookie
    token = client.cookies.get('relay_session')
    row = client.app.state.store.db.execute('SELECT token_hash FROM admin_sessions').fetchone()
    assert row[0] != token
    assert 'monitor-list' in client.get('/').text
    assert client.post('/api/auth/logout').status_code == 200
    assert not client.cookies.get('relay_session')
    client.cookies.set('relay_session', token)
    assert client.get('/api/status').status_code == 401
    client.cookies.clear()
    assert client.post('/api/auth/login', json={'username': 'admin', 'password': 'test-password-123'}).status_code == 200
    with client.app.state.store.db:
        client.app.state.store.db.execute('UPDATE admin_sessions SET expires_at=0')
    assert client.get('/api/status').status_code == 401


def test_login_csrf_and_rate_limit(client):
    client.cookies.clear()
    credentials = {'username': 'admin', 'password': 'test-password-123'}
    assert client.post('/api/auth/login', json=credentials, headers={'Origin': 'https://evil.example'}).status_code == 403
    for _ in range(10):
        assert client.post('/api/auth/login', json=credentials | {'password': 'wrong'}).status_code == 401
    assert client.post('/api/auth/login', json=credentials).status_code == 429


def test_sessions_survive_restart_and_reset_revokes_them(store):
    store.set_password('session-password-123')
    record = store.setting('admin')
    token = store.create_session(record)
    reopened = Store(store.directory)
    assert reopened.valid_session(token)
    reopened.set_password('another-password-123')
    assert not store.valid_session(token)
    assert store.create_session(record) is None
    reopened.db.close()


def test_https_session_cookie_secure(tmp_path):
    app = create_app(tmp_path, background=False)
    app.state.store.set_password('test-password-123')
    with TestClient(app, base_url='https://testserver') as http:
        response = http.post('/api/auth/login', json={'username': 'admin', 'password': 'test-password-123'})
        assert 'secure' in response.headers['set-cookie'].lower()
        assert http.get('/api/status').status_code == 200


def test_invalid_config_types_and_large_ids_return_422(client):
    for server in (42, True, [], {'host': 'example.com'}):
        assert client.post('/api/channels', json={'name': 'bad', 'kind': 'bark', 'config': {'server': server, 'device_key': 'test-key'}}).status_code == 422
    assert client.post('/api/channels', json={'name': 'bad', 'kind': 'telegram', 'config': {'token': True, 'chat_id': 123}}).status_code == 422
    assert client.post('/api/monitors', json={'name': 'bad', 'chat_id': -(2**80), 'users': [42]}).status_code == 422
    assert client.get(f'/api/messages?before={2**80}').status_code == 422
    assert client.delete(f'/api/monitors/{2**80}').status_code == 422
    assert client.post('/api/telegram/verify', json={'code': '', 'password': ''}).status_code == 422


def test_invalid_json_channel_edit_returns_422(client):
    cid = channel(client.app.state.store)
    response = client.put(f'/api/channels/{cid}', content='{invalid', headers={'Content-Type': 'application/json'})
    assert response.status_code == 422
    assert client.app.state.store.channel(cid) is not None


def test_login_secrets_keep_leading_and_trailing_spaces():
    assert LoginVerify(code=' 12345 ', password=' secret ').password == ' secret '
    assert LoginVerify(code=' 12345 ').code == '12345'
    assert Settings(proxy_password=' proxy secret ').proxy_password == ' proxy secret '


async def test_telegram_verify_requires_authorized_session(store):
    runtime = Runtime(store)
    fake = SimpleNamespace(sign_in=AsyncMock(return_value=SimpleNamespace(phone_code_hash='sent-again')),
                           is_user_authorized=AsyncMock(return_value=False))
    runtime.get_client = AsyncMock(return_value=fake)
    runtime.phone = '+8613800000000'
    with pytest.raises(ValueError, match='登录未完成'):
        await runtime.verify('12345', '')
    assert not runtime.authorized


async def test_message_in_startup_second_is_not_dropped(store):
    rule(store, [])
    runtime = Runtime(store)
    runtime.started_at = datetime.now(timezone.utc).replace(microsecond=750000)
    event = SimpleNamespace(is_group=True, chat_id=-100123, id=88, sender_id=42,
                            date=runtime.started_at.replace(microsecond=0), raw_text='new message',
                            get_sender=AsyncMock(return_value=User(id=42, first_name='Alice')),
                            get_chat=AsyncMock(return_value=SimpleNamespace(title='Group', username=None)))
    await runtime.on_message(event)
    assert len(store.messages()) == 1


def test_deleting_channel_while_delivery_is_in_flight_stays_cancelled(store):
    cid = channel(store)
    store.record(rule(store, [cid]), message(), {'body': 'test'})
    job = store.next_job()
    store.delete('channels', cid)
    assert store.finish_job(job, False, 10, 'ReadTimeout') == 'cancelled'
    assert store.next_job() is None
    assert store.messages()[0]['deliveries'][0]['status'] == 'cancelled'


def test_deleted_monitor_is_not_recorded_from_stale_event(store):
    item = rule(store, [])
    store.delete('monitors', item['id'])
    store.record(item, message(), {'body': 'test'})
    assert store.messages() == []


async def test_concurrent_failed_logins_respect_limit(tmp_path):
    app = create_app(tmp_path, background=False)
    app.state.store.set_password('test-password-123')
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as http:
        results = await asyncio.gather(*(http.post('/api/auth/login', json={'username': 'admin', 'password': 'wrong'}) for _ in range(12)))
        statuses = [r.status_code for r in results]
        assert statuses.count(401) == 10
        assert statuses.count(429) == 2
    app.state.store.db.close()

def test_active_session_renews_server_expiry_and_browser_cookie(client):
    import time
    from src.db import SESSION_LIFETIME
    store = client.app.state.store
    with store.db:
        store.db.execute('UPDATE admin_sessions SET expires_at=?', (time.time() + 60,))
    response = client.get('/api/status')
    assert response.status_code == 200
    assert 'Max-Age=1296000' in response.headers['set-cookie']
    expires = store.db.execute('SELECT expires_at FROM admin_sessions').fetchone()[0]
    assert abs(expires - time.time() - SESSION_LIFETIME) < 5
    response = client.get('/')
    assert 'Max-Age=1296000' in response.headers['set-cookie']
    token = client.cookies.get('relay_session')
    with store.db:
        store.db.execute('UPDATE admin_sessions SET expires_at=0')
    response = client.get('/api/status')
    assert response.status_code == 401
    assert 'set-cookie' not in response.headers
    assert not store.renew_session(token)


def test_account_change_does_not_restore_invalidated_session(client):
    token = client.cookies.get('relay_session')
    response = client.put('/api/admin', json={'username':'renamed-admin','current_password':'test-password-123'})
    assert response.status_code == 200
    assert not client.app.state.store.valid_session(token)
    assert not client.app.state.store.renew_session(token)
    assert 'Max-Age=1296000' not in response.headers.get('set-cookie','')
