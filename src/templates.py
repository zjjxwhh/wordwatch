"""Restricted plain-text notification templates; no expressions or attribute access."""
from string import Formatter
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

FIELDS = frozenset({'monitor_name', 'chat_title', 'chat_id', 'sender_name', 'sender_id',
                    'username', 'text', 'sent_at', 'message_id', 'url'})
DEFAULT_TITLE = '{monitor_name} · {chat_title}'
DEFAULT_BODY = '{monitor_name} · {chat_title}\n{sender_name} (@{username}) [ID: {sender_id}]\n{sent_at}\n\n{text}'
DEFAULT_BODY_TEMPLATE = DEFAULT_BODY + '\n\n{url}'
DEFAULT_PRESET_TITLE = '{monitor_name}'
DEFAULT_PRESET_BODY = '{sender_name}: {text}\n\n📌 {monitor_name}\n💬 {chat_title}\n👤 {sender_name} (@{username})\n🕒 {sent_at}\n🔗 {url}'


def validate_template(value):
    for _, field, spec, conversion in Formatter().parse(value):
        if field is not None and (field not in FIELDS or spec or conversion):
            raise ValueError('模板包含不支持的变量或格式；仅支持 {变量名}，字面花括号请写 {{ 和 }}')
    return value


def render_notification(rule, message, timezone_name='Asia/Shanghai'):
    values = {key: str(message.get(key) or '') for key in FIELDS}
    if values['sent_at']:
        sent_at = datetime.fromisoformat(values['sent_at'])
        if sent_at.tzinfo is None:
            sent_at = sent_at.replace(tzinfo=timezone.utc)
        values['sent_at'] = sent_at.astimezone(ZoneInfo(timezone_name)).strftime('%Y-%m-%d %H:%M:%S')
    values['monitor_name'] = rule['name']
    values['username'] = message.get('username') or '-'
    custom_body = bool(rule.get('body_template'))
    title = (rule.get('title_template') or DEFAULT_TITLE).format_map(values)
    body = (rule.get('body_template') or DEFAULT_BODY).format_map(values)
    if not custom_body and values['url']:
        body += '\n\n' + values['url']
    return {'title': title, 'body': body if body.strip() else '[空通知内容]', 'url': values['url'], 'custom_body': custom_body}
