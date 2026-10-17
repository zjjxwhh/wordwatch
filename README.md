# 观言 · WordWatch

监控 Telegram 指定群组内指定用户的发言，通过 Telegram Bot 或 Bark 推送通知。

- Web 页面管理监控项、管理员账户和推送渠道。
- 支持多个 Bot / Bark 渠道，多选推送、跨监控项复用。
- 消息记录、配置和推送队列保存到 SQLite，失败自动重试。
- Python 3.14 + uv，支持 Docker 部署。

## 部署

### Docker Compose

在项目目录执行：

```sh
mkdir -p data
sudo chown 10001:10001 data  # 仅 Linux 需要

docker compose pull
docker compose up -d
```

Windows 使用 `New-Item -ItemType Directory -Force data` 创建目录，无需执行 `chown`。

### 使用 GHCR 镜像

镜像发布成功后，可直接运行，支持 amd64 / arm64：

```sh
docker run -d --name wordwatch --restart unless-stopped \
  -p 127.0.0.1:8080:8080 \
  -v "${PWD}/data:/app/data" \
  ghcr.io/zjjxwhh/wordwatch:latest
```

同样需要先创建 data 目录并设置权限。首次拉取私有镜像需要先登录 GHCR；如需匿名拉取，将 Package 可见性设为 Public。

## 首次使用

打开 [http://127.0.0.1:8080](http://127.0.0.1:8080)，在页面创建管理员账户并登录。登录有效期为 15 天，使用中自动续期；退出或修改账户信息后失效。然后：

1. **Telegram 连接**：从 [my.telegram.org](https://my.telegram.org) 获取 API ID / API Hash，保存后通过手机号、验证码及两步验证密码登录。
2. **我的群组**：查询群 ID。监听账号必须已加入目标群。
3. **推送渠道**：添加 Bot 或 Bark 渠道，发送测试通知。
4. **监控项**：填写群 ID、用户 ID 或 `@username`，选择推送渠道。匹配的新消息会出现在“消息记录”中。

Bot Token 从 [@BotFather](https://t.me/BotFather) 获取，私聊接收者需先向 Bot 发送 `/start`。Bark 填写服务根地址和 Device Key，使用 POST `/push`，支持图标、铃声及额外 JSON 参数。

在系统设置中配置统一的 SOCKS5 / HTTP CONNECT 代理，Telegram 连接和每个推送渠道分别勾选“使用代理”。最大推送尝试次数在每个渠道中设置（默认 10 次，包含首次发送）。升级时自动迁移旧代理及重试设置；如原来 Telegram 与通知使用不同代理，以 Telegram 代理作为系统代理，请在升级后确认。容器访问宿主机代理时，不能使用容器自身的 `127.0.0.1`。

## 通知模板

在“通知模板”选项卡中添加、编辑和预览多套预设，初始仅提供“默认消息”预设，正文带监控项、群组、发言者、时间和链接的 emoji 标记。编辑监控项时可直接选择预设，或选择“自定义 / 默认内容”手动填写；自定义模式可导入预设后独立修改。留空沿用默认内容，Bot 使用正文，Bark 使用标题与正文。

直接引用预设的监控项会跟随其修改；导入的自定义副本不受影响。删除预设时，关联监控项保留当前内容并转为自定义。所有模板存入 SQLite。

“系统设置”中可配置通知时区，默认 `Asia/Shanghai`。`{sent_at}` 等时间变量在渲染时转换到该时区，按 `YYYY-MM-DD HH:mm:ss` 输出，不显示时区标识，并自动适配夏令时。设置对新消息和预览生效，已入队通知不变；原始消息时间保留，网页消息列表仍按浏览器时区显示。

```text
{chat_title} · {sender_name}
{text}
{url}
```

还支持 `{monitor_name}`、`{chat_id}`、`{sender_id}`、`{username}`（不含 @）、`{sent_at}`、`{message_id}`。自定义正文只发送模板内容，不自动附加链接；字面花括号使用 `{{` / `}}`。模板修改对新消息生效，已入队通知保持原内容。

## 数据与维护

- `./data` 保存 SQLite、Telegram 登录会话和轮转日志。备份前停止服务，再复制整个目录；其中含密钥和会话，请勿公开。
- 仅监听新消息，不抓取历史、不处理编辑或删除，也不下载媒体；停机期间不保证补发。消息与推送记录目前不自动清理。
- 一个数据目录只运行一个实例。通知在极少数中断情况下可能重复发送。
- 默认仅允许本机访问；远程访问请使用 HTTPS 反向代理或 SSH 隧道。

Compose 常用命令：

```sh
docker compose logs -f --tail=100   # 查看日志
docker compose pull
docker compose up -d       # 更新并重启
docker compose down               # 停止服务，保留 data
docker compose run --rm wordwatch python main.py password  # 重置管理员密码
```

## 本地开发

安装 [uv](https://docs.astral.sh/uv/getting-started/installation/)，在项目根目录执行：

```sh
uv sync --locked                   # 创建虚拟环境并安装依赖
uv run python main.py serve        # 启动服务
uv run pytest -q                   # 运行测试
```

启动入口为 `main.py`，业务代码和页面资源位于 `src/`。默认使用 `data/`，可通过 `DATA_DIR` 修改。

## 镜像发布

[GitHub Workflow](.github/workflows/docker-publish.yml) 在 `main` 推送或手动运行时，直接构建并推送到 `ghcr.io/zjjxwhh/wordwatch`；PR 仅构建，不发布。使用自带 `GITHUB_TOKEN`，无需额外密钥。

每次发布只生成两个镜像标签：`latest` 和北京时间（Asia/Shanghai）的 `YYYYMMDDHHmm` 时间戳，例如 `202610161530`。
