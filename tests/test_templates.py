import json
import sqlite3
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient
import httpx
import pytest
from telethon.tl.types import User

from src.db import Store
from src.models import Channel, Monitor
from src.push import deliver
from src.runtime import Runtime
from src.templates import render_notification
from src.web import create_app


def test_render_default_and_custom_without_interpreting_message():
    message = {'chat_title': 'Group', 'chat_id': -100123, 'sender_name': 'Alice',
               'sender_id': 42, 'username': None, 'text': '{not_a_variable} 😀',
               'sent_at': '2026-10-17T12:00:00+08:00', 'message_id': 7, 'url': 'https://t.me/c/123/7'}
    default = render_notification({'name': 'Watch'}, message)
    assert default['title'] == 'Watch · Group'
    assert default['body'].endswith('\n\nhttps://t.me/c/123/7')
    custom = render_notification({'name': 'Watch', 'title_template': '{chat_title}',
                                  'body_template': '{{literal}} {sender_name}: {text}'}, message)
    assert custom['body'] == '{literal} Alice: {not_a_variable} 😀'
    assert custom['title'] == 'Group'
    assert custom['custom_body']


@pytest.mark.parametrize('template', ['{unknown}', '{text.__class__}', '{text[0]}', '{text!r}', '{text:>10}', '{', '{}'])
def test_unsafe_or_invalid_templates_rejected(template):
    with pytest.raises(ValueError):
        Monitor(name='Watch', chat_id=-100123, users=[42], body_template=template)


def test_existing_database_migrates_without_losing_monitors(tmp_path):
    db = sqlite3.connect(tmp_path / 'monitor.sqlite3')
    db.execute('CREATE TABLE monitors (id INTEGER PRIMARY KEY, name TEXT NOT NULL, chat_id INTEGER NOT NULL, users TEXT NOT NULL, enabled INTEGER NOT NULL)')
    db.execute("INSERT INTO monitors VALUES (1,'Existing',-100123,'[42]',1)")
    db.commit()
    db.close()
    for _ in range(2):
        store = Store(tmp_path)
        item = store.monitors()[0]
        assert item['name'] == 'Existing'
        assert item['title_template'] == item['body_template'] == ''
        store.db.close()


def test_template_api_preview_and_persistence(tmp_path):
    app = create_app(tmp_path, background=False)
    app.state.store.set_password('test-password-123')
    with TestClient(app) as http:
        http.post('/api/auth/login', json={'username': 'admin', 'password': 'test-password-123'})
        body = {'name': 'Watch', 'chat_id': -100123, 'users': [42],
                'title_template': '{monitor_name}', 'body_template': '  {sender_name}\n{text}  '}
        response = http.post('/api/monitors', json=body)
        assert response.status_code == 200
        item_id = response.json()['id']
        result = http.get('/api/monitors').json()[0]
        assert result['body_template'] == body['body_template']
        assert app.state.store.rules_for_chat(-100123)[0]['title_template'] == '{monitor_name}'
        preview = http.post('/api/templates/preview', json={'title_template': '{chat_title}', 'body_template': '{sender_name}: {text}'})
        assert preview.status_code == 200
        assert preview.json()['body'] == 'Alice: 这是一条示例消息。'
        assert http.post('/api/templates/preview', json={'body_template': '{invalid}'}).status_code == 422
        assert http.put(f'/api/monitors/{item_id}', json=body | {'body_template': '{url}'}).status_code == 200
    reopened = Store(tmp_path)
    assert reopened.monitors()[0]['body_template'] == '{url}'
    reopened.db.close()


async def test_runtime_queues_rendered_payload_and_keeps_original(tmp_path):
    store = Store(tmp_path)
    channel_id = store.save_channel(Channel(name='Phone', kind='bark', config={'server': 'https://api.day.app', 'device_key': 'key'}))
    monitor = Monitor(name='Watch', chat_id=-100123, users=[42], channel_ids=[channel_id],
                      title_template='[{monitor_name}]', body_template='{sender_name}: {text}')
    item_id = store.save_monitor(monitor)
    runtime = Runtime(store)
    event = SimpleNamespace(is_group=True, chat_id=-100123, id=7, sender_id=42,
                            date=datetime.now(timezone.utc), raw_text='Original {text}',
                            get_sender=AsyncMock(return_value=User(id=42, first_name='Alice')),
                            get_chat=AsyncMock(return_value=SimpleNamespace(title='Group', username=None)))
    await runtime.on_message(event)
    job = store.next_job()
    original_payload = job['payload']
    assert json.loads(original_payload)['body'] == 'Alice: Original {text}'
    assert store.messages()[0]['text'] == 'Original {text}'
    store.save_monitor(monitor.model_copy(update={'body_template': 'changed'}), item_id)
    assert store.next_job()['payload'] == original_payload
    store.db.close()


async def test_bot_custom_body_does_not_append_link():
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={'ok': True})
    payload = {'title': 'Title', 'body': 'Only this text', 'url': 'https://t.me/a/7', 'custom_body': True}
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        await deliver(http, {'kind': 'telegram', 'config': {'token': 'token', 'chat_id': '42'}}, payload)
    assert json.loads(requests[0].content)['text'] == 'Only this text'


