from datetime import datetime, timezone
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient
import pytest
from telethon.tl.types import User

from src.db import Store
from src.models import Channel, Monitor, SystemSettings
from src.runtime import Runtime
from src.templates import render_notification
from src.web import create_app


def test_default_timezone_and_both_template_fields():
    payload = render_notification({'name': 'Test', 'title_template': '{sent_at}', 'body_template': '{sent_at}'},
                                  {'sent_at': '2026-10-17T20:30:00+00:00'})
    assert payload['title'] == payload['body'] == '2026-10-18 04:30:00'


@pytest.mark.parametrize('sent_at,expected', [
    ('2026-01-17T08:30:00+00:00','2026-01-17 03:30:00'),
    ('2026-07-17T08:30:00+00:00','2026-07-17 04:30:00'),
])
def test_daylight_saving_time(sent_at, expected):
    result = render_notification({'name': 'Test', 'body_template': '{sent_at}'}, {'sent_at': sent_at}, 'America/New_York')
    assert result['body'] == expected


def test_timezone_settings_validation_and_persistence(tmp_path):
    app = create_app(tmp_path, background=False)
    app.state.store.set_password('test-password-123')
    with TestClient(app) as http:
        assert http.get('/api/system-settings').status_code == 401
        http.post('/api/auth/login', json={'username':'admin','password':'test-password-123'})
        assert http.get('/api/system-settings').json()['timezone'] == 'Asia/Shanghai'
        for invalid in ['Mars/Unknown','', '/etc/passwd', '../UTC']:
            assert http.put('/api/system-settings', json={'timezone':invalid}).status_code == 422
        assert http.put('/api/system-settings', json={'timezone':'UTC'}).status_code == 200
        assert http.get('/api/system-settings').json()['timezone'] == 'UTC'
        result = http.post('/api/templates/preview', json={'body_template':'{sent_at}'}).json()
        assert result['body'] == '2026-10-17 04:00:00'
        assert http.get('/api/admin').status_code == 200
    store = Store(tmp_path)
    assert store.system_settings()['timezone'] == 'UTC'
    store.db.close()


async def test_runtime_uses_configured_timezone_without_changing_message_time(tmp_path):
    store = Store(tmp_path)
    store.set_setting('system', {'timezone':'America/New_York'})
    cid = store.save_channel(Channel(name='Phone',kind='bark',config={'server':'https://api.day.app','device_key':'test'}))
    store.save_monitor(Monitor(name='Watch',chat_id=-100123,users=[42],channel_ids=[cid],body_template='{sent_at}'))
    runtime = Runtime(store)
    runtime.started_at = datetime(2026,1,1,tzinfo=timezone.utc)
    sent_at = datetime(2026,7,17,8,30,tzinfo=timezone.utc)
    event = SimpleNamespace(is_group=True,chat_id=-100123,id=7,sender_id=42,date=sent_at,raw_text='hello',
                            get_sender=AsyncMock(return_value=User(id=42,first_name='Alice')),
                            get_chat=AsyncMock(return_value=SimpleNamespace(title='Group',username=None)))
    await runtime.on_message(event)
    assert store.messages()[0]['sent_at'] == '2026-07-17T08:30:00+00:00'
    queued = store.next_job()['payload']
    assert json.loads(queued)['body'] == '2026-07-17 04:30:00'
    store.set_setting('system', {'timezone':'UTC'})
    assert store.next_job()['payload'] == queued
    store.db.close()
