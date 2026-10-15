import hashlib
import hmac
import json
from pathlib import Path
import secrets
import sqlite3
import time

from .models import Settings


class Store:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.directory / 'monitor.sqlite3', check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
        PRAGMA journal_mode=WAL;
        PRAGMA foreign_keys=ON;
        PRAGMA busy_timeout=5000;
        CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS channels (
          id INTEGER PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL,
          enabled INTEGER NOT NULL, config TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS monitors (
          id INTEGER PRIMARY KEY, name TEXT NOT NULL, chat_id INTEGER NOT NULL,
          users TEXT NOT NULL, enabled INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS monitor_channels (
          monitor_id INTEGER REFERENCES monitors(id) ON DELETE CASCADE,
          channel_id INTEGER REFERENCES channels(id) ON DELETE CASCADE,
          PRIMARY KEY(monitor_id, channel_id));
        CREATE TABLE IF NOT EXISTS messages (
          id INTEGER PRIMARY KEY, monitor_id INTEGER REFERENCES monitors(id) ON DELETE SET NULL,
          monitor_name TEXT NOT NULL, chat_id INTEGER NOT NULL, message_id INTEGER NOT NULL,
          sender_id INTEGER, sender_name TEXT, username TEXT, chat_title TEXT,
          text TEXT NOT NULL, url TEXT NOT NULL, sent_at TEXT NOT NULL, received_at REAL NOT NULL,
          UNIQUE(monitor_id, chat_id, message_id));
        CREATE INDEX IF NOT EXISTS messages_monitor ON messages(monitor_id, id DESC);
        CREATE TABLE IF NOT EXISTS deliveries (
          id INTEGER PRIMARY KEY, message_id INTEGER REFERENCES messages(id) ON DELETE CASCADE,
          channel_id INTEGER REFERENCES channels(id) ON DELETE SET NULL,
          channel_name TEXT NOT NULL, payload TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
          attempts INTEGER NOT NULL DEFAULT 0, due REAL NOT NULL DEFAULT 0, error TEXT,
          UNIQUE(message_id, channel_id));
        CREATE INDEX IF NOT EXISTS deliveries_pending ON deliveries(status, due);
        CREATE TABLE IF NOT EXISTS admin_sessions (
          token_hash TEXT PRIMARY KEY, expires_at REAL NOT NULL, account_version TEXT NOT NULL);
        PRAGMA user_version=1;
        ''')

    def setting(self, key, default=None):
        row = self.db.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set_setting(self, key, value):
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)', (key, json.dumps(value)))

    def settings(self):
        return Settings(**self.setting('telegram', {})).model_dump(exclude={'clear_http_proxy'})

    @staticmethod
    def password_record(password, username):
        if not 12 <= len(password) <= 256:
            raise ValueError('密码必须为 12–256 位')
        salt = secrets.token_hex(16)
        digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()
        return {'username': username, 'salt': salt, 'digest': digest}

    def set_password(self, password, username=None):
        username = username or self.setting('admin', {}).get('username', 'admin')
        self.set_setting('admin', self.password_record(password, username))
        with self.db:
            self.db.execute('DELETE FROM admin_sessions')

    def initialize_admin(self, record):
        # Atomic first-write wins, including across separate DB connections.
        with self.db:
            return bool(self.db.execute('INSERT OR IGNORE INTO settings(key,value) VALUES (?,?)',
                                       ('admin', json.dumps(record))).rowcount)

    def update_admin(self, old_record, new_record):
        with self.db:
            # Avoid overwriting a concurrent password reset or account change.
            row = self.db.execute('SELECT value FROM settings WHERE key=?', ('admin',)).fetchone()
            if not row or json.loads(row[0]) != old_record:
                return False
            changed = bool(self.db.execute('UPDATE settings SET value=? WHERE key=? AND value=?',
                                          (json.dumps(new_record), 'admin', row[0])).rowcount)
            if changed:
                self.db.execute('DELETE FROM admin_sessions')
            return changed

    @staticmethod
    def account_version(record):
        return hashlib.sha256(json.dumps(record, sort_keys=True).encode()).hexdigest()

    def create_session(self, record, lifetime=43200):
        if self.setting('admin') != record:
            return None
        token = secrets.token_urlsafe(32)
        version = self.account_version(record)
        with self.db:
            self.db.execute('DELETE FROM admin_sessions WHERE expires_at<=? OR account_version!=?', (time.time(), version))
            self.db.execute('INSERT INTO admin_sessions VALUES (?,?,?)',
                            (hashlib.sha256(token.encode()).hexdigest(), time.time() + lifetime, version))
        return token

    def valid_session(self, token):
        if not token or len(token) > 128:
            return False
        row = self.db.execute('SELECT * FROM admin_sessions WHERE token_hash=?',
                              (hashlib.sha256(token.encode()).hexdigest(),)).fetchone()
        record = self.setting('admin')
        return bool(row and record and row['expires_at'] > time.time() and
                    hmac.compare_digest(row['account_version'], self.account_version(record)))

    def delete_session(self, token):
        if token:
            with self.db:
                self.db.execute('DELETE FROM admin_sessions WHERE token_hash=?', (hashlib.sha256(token.encode()).hexdigest(),))

    def check_password(self, password, value=None):
        value = self.setting('admin') if value is None else value
        if not value:
            return False
        digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(value['salt']), n=16384, r=8, p=1).hex()
        return hmac.compare_digest(digest, value['digest'])

    def channels(self):
        return [dict(r) | {'config': json.loads(r['config']), 'enabled': bool(r['enabled'])}
                for r in self.db.execute('SELECT * FROM channels ORDER BY id')]

    def channel(self, channel_id):
        return next((c for c in self.channels() if c['id'] == channel_id), None)

    def save_channel(self, model, channel_id=None):
        with self.db:
            values = (model.name, model.kind, model.enabled, json.dumps(model.config))
            if channel_id is None:
                channel_id = self.db.execute('INSERT INTO channels(name,kind,enabled,config) VALUES (?,?,?,?)', values).lastrowid
            else:
                self.db.execute('UPDATE channels SET name=?,kind=?,enabled=?,config=? WHERE id=?', (*values, channel_id))
        return channel_id

    def monitors(self):
        result = []
        for row in self.db.execute('SELECT * FROM monitors ORDER BY id'):
            item = dict(row)
            item['users'] = json.loads(item['users'])
            item['enabled'] = bool(item['enabled'])
            item['channel_ids'] = [r[0] for r in self.db.execute('SELECT channel_id FROM monitor_channels WHERE monitor_id=?', (item['id'],))]
            item['message_count'] = self.db.execute('SELECT COUNT(*) FROM messages WHERE monitor_id=?', (item['id'],)).fetchone()[0]
            result.append(item)
        return result

    def rules_for_chat(self, chat_id):
        result = []
        for row in self.db.execute('SELECT * FROM monitors WHERE enabled=1 AND chat_id=?', (chat_id,)):
            item = dict(row)
            item['users'] = json.loads(item['users'])
            item['channel_ids'] = [r[0] for r in self.db.execute('SELECT channel_id FROM monitor_channels WHERE monitor_id=?', (item['id'],))]
            result.append(item)
        return result

    def save_monitor(self, model, monitor_id=None):
        channel_ids = set(model.channel_ids)
        if not channel_ids <= {c['id'] for c in self.channels()}:
            raise ValueError('选择的渠道不存在')
        with self.db:
            values = (model.name, model.chat_id, json.dumps(model.users), model.enabled)
            if monitor_id is None:
                monitor_id = self.db.execute('INSERT INTO monitors(name,chat_id,users,enabled) VALUES (?,?,?,?)', values).lastrowid
            else:
                self.db.execute('UPDATE monitors SET name=?,chat_id=?,users=?,enabled=? WHERE id=?', (*values, monitor_id))
                self.db.execute('DELETE FROM monitor_channels WHERE monitor_id=?', (monitor_id,))
            self.db.executemany('INSERT INTO monitor_channels VALUES (?,?)', [(monitor_id, c) for c in channel_ids])
        return monitor_id

    def delete(self, kind, item_id):
        if kind not in ('monitors', 'channels'):
            raise ValueError('Unknown table')
        with self.db:
            if kind == 'channels':
                self.db.execute("UPDATE deliveries SET status='cancelled' WHERE channel_id=? AND status='pending'", (item_id,))
            self.db.execute(f'DELETE FROM {kind} WHERE id=?', (item_id,))

    def record(self, rule, message, payload):
        with self.db:
            if not self.db.execute('SELECT 1 FROM monitors WHERE id=? AND enabled=1 AND chat_id=?',
                                   (rule['id'], message['chat_id'])).fetchone():
                return
            cursor = self.db.execute('''INSERT OR IGNORE INTO messages
                (monitor_id,monitor_name,chat_id,message_id,sender_id,sender_name,username,chat_title,text,url,sent_at,received_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)''',
                (rule['id'], rule['name'], message['chat_id'], message['message_id'], message['sender_id'],
                 message['sender_name'], message['username'], message['chat_title'], message['text'], message['url'], message['sent_at'], time.time()))
            if not cursor.rowcount:
                return
            message_id = cursor.lastrowid
            for channel_id in rule['channel_ids']:
                channel = self.channel(channel_id)
                if channel and channel['enabled']:
                    self.db.execute('INSERT INTO deliveries(message_id,channel_id,channel_name,payload) VALUES (?,?,?,?)',
                                    (message_id, channel_id, channel['name'], json.dumps(payload, ensure_ascii=False)))

    def messages(self, monitor_id=None, before=None, limit=50):
        conditions, params = ['1=1'], []
        if monitor_id is not None:
            conditions.append('monitor_id=?')
            params.append(monitor_id)
        if before is not None:
            conditions.append('id<?')
            params.append(before)
        rows = self.db.execute(f"SELECT * FROM messages WHERE {' AND '.join(conditions)} ORDER BY id DESC LIMIT ?", (*params, limit)).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item['deliveries'] = [dict(r) for r in self.db.execute('SELECT id,channel_id,channel_name,status,attempts,error FROM deliveries WHERE message_id=?', (row['id'],))]
            result.append(item)
        return result

    def next_job(self):
        row = self.db.execute('''SELECT d.* FROM deliveries d JOIN channels c ON c.id=d.channel_id
            WHERE d.status='pending' AND d.due<=? AND c.enabled=1 ORDER BY d.due,d.id LIMIT 1''', (time.time(),)).fetchone()
        return dict(row) if row else None

    def finish_job(self, job, success, max_attempts, error=None, retry_after=0):
        attempts = job['attempts'] + 1
        status = 'sent' if success else ('failed' if attempts >= max_attempts else 'pending')
        with self.db:
            self.db.execute("UPDATE deliveries SET status=?,attempts=?,due=?,error=? WHERE id=? AND status='pending' AND channel_id IS NOT NULL",
                            (status, attempts, time.time() + max(min(2 ** attempts, 3600), retry_after or 0), error, job['id']))
        row = self.db.execute('SELECT status FROM deliveries WHERE id=?', (job['id'],)).fetchone()
        return row[0] if row else 'cancelled'
