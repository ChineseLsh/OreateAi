# ThreadAI

OreateAI 协议优先工具，提供账号注册、每日签到、账号池恢复和视频生成。

用户信息、积分、签到、模型配置和会话创建通过 `curl_cffi` Chrome 指纹协议请求完成。只有注册、密码登录和视频 SSE 需要动态 `jt` 时，才按需启动真实 Chrome 并在同一页面环境内完成风控提交。账号 Cookie 保存在 `data.db`，协议与浏览器会话会自动同步。

## 环境要求

- Python 3.10+
- Google Chrome
- 可访问 OreateAI、邮箱服务和视频 CDN 的网络环境

安装 Python 依赖和 Playwright Chrome 支持：

```powershell
python -m pip install -r requirements.txt
python -m playwright install chrome
```

默认使用本机 Chrome channel，并以无头模式启动。查询、Cookie 恢复和签到不会打开 Chrome；首次执行动态风控请求时才加载 OreateAI 风控运行时。

## 基础配置

配置保存在项目根目录的 `.env`，该文件已被 Git 忽略。首次使用可复制 `.env.example`，也可以启动 Web 服务后在“配置管理”页面保存。配置在服务启动时载入，页面保存后需要重启服务。

```powershell
Copy-Item .env.example .env
```

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `THREADAI_PROXY` | `http://127.0.0.1:7897` | 注册、账号恢复和视频请求使用的代理；设为空字符串表示直连 |
| `THREADAI_EMAIL_PROVIDER` | `auto` | `auto`、`luckmail`、`mailtm`、`linshiyouxiang`、`guerrilla` 或 `1secmail` |
| `THREADAI_ALLOW_PLUS_EMAIL` | `false` | 是否允许邮箱本地部分包含 `+`；成功 HAR 显示此类地址会被拒绝 |
| `THREADAI_BROWSER_CHANNEL` | `chrome` | Playwright 浏览器 channel |
| `THREADAI_BROWSER_HEADLESS` | `true` | 是否无头运行 Chrome；排障时可设为 `false` 显示窗口 |
| `THREADAI_BROWSER_TIMEOUT_MS` | `60000` | 页面和风险运行时就绪超时 |
| `THREADAI_BROWSER_RISK_TIMEOUT_MS` | `15000` | 单次浏览器风险令牌超时；失败时仅重试令牌一次 |

CLI 的 `--proxy` 省略时会保留 `THREADAI_PROXY` 配置；显式传入时仅覆盖本次命令。

## LuckMail

LuckMail provider 支持项目邮箱、项目接码和私有邮箱库存三种模式。提供给 OreateAI 的邮箱必须为不带 `+` 别名的独立地址。

默认通过 LuckMail 的 Grok 项目购买或复用 `ms_imap` 类型的 `outlook.com` 邮箱，并使用邮箱 Token 读取 OreateAI 原始验证邮件。`project_order` 保留项目规则接码，`private_inventory` 使用私有邮箱库存 + Microsoft OAuth/IMAP。

在 `.env` 中启用：

```dotenv
THREADAI_EMAIL_PROVIDER=luckmail
LUCKMAIL_API_KEY=<your-api-key>
LUCKMAIL_API_SECRET=<your-api-secret>
```

`LUCKMAIL_API_SECRET` 仅在服务端要求签名时配置。配置页面不会回传密钥原文；密码框留空会保留现有值，勾选清除才会删除。

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `LUCKMAIL_BASE_URL` | `https://mails.luckyous.com` | LuckMail API 根地址 |
| `LUCKMAIL_PROXY` | 回退到 `THREADAI_PROXY` | LuckMail API 与 Microsoft token 请求代理 |
| `LUCKMAIL_IMAP_PROXY` | 回退到 `THREADAI_PROXY` | IMAP 连接代理，支持 HTTP/SOCKS |
| `LUCKMAIL_HTTP_RETRIES` | `3` | API 请求次数 |
| `LUCKMAIL_MODE` | `project_purchase` | `project_purchase` 读取项目邮箱原始邮件；`project_order` 使用项目规则接码；`private_inventory` 使用私有邮箱库存 |
| `LUCKMAIL_PROJECT_CODE` | `grok` | LuckMail 项目代码 |
| `LUCKMAIL_EMAIL_TYPE` | `ms_imap` | 项目邮箱类型 |
| `LUCKMAIL_DOMAIN` | `outlook.com` | 项目邮箱要求的域名；不匹配的地址不会使用 |
| `LUCKMAIL_ORDER_ALLOCATION_ATTEMPTS` | `10` | 遇到已注册邮箱时保持订单占位并继续分配的最大次数 |
| `LUCKMAIL_ORDER_TIMEOUT` | `300` | 项目订单接码超时秒数 |
| `LUCKMAIL_ORDER_POLL_INTERVAL` | `3` | 项目订单轮询间隔秒数 |
| `LUCKMAIL_INVENTORY_CACHE_SECONDS` | `60` | 邮箱库存缓存时间 |
| `LUCKMAIL_POLL_INTERVAL` | `5` | 收件箱轮询间隔 |
| `LUCKMAIL_RECENT_SECONDS` | `900` | 只接受该时间窗口内的邮件 |
| `LUCKMAIL_IMAP_HOSTS` | Outlook 两个常用 IMAP host | 逗号分隔的 IMAP host 列表 |
| `LUCKMAIL_IMAP_LAST_N` | `30` | 每次检查最近邮件数量 |
| `LUCKMAIL_REQUIRE_RECIPIENT_MATCH` | `true` | 验证邮件收件人必须与当前邮箱一致 |

