import httpx


class DeliveryError(Exception):
    def __init__(self, retry_after=0):
        self.retry_after = retry_after


def truncate(text, limit=4000):
    raw = text.encode('utf-16-le')
    return text if len(raw) <= limit * 2 else raw[:(limit - 1) * 2].decode('utf-16-le', errors='ignore') + '…'


async def deliver(http: httpx.AsyncClient, channel, payload):
    c = channel['config']
    if channel['kind'] == 'telegram':
        url = f"https://api.telegram.org/bot{c['token']}/sendMessage"
        text = payload['body']
        link = '\n\n' + payload['url'] if payload.get('url') else ''
        suffix = link if link and (not payload.get('custom_body') or text.endswith(link)) else ''
        if suffix and text.endswith(suffix):
            text = text[:-len(suffix)]
        text = truncate(text, 4000 - len(suffix.encode('utf-16-le')) // 2) + suffix
        body = {'chat_id': c['chat_id'], 'text': text,
                'link_preview_options': {'is_disabled': True}}
        if c.get('message_thread_id'):
            body['message_thread_id'] = int(c['message_thread_id'])
        body['disable_notification'] = bool(c.get('disable_notification', False))
    else:
        url = c['server'].rstrip('/') + '/push'
        body = dict(c.get('params', {}))
        body.update(device_key=c['device_key'], title=payload['title'], body=truncate(payload['body']))
        if payload.get('url') and 'url' not in body:
            body['url'] = payload['url']
    response = await http.post(url, json=body)
    try:
        data = response.json()
    except ValueError:
        raise DeliveryError() from None
    if not isinstance(data, dict):
        raise DeliveryError()
    ok = data.get('ok') is True if channel['kind'] == 'telegram' else data.get('code') == 200
    if not response.is_success or not ok:
        params = data.get('parameters')
        retry = params.get('retry_after', 0) if isinstance(params, dict) else 0
        raise DeliveryError(retry if isinstance(retry, (int, float)) else 0)
