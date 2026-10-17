import base64
import hashlib
import hmac
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from src.models import Channel
from src.push import DeliveryError, deliver
from src.web import create_app

WEBHOOK = 'https://oapi.dingtalk.com/robot/send?access_token=test-token'


@pytest.mark.parametrize('secret', ['', 'SECtest-secret'])
async def test_dingtalk_payload_and_signature(monkeypatch, secret):
    monkeypatch.setattr('src.push.time.time', lambda: 1700000000)
    seen = []
    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={'errcode':0,'errmsg':'ok'})
    channel = Channel(name='钉钉', kind='dingtalk', config={
        'webhook':WEBHOOK+'&timestamp=old&sign=expired','secret':secret,'at_all':True}).model_dump()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        await deliver(http, channel, {'title':'监控标题','body':'Alice: 测试消息\nhttps://t.me/c/1/2','url':'https://t.me/c/1/2'})
    request = seen[0]
    assert request.url.params['access_token'] == 'test-token'
    assert json.loads(request.content) == {'msgtype':'text', 'text':{
        'content':'监控标题\n\nAlice: 测试消息\nhttps://t.me/c/1/2'}, 'at':{'isAtAll':True}}
    if secret:
        assert request.url.params['timestamp'] == '1700000000000'
        expected = base64.b64encode(hmac.new(secret.encode(), ('1700000000000\n'+secret).encode(), hashlib.sha256).digest()).decode()
        assert request.url.params['sign'] == expected
    else:
        assert 'timestamp' not in request.url.params and 'sign' not in request.url.params


@pytest.mark.parametrize('status,response', [(200,{'errcode':310000}), (200,{}), (200,[]), (502,{'errcode':0})])
async def test_dingtalk_rejects_failed_delivery(status, response):
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request:httpx.Response(status,json=response))) as http:
        with pytest.raises(DeliveryError):
            await deliver(http, {'kind':'dingtalk','config':{'webhook':WEBHOOK}}, {'title':'test','body':'message'})


@pytest.mark.parametrize('config', [
    {}, {'webhook':'http://oapi.dingtalk.com/robot/send?access_token=x'},
    {'webhook':'https://example.com/robot/send?access_token=x'},
    {'webhook':'https://oapi.dingtalk.com/robot/send'},
    {'webhook':WEBHOOK,'secret':123}, {'webhook':WEBHOOK,'at_all':'yes'},
])
def test_dingtalk_validation(config):
    with pytest.raises(ValueError):
        Channel(name='test',kind='dingtalk',config=config)


def test_dingtalk_crud_and_test_send(tmp_path, monkeypatch):
    app = create_app(tmp_path, background=False)
    app.state.store.set_password('test-password-123')
    requests = []
    original_client = httpx.AsyncClient
    def handler(request):
        requests.append(request)
        return httpx.Response(200,json={'errcode':0})
    def fake_client(**kwargs):
        assert kwargs['proxy'] == 'http://proxy:8080'
        assert kwargs['trust_env'] is False
        return original_client(transport=httpx.MockTransport(handler))
    with TestClient(app) as client:
        client.post('/api/auth/login',json={'username':'admin','password':'test-password-123'})
        client.put('/api/system-settings',json={'proxy_type':'http','proxy_host':'proxy','proxy_port':8080})
        body = {'name':'Team','kind':'dingtalk','config':{'webhook':WEBHOOK,'secret':'SECtest'},
                'use_proxy':True,'max_attempts':4}
        result = client.post('/api/channels',json=body)
        assert result.status_code == 200
        cid = result.json()['id']
        saved = client.get('/api/channels').json()[0]
        assert saved['config'] == body['config'] and saved['max_attempts'] == 4
        body['config']['secret'] = ''
        assert client.put(f'/api/channels/{cid}',json=body).status_code == 200
        assert app.state.store.channel(cid)['config']['secret'] == ''
        monkeypatch.setattr('src.web.httpx.AsyncClient',fake_client)
        assert client.post(f'/api/channels/{cid}/test').status_code == 200
        assert requests[0].url.params['access_token'] == 'test-token'
        assert 'timestamp' not in requests[0].url.params
        assert client.delete(f'/api/channels/{cid}').status_code == 200
    app.state.store.db.close()