也可以只覆盖一次注册的 provider：

```powershell
python main.py register --provider luckmail
```

## CLI

注册会启动浏览器环境，完成后自动把账号、Cookie 和积分写入 SQLite：

```powershell
# 使用 THREADAI_EMAIL_PROVIDER
python main.py register

# 指定 provider
python main.py register --provider luckmail

# 指定已有邮箱，验证邮件到达后手动输入 tokenID
python main.py register --email "user@example.com"
```

查询账号真实登录状态和积分。账号选择器可使用邮箱，或使用从 0 开始的账号下标；下标按最新账号优先排列：

```powershell
python main.py check 0
python main.py check "user@example.com"
```

为账号库中的全部账号恢复浏览器会话并领取每日 first-use 积分：

```powershell
python main.py checkin
```

生成视频时可自动选择积分最高的可用账号，也可用 `--account` 固定账号：

```powershell
python main.py video "雨夜城市街道的电影镜头" --save videos/demo.mp4
python main.py video "海边日落" --account 0 --resolution 720 --ratio 16:9
python main.py video "产品缓慢旋转" --account "user@example.com" --no-audio
```

提交前会读取服务端模型配置并计算实际积分成本。常用选项：

- `--model`：模型名称，默认 `Seedance 2.0 Mini`
- `--duration`：`5` 或 `10` 秒
- `--resolution`：例如 `480` 或 `720`
- `--ratio`：例如 `16:9`、`9:16` 或 `1:1`
- `--no-audio`：关闭音频
- `--image-url`：已上传参考图的 objectPath
- `--save`：生成后下载到本地路径

查看完整帮助：

```powershell
python main.py --help
python main.py video --help
```

## Web 服务

```powershell
python -m uvicorn api.server:app --host 127.0.0.1 --port 8900
```

打开 `http://127.0.0.1:8900`。Web 服务与 CLI 共用 `data.db`、浏览器配置、邮箱 provider 和代理设置。

## API

| 方法 | 端点 | 说明 |
|------|------|------|
| GET | `/api/stats` | 统计信息 |
| GET | `/api/accounts` | 账号列表 |
| POST | `/api/register` | 注册单号 |
| POST | `/api/fission` | 裂变注册 |
| POST | `/api/checkin` | 全部签到 |
| POST | `/api/accounts/{email}/refresh` | 恢复账号并刷新积分 |
| GET | `/api/videos` | 视频历史 |
| POST | `/api/video` | 生成视频 |
| POST | `/api/upload` | 上传参考图 |
| GET | `/api/task/{id}` | 查询后台任务状态 |

## 项目结构

```text
threadAi/
|-- config.py                 # 环境变量和全局配置
|-- main.py                   # CLI 入口
|-- requirements.txt          # Python 依赖
|-- api/server.py             # FastAPI 服务
|-- core/browser_runtime.py   # Chrome/Playwright 风险请求运行时
|-- core/client.py            # 浏览器与 HTTP 会话封装
|-- core/db.py                # SQLite 持久化
|-- core/pool.py              # 账号锁、恢复和调度
|-- modules/email_provider.py # 邮箱 provider 工厂
|-- modules/luckmail.py       # LuckMail + Microsoft OAuth/IMAP
|-- modules/register.py       # 注册与邮箱确认
|-- modules/login.py          # 登录与签到
|-- modules/video.py          # 模型定价、SSE 和视频下载
|-- static/index.html         # Web 面板
`-- videos/                   # 本地视频目录
```

`data.db`、`accounts.jsonl` 以及浏览器会话数据包含账号凭据或登录状态，应保留在本机并避免提交到版本库。

## License

MIT
