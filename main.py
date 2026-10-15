import argparse
import getpass
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import urllib.request

import uvicorn

from src.db import Store
from src.web import create_app


def main():
    parser = argparse.ArgumentParser(description='观言 · WordWatch — Telegram 消息监控服务')
    parser.add_argument('command', choices=['serve', 'password', 'health'], nargs='?', default='serve')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8080)
    parser.add_argument('--username', help='重置管理员密码时可同时更改用户名')
    args = parser.parse_args()
    directory = Path(os.getenv('DATA_DIR', 'data'))
    directory.mkdir(parents=True, exist_ok=True)
    if args.command == 'health':
        try:
            with urllib.request.urlopen(f'http://127.0.0.1:{args.port}/healthz', timeout=3) as response:
                raise SystemExit(0 if response.status == 200 else 1)
        except OSError:
            raise SystemExit(1)
    if args.command == 'password':
        password = getpass.getpass('管理员密码（至少 12 位）: ')
        if password != getpass.getpass('再次输入: '):
            raise SystemExit('两次密码不一致')
        store = Store(directory)
        try:
            if args.username:
                from src.models import AdminSetup
                AdminSetup(username=args.username, password=password, confirm_password=password)
            store.set_password(password, args.username)
            username = store.setting('admin').get('username', 'admin')
        except ValueError as exc:
            raise SystemExit(str(exc)) from None
        finally:
            store.db.close()
        print(f'已保存管理员密码。用户名：{username}')
        return
    file_handler = RotatingFileHandler(directory / 'monitor.log', maxBytes=5 * 1024 * 1024, backupCount=3, encoding='utf-8')
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s %(message)s', handlers=[logging.StreamHandler(), file_handler])
    logging.getLogger('httpx').setLevel(logging.WARNING)
    app = create_app(directory)
    uvicorn.run(app, host=args.host, port=args.port, access_log=False)


if __name__ == '__main__':
    main()
