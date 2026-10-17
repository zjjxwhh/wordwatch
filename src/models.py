from typing import Annotated, Literal

from .templates import validate_template
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

SQLITE_MAX_ID = 2**63 - 1
PositiveID = Annotated[int, Field(gt=0, le=SQLITE_MAX_ID)]
SecretText = Annotated[str, StringConstraints(strip_whitespace=False)]


class Input(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)


class Channel(Input):
    name: str = Field(min_length=1, max_length=100)
    kind: Literal['telegram', 'bark']
    enabled: bool = True
    config: dict

    @model_validator(mode='after')
    def validate_config(self):
        c = self.config
        required = ('token', 'chat_id') if self.kind == 'telegram' else ('server', 'device_key')
        for key in required:
            value = c.get(key)
            if key == 'chat_id' and type(value) is int:
                continue
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f'缺少渠道参数 {key}')
        if self.kind == 'telegram' and c.get('message_thread_id') is not None:
            if type(c['message_thread_id']) is not int or c['message_thread_id'] <= 0:
                raise ValueError('话题 ID 必须为正整数')
        if self.kind == 'bark':
            url = urlsplit(c['server'])
            if url.scheme not in ('http', 'https') or not url.hostname or url.query or url.fragment or url.username:
                raise ValueError('Bark server 必须为 HTTP(S) 服务根地址')
            if url.path.rstrip('/').endswith('/push'):
                raise ValueError('Bark server 不需要包含 /push')
            if not isinstance(c.get('params', {}), dict):
                raise ValueError('Bark params 必须为 JSON 对象')
            if set(c.get('params', {})) & {'device_key', 'body', 'title'}:
                raise ValueError('额外参数不能覆盖 device_key/body/title')
        return self


class TemplateFields(Input):
    title_template: Annotated[str, StringConstraints(strip_whitespace=False)] = Field(default='', max_length=500)
    body_template: Annotated[str, StringConstraints(strip_whitespace=False)] = Field(default='', max_length=10000)

    @field_validator('title_template', 'body_template')
    @classmethod
    def valid_template(cls, value):
        return validate_template(value)


class NotificationTemplate(TemplateFields):
    name: str = Field(min_length=1, max_length=100)


class Monitor(TemplateFields):
    name: str = Field(min_length=1, max_length=100)
    chat_id: int = Field(lt=0, ge=-SQLITE_MAX_ID)
    users: list[int | str] = Field(min_length=1, max_length=1000)
    channel_ids: list[PositiveID] = Field(default_factory=list, max_length=100)
    enabled: bool = True
    template_id: PositiveID | None = None

    @field_validator('users')
    @classmethod
    def valid_users(cls, users):
        result = []
        for user in users:
            if isinstance(user, str) and user.isdigit():
                user = int(user)
            if not ((type(user) is int and 0 < user <= SQLITE_MAX_ID) or
                    (isinstance(user, str) and user.startswith('@') and len(user) > 1)):
                raise ValueError('用户必须是正整数 ID 或 @username')
            if user not in result:
                result.append(user)
        return result


class Settings(Input):
    api_id: int | None = Field(default=None, gt=0)
    api_hash: str = ''
    proxy_host: str = ''
    proxy_port: int = Field(default=1080, ge=1, le=65535)
    proxy_username: str = ''
    proxy_password: SecretText = ''
    http_proxy: str = ''
    max_attempts: int = Field(default=10, ge=1, le=100)
    clear_http_proxy: bool = False

    @field_validator('http_proxy')
    @classmethod
    def valid_proxy(cls, value):
        if value and (urlsplit(value).scheme not in ('http', 'https', 'socks5', 'socks5h') or not urlsplit(value).hostname):
            raise ValueError('HTTP 代理地址必须为 http(s):// 或 socks5(h):// URL')
        return value


class LoginCode(Input):
    phone: str = Field(min_length=5, max_length=32)


class LoginVerify(Input):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=False)
    code: str = ''
    password: str = ''

    @field_validator('code')
    @classmethod
    def trim_code(cls, value):
        return value.strip()

    @model_validator(mode='after')
    def require_credentials(self):
        if not self.code and not self.password:
            raise ValueError('请填写验证码或两步验证密码')
        return self


class AdminSetup(Input):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=False)
    username: str = Field(min_length=3, max_length=64, pattern=r'^[a-zA-Z0-9_.-]+$')
    password: str = Field(min_length=12, max_length=256)
    confirm_password: str = Field(min_length=12, max_length=256)

    @model_validator(mode='after')
    def passwords_match(self):
        if self.password != self.confirm_password:
            raise ValueError('两次密码不一致')
        return self


class AdminUpdate(Input):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=False)
    username: str = Field(min_length=3, max_length=64, pattern=r'^[a-zA-Z0-9_.-]+$')
    current_password: str = Field(min_length=1, max_length=256)
    password: str = Field(default='', max_length=256)
    confirm_password: str = Field(default='', max_length=256)

    @model_validator(mode='after')
    def valid_password(self):
        if self.password and len(self.password) < 12:
            raise ValueError('新密码至少 12 位')
        if self.password != self.confirm_password:
            raise ValueError('两次新密码不一致')
        return self


class AdminLogin(Input):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=False)
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class SystemSettings(Input):
    timezone: str = Field(default='Asia/Shanghai', min_length=1, max_length=100)

    @field_validator('timezone')
    @classmethod
    def valid_timezone(cls, value):
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError('无效时区，请填写 IANA 时区名，例如 Asia/Shanghai 或 UTC') from None
        return value
