from unittest.mock import AsyncMock, Mock

from fastapi.testclient import TestClient
import pytest
from python_socks import ProxyType
from telethon.network.connection import ConnectionTcpFull

from src.db import Store
from src.models import Settings, SystemSettings
from src.runtime import Runtime
from src.web import create_app


@pytest.mark.parametrize('proxy_type',['socks5','http'])
async def test_telegram_proxy_type_passed_to_client(tmp_path,monkeypatch,proxy_type):
    store=Store(tmp_path)
    store.set_setting('telegram',{'api_id':12345,'api_hash':'private-hash','use_proxy':True})
    store.set_setting('system',{'proxy_type':proxy_type,
                                  'proxy_host':'localhost','proxy_port':7890,'proxy_username':'user','proxy_password':'pass'})
    fake=Mock()
    fake.is_connected.return_value=True
    constructor=Mock(return_value=fake)
    monkeypatch.setattr('src.runtime.TelegramClient',constructor)
    runtime=Runtime(store)
    await runtime.get_client()
    proxy=constructor.call_args.kwargs['proxy']
    assert proxy['proxy_type']==proxy_type
    assert proxy['username']=='user' and proxy['password']=='pass'
    parsed=ConnectionTcpFull._parse_proxy(**proxy)
    assert parsed[0]==(ProxyType.HTTP if proxy_type=='http' else ProxyType.SOCKS5)
    assert SystemSettings().proxy_type=='socks5'
    store.db.close()


def test_connection_settings_mask_hash_and_show_verified_phone(tmp_path):
    app=create_app(tmp_path,background=False)
    app.state.store.set_password('test-password-123')
    with TestClient(app) as http:
        http.post('/api/auth/login',json={'username':'admin','password':'test-password-123'})
        assert http.get('/api/settings').json()['api_hash_configured'] is False
        assert http.get('/api/settings').json()['account_phone']==''
        body={'api_id':12345,'api_hash':'private-hash','use_proxy':True}
        assert http.put('/api/settings',json=body).status_code==200
        app.state.store.set_setting('telegram_account',{'phone':'+8613800000000'})
        app.state.runtime.authorized=True
        data=http.get('/api/settings').json()
        assert data['api_hash']=='private-hash' and data['api_hash_configured']
        assert data['account_phone']=='+8613800000000' and data['authorized']
        assert data['use_proxy'] is True
        assert 'private-hash' not in http.get('/api/status').text
        assert http.put('/api/system-settings',json={'proxy_type':'https'}).status_code==422
    reopened=Store(tmp_path)
    assert reopened.setting('telegram_account')['phone']=='+8613800000000'
    reopened.db.close()


async def test_phone_persisted_only_after_verified_login(tmp_path):
    store=Store(tmp_path)
    runtime=Runtime(store)
    fake=Mock()
    fake.is_user_authorized=AsyncMock(side_effect=[False,True])
    fake.send_code_request=AsyncMock(return_value=Mock(phone_code_hash='hash'))
    fake.sign_in=AsyncMock()
    runtime.get_client=AsyncMock(return_value=fake)
    await runtime.request_code('+8613800000000')
    assert store.setting('telegram_account') is None
    await runtime.verify('12345','')
    assert store.setting('telegram_account')=={'phone':'+8613800000000'}
    assert runtime.status()['account_phone']=='+8613800000000'
    store.db.close()


@pytest.mark.parametrize('use_proxy,expected', [
    (True, 'http://a%40b:p%3A%2F%23@[::1]:7890'), (False, None),
])
def test_channel_proxy_selection(tmp_path, use_proxy, expected):
    store = Store(tmp_path)
    store.set_setting('system', {'proxy_type':'http','proxy_host':'::1','proxy_port':7890,
                                 'proxy_username':'a@b','proxy_password':'p:/#'})
    assert store.notification_proxy({'use_proxy':use_proxy}) == expected
    store.db.close()


@pytest.mark.parametrize('mode', ['direct','shared','custom'])
def test_legacy_proxy_and_channel_migration(tmp_path, mode):
    import sqlite3
    import json
    db = sqlite3.connect(tmp_path / 'monitor.sqlite3')
    db.executescript("CREATE TABLE settings (key TEXT PRIMARY KEY,value TEXT NOT NULL);"
                     "CREATE TABLE channels(id INTEGER PRIMARY KEY,name TEXT,kind TEXT,enabled INTEGER,config TEXT);")
    old = {'api_id':12345,'api_hash':'private-hash','proxy_host':'old.proxy','proxy_type':'http',
           'proxy_port':7890,'max_attempts':7,'notification_proxy_mode':mode,'http_proxy':'http://other.proxy:8080'}
    db.execute('INSERT INTO settings VALUES (?,?)', ('telegram',json.dumps(old)))
    db.execute("INSERT INTO channels VALUES (1,'Bot','telegram',1,?)", (json.dumps({'token':'test','chat_id':'42'}),))
    db.commit()
    db.close()
    for _ in range(2):
        store = Store(tmp_path)
        assert store.settings()['use_proxy'] is True
        assert store.settings()['api_hash'] == 'private-hash'
        assert store.system_settings()['proxy_host'] == 'old.proxy'
        assert store.channel(1)['use_proxy'] == (mode != 'direct')
        assert store.channel(1)['max_attempts'] == 7
        store.db.close()


async def test_telegram_direct_ignores_system_proxy(tmp_path, monkeypatch):
    store = Store(tmp_path)
    store.set_setting('telegram', {'api_id':12345,'api_hash':'hash','use_proxy':False})
    store.set_setting('system', {'proxy_host':'proxy'})
    fake = Mock()
    fake.is_connected.return_value = True
    constructor = Mock(return_value=fake)
    monkeypatch.setattr('src.runtime.TelegramClient', constructor)
    await Runtime(store).get_client()
    assert constructor.call_args.kwargs['proxy'] is None
    store.db.close()
