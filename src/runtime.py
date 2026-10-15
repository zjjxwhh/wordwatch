import asyncio
from datetime import datetime, timezone
import json
import logging
import time

import httpx
from telethon import TelegramClient, events, utils
from telethon.errors import SessionPasswordNeededError

from .push import deliver

log = logging.getLogger('monitor')


def matches(users, sender_id, username):
    return any((type(u) is int and u == sender_id) or
               (isinstance(u, str) and username and u[1:].casefold() == username.casefold()) for u in users)


class Runtime:
    def __init__(self, store):
        self.store = store
        self.client = None
        self.lock = asyncio.Lock()
        self.started_at = datetime.now(timezone.utc)
        self.authorized = False
        self.state = '等待配置'
        self.error = None
        self.phone = None
        self.phone_code_hash = None
        self.tasks = []
        self.worker_heartbeat = 0

    async def start(self):
        self.tasks = [asyncio.create_task(self.connection_loop()), asyncio.create_task(self.push_loop())]

    async def stop(self):
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        if self.client:
            await self.client.disconnect()

    async def reset(self):
        async with self.lock:
            if self.client:
                await self.client.disconnect()
            self.client = None
            self.authorized = False
            self.phone = self.phone_code_hash = None
            self.state = '等待连接'

    async def get_client(self):
        # Callers serialize client creation/reset/login with self.lock.
        s = self.store.settings()
        if not s['api_id'] or not s['api_hash']:
            self.state = '等待配置'
            return None
        if self.client is None:
            proxy = None
            if s['proxy_host']:
                proxy = {'proxy_type': 'socks5', 'addr': s['proxy_host'], 'port': s['proxy_port'],
                         'username': s['proxy_username'] or None, 'password': s['proxy_password'] or None}
            self.client = TelegramClient(str(self.store.directory / 'telegram'), s['api_id'], s['api_hash'], proxy=proxy,
                                         connection_retries=2, retry_delay=2, timeout=10)
            self.client.add_event_handler(self.on_message, events.NewMessage())
        if not self.client.is_connected():
            await self.client.connect()
        return self.client

    async def connection_loop(self):
        while True:
            try:
                async with self.lock:
                    client = await self.get_client()
                    if client:
                        self.authorized = await client.is_user_authorized()
                        self.state = '监听中' if self.authorized else '等待登录'
                        self.error = None
            except Exception as exc:
                self.authorized = False
                self.state = '连接失败，稍后重试'
                self.error = type(exc).__name__
                log.warning('Telegram connection failed: %s', self.error)
            await asyncio.sleep(10)

    async def request_code(self, phone):
        async with self.lock:
            client = await self.get_client()
            if not client:
                raise ValueError('请先保存 Telegram API 配置')
            if await client.is_user_authorized():
                raise ValueError('当前账号已经登录')
            result = await client.send_code_request(phone)
            self.phone, self.phone_code_hash = phone, result.phone_code_hash
            return {'status': 'code_sent'}

    async def verify(self, code, password):
        async with self.lock:
            client = await self.get_client()
            if not client or not self.phone:
                raise ValueError('请先获取验证码')
            try:
                if password:
                    await client.sign_in(password=password)
                else:
                    await client.sign_in(self.phone, code=code, phone_code_hash=self.phone_code_hash)
            except SessionPasswordNeededError:
                return {'status': 'password_required'}
            if not await client.is_user_authorized():
                raise ValueError('登录未完成，请检查验证码或两步验证密码')
            self.authorized = True
            self.state = '监听中'
            self.error = None
            self.phone = self.phone_code_hash = None
            return {'status': 'authorized'}

    async def on_message(self, event):
        # Telegram timestamps have second precision, unlike datetime.now().
        if not event.is_group or event.date < self.started_at.replace(microsecond=0):
            return
        rules = self.store.rules_for_chat(event.chat_id)
        if not rules:
            return
        try:
            sender = await event.get_sender()
            username = getattr(sender, 'username', None)
            matched = [r for r in rules if matches(r['users'], event.sender_id, username)]
            if not matched:
                return
            chat = await event.get_chat()
            title = getattr(chat, 'title', str(event.chat_id))
            name = utils.get_display_name(sender) if sender else str(event.sender_id)
            url = ''
            if getattr(chat, 'username', None):
                url = f'https://t.me/{chat.username}/{event.id}'
            elif str(event.chat_id).startswith('-100'):
                url = f'https://t.me/c/{str(event.chat_id)[4:]}/{event.id}'
            text = event.raw_text or '[媒体或非文本消息]'
            message = {'chat_id': event.chat_id, 'message_id': event.id, 'sender_id': event.sender_id,
                       'sender_name': name, 'username': username, 'chat_title': title, 'text': text,
                       'url': url, 'sent_at': event.date.isoformat()}
            for rule in matched:
                body = f"{rule['name']} · {title}\n{name} (@{username or '-'}) [ID: {event.sender_id}]\n{event.date.isoformat()}\n\n{text}"
                if url:
                    body += f'\n\n{url}'
                self.store.record(rule, message, {'title': f"{rule['name']} · {title}", 'body': body, 'url': url})
        except Exception as exc:
            log.error('Message handling failed: chat=%s message=%s error=%s', event.chat_id, event.id, type(exc).__name__)

    async def push_loop(self):
        while True:
            self.worker_heartbeat = time.time()
            job = self.store.next_job()
            if job is None:
                await asyncio.sleep(0.5)
                continue
            channel = self.store.channel(job['channel_id'])
            settings = self.store.settings()
            try:
                async with httpx.AsyncClient(timeout=20, proxy=settings['http_proxy'] or None, trust_env=False) as http:
                    await deliver(http, channel, json.loads(job['payload']))
            except Exception as exc:
                status = self.store.finish_job(job, False, settings['max_attempts'], type(exc).__name__, getattr(exc, 'retry_after', 0))
                log.warning('Delivery %s: id=%s channel=%s error=%s', status, job['id'], job['channel_id'], type(exc).__name__)
            else:
                self.store.finish_job(job, True, settings['max_attempts'])
                log.info('Delivery sent: id=%s channel=%s', job['id'], job['channel_id'])

    def status(self):
        return {'state': self.state, 'error': self.error, 'authorized': self.authorized,
                'connected': bool(self.client and self.client.is_connected()),
                'started_at': self.started_at.isoformat(),
                'worker_ok': bool(self.worker_heartbeat and time.time() - self.worker_heartbeat < 90)}