def test_preset_crud_live_reference_and_custom_copy(tmp_path):
    app = create_app(tmp_path, background=False)
    app.state.store.set_password('test-password-123')
    with TestClient(app) as http:
        http.post('/api/auth/login', json={'username': 'admin', 'password': 'test-password-123'})
        assert len(http.get('/api/templates').json()) == 1
        preset = {'name': 'Project updates', 'title_template': '{monitor_name}', 'body_template': 'Initial: {text}'}
        tid = http.post('/api/templates', json=preset).json()['id']
        base = {'name': 'Linked', 'chat_id': -100123, 'users': [42]}
        linked_id = http.post('/api/monitors', json=base | {'template_id': tid}).json()['id']
        copy_id = http.post('/api/monitors', json=base | {'name': 'Copy', 'title_template': preset['title_template'], 'body_template': preset['body_template']}).json()['id']
        assert http.put(f'/api/templates/{tid}', json=preset | {'body_template': 'Updated: {text}'}).status_code == 200
        items = {m['id']: m for m in http.get('/api/monitors').json()}
        assert items[linked_id]['template_id'] == tid
        assert items[linked_id]['body_template'] == 'Updated: {text}'
        assert items[copy_id]['template_id'] is None
        assert items[copy_id]['body_template'] == 'Initial: {text}'
        rules = {r['id']: r for r in app.state.store.rules_for_chat(-100123)}
        assert render_notification(rules[linked_id], {'text': 'hello'})['body'] == 'Updated: hello'
        assert http.delete(f'/api/templates/{tid}').status_code == 200
        remaining = {m['id']: m for m in http.get('/api/monitors').json()}
        assert remaining[linked_id]['template_id'] is None
        assert remaining[linked_id]['body_template'] == 'Updated: {text}'
        assert http.post('/api/monitors', json=base | {'template_id': tid}).status_code == 422
        assert app.state.store.db.execute('PRAGMA foreign_key_check').fetchall() == []
    reopened = Store(tmp_path)
    assert len(reopened.notification_templates()) == 1
    assert reopened.monitors()[0]['body_template'] == 'Updated: {text}'
    reopened.db.close()


def test_presets_validation_and_deleting_all_does_not_reseed(tmp_path):
    from src.models import NotificationTemplate
    with pytest.raises(ValueError):
        NotificationTemplate(name='Invalid', body_template='{text.__class__}')
    store = Store(tmp_path)
    for preset in store.notification_templates():
        store.delete_notification_template(preset['id'])
    store.db.close()
    store = Store(tmp_path)
    assert store.notification_templates() == []
    store.db.close()


def test_preset_edit_keeps_already_queued_payload(tmp_path):
    from src.models import NotificationTemplate
    store = Store(tmp_path)
    tid = store.save_notification_template(NotificationTemplate(name='Shared', body_template='old {text}'))
    cid = store.save_channel(Channel(name='Phone', kind='bark', config={'server': 'https://api.day.app', 'device_key': 'key'}))
    mid = store.save_monitor(Monitor(name='Watch', chat_id=-100123, users=[42], channel_ids=[cid], template_id=tid))
    msg = {'chat_id': -100123, 'message_id': 1, 'sender_id': 42, 'sender_name': 'Alice', 'username': 'alice',
           'chat_title': 'Group', 'text': 'hello', 'url': '', 'sent_at': '2026-10-17T12:00:00+08:00'}
    rule = store.rules_for_chat(-100123)[0]
    store.record(rule, msg, render_notification(rule, msg))
    store.save_notification_template(NotificationTemplate(name='Shared', body_template='new {text}'), tid)
    assert json.loads(store.next_job()['payload'])['body'] == 'old hello'
    assert render_notification(store.rules_for_chat(-100123)[0], msg)['body'] == 'new hello'
    store.save_monitor(Monitor(name='Watch', chat_id=-100123, users=[42], body_template='custom {text}'), mid)
    assert store.monitors()[0]['template_id'] is None
    assert store.rules_for_chat(-100123)[0]['body_template'] == 'custom {text}'
    store.db.close()


def test_single_default_preset_and_legacy_migration_preserves_custom_content(tmp_path):
    from src.templates import DEFAULT_TITLE, DEFAULT_PRESET_TITLE, DEFAULT_PRESET_BODY
    store = Store(tmp_path)
    presets = store.notification_templates()
    assert len(presets) == 1
    assert presets[0]['name'] == '默认消息'
    assert presets[0]['title_template'] == DEFAULT_PRESET_TITLE
    assert presets[0]['body_template'] == DEFAULT_PRESET_BODY
    with store.db:
        store.db.execute('UPDATE notification_templates SET name=?,title_template=?,body_template=? WHERE id=?',
                         ('标准通知', DEFAULT_TITLE, '', presets[0]['id']))
        store.db.execute("DELETE FROM settings WHERE key='default_preset_v2'")
    store.db.close()
    store = Store(tmp_path)
    assert store.notification_template(presets[0]['id'])['body_template'] == DEFAULT_PRESET_BODY
    assert store.notification_template(presets[0]['id'])['name'] == '默认消息'
    with store.db:
        store.db.execute('UPDATE notification_templates SET body_template=? WHERE id=?', ('My custom {text}', presets[0]['id']))
    store.db.close()
    store = Store(tmp_path)
    assert store.notification_template(presets[0]['id'])['body_template'] == 'My custom {text}'
    store.db.close()
