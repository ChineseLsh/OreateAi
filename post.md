# [开源] ThreadAI — Oreate AI 视频生成协议工具，自动注册+裂变+签到+批量生视频

## 项目简介

把 [Oreate AI](https://www.oreateai.com) 的视频生成能力完整协议化了，支持自动注册账号、裂变拉新、每日签到、批量异步生成视频，带 Web 管理面板。

注册一个号 10 秒，裂变 10 个号 2 分钟，每个号 50+ 积分，生成一个 5 秒视频只要 5~20 积分。

**GitHub**: https://github.com/ChineseLsh/OreateAi

---

## 功能一览

| 功能 | 说明 |
|------|------|
| 自动注册 | 临时邮箱全自动，10 秒一个号 |
| 裂变注册 | 链式邀请注册，Clash 节点自动轮换 IP |
| 每日签到 | 批量 emaillogin 触发签到，+30 积分/天/号 |
| 文生视频 | 输入文字描述，SSE 流式生成 |
| 图生视频 | 上传参考图 + 描述，GCS 上传 + SSE 生成 |
| 账号池调度 | Cookie 持久化 + 密码登录降级，自动选号 |
| 积分追踪 | 生成前后积分差值计算，SQLite 持久化 |
| Web 面板 | 三页 Tab（生成/账号/历史），批量异步提交 |

## 支持模型

支持 Oreate AI 平台上的 15 个模型，aiType 动态获取：

| 模型 | 真人 | 最低积分 (5s) |
|------|:----:|:----:|
| Pixverse V5 | ✓ | 5 |
| Seedance 1.5 Pro | ✓ | 7 |
| Kling 2.6 | ✓ | 15 |
| Seedance 2.0 Mini | ✗ | 20 |
| Seedance 2.0 Fast | ✗ | 25 |
| Kling 2.5 | ✓ | 25 |
| Kling 3.0 / 3.0 Omni | ✓ | 30 |
| Wan 2.5 / 2.6 / 2.7 | ✓ | 30 |
| Seedance 2.0 | ✗ | 40 |
| Veo 3 / 3.1 | ✓ | 100 |

> 带 ✗ 的模型不支持真人参考图，前端会自动提示。

---

## 快速开始

```bash
git clone https://github.com/ChineseLsh/OreateAi.git
cd OreateAi

pip install httpx cryptography fastapi uvicorn python-multipart

# 编辑 config.py 设置你的代理端口
# DEFAULT_PROXY = "http://127.0.0.1:7897"

python -m uvicorn api.server:app --host 0.0.0.0 --port 8900
```

打开 `http://localhost:8900` 就能用了。

---

## 界面截图

### 视频生成页

左侧输入描述（支持多行批量提交），选模型/时长/比例，可上传参考图。右侧实时显示生成队列，完成后直接内嵌播放。

### 账号管理页

一键注册、批量裂变、全部签到。账号表格展示邮箱、密码、积分、状态、最近使用时间。

### 生成历史页

所有视频生成记录，支持状态筛选（完成/生成中/失败），点击播放或下载。

---

## 技术细节

### 注册流程

```
getticket (获取 RSA 公钥)
    → emailsignupin (RSA 加密密码提交注册)
    → 临时邮箱收验证链接，提取 tokenID
    → emailregisterconfirm (确认注册)
    → 自动获得 50 积分
```

临时邮箱用的 [linshiyouxiang.net](https://www.linshiyouxiang.net) 的 API，支持 `mowan666.com`、`mailsbay.com` 等多域名。

### 登录复用

发现了 `/passport/api/emaillogin` 接口——可以用邮箱+密码直接登录，不需要邮箱验证。这意味着：

- 注册时保存 cookies，后续直接恢复 session
- Cookie 过期了自动走 emaillogin 降级
- 登录自动触发每日签到 +30 积分

### 视频生成 (SSE)

```
create/chat → 建会话
    → sse/stream (POST, SSE 流式)
    → 服务端每 5 秒 ping
    → generating 事件返回视频 URL
    → 下载 MP4
```

图生视频需要两步：
1. `getuploadbostoken` 获取 GCS 上传凭证
2. 上传到 Google Cloud Storage
3. SSE 提交时在 `videoConfig.textOrImage.image` 和 `messages[0].attachments` 同时传 objectPath

### 账号池调度

```python
acquire_account(min_points=20)
    → 按积分 DESC + 最久未用排序
    → 恢复 Cookie → 失败则 emaillogin
    → 生成视频
    → release_account(更新积分)
```

### IP 轮换

裂变注册时通过 Clash API 自动切换节点，每个号不同 IP：

```python
from core.clash import NodeRotator
rotator = NodeRotator()
for i in range(depth):
    rotator.next()  # 自动切节点
    fission_register(invite_code)
rotator.restore()  # 恢复原始节点
```

---

## 项目结构

```
OreateAi/
├── config.py              # 代理、API 地址配置
├── main.py                # CLI 入口
├── api/server.py          # FastAPI 服务 (10 个端点)
├── core/
│   ├── client.py          # HTTP 客户端 (SSL 兼容)
│   ├── crypto.py          # RSA 加密 (PKCS#1)
│   ├── db.py              # SQLite (accounts + videos)
│   ├── pool.py            # 账号池调度
│   └── clash.py           # Clash 节点切换
├── modules/
│   ├── register.py        # 注册全流程
│   ├── login.py           # 登录 + 批量签到
│   ├── fission.py         # 裂变注册 + IP 轮换
│   ├── video.py           # SSE 视频生成
│   ├── upload.py          # GCS 图片上传
│   └── email_provider.py  # 临时邮箱 (多源)
└── static/index.html      # Web 面板 (三页 Tab SPA)
```

总共 ~2600 行 Python + 460 行前端，纯协议实现无浏览器依赖。

---

## API 接口

如果你想二次开发或接入自己的系统：

```
GET  /api/stats                      # 统计
GET  /api/accounts                   # 账号列表
POST /api/register                   # 注册单号
POST /api/fission {depth}            # 裂变注册
POST /api/checkin                    # 全部签到
POST /api/video {prompt, model_name} # 生成视频
POST /api/upload (multipart)         # 上传参考图
GET  /api/task/{id}                  # 查询任务状态
```

所有耗时操作都是异步的，POST 返回 `task_id`，轮询 `/api/task/{id}` 获取结果。

---

## 注意事项

1. **需要代理**：Oreate AI 在国内无法直连，需要 Clash/V2Ray 等代理
2. **模型兼容性**：Seedance 2.0 系列不支持真人参考图，用 Kling/Seedance 1.5 Pro
3. **积分机制**：注册 50 + 每日签到 30 = 每天 80 积分/号
4. **生成耗时**：5 秒视频大约需要 1~3 分钟生成
5. **临时邮箱**：依赖 linshiyouxiang.net，如果被 CF 拦需要换亚洲节点

---

## License

MIT - 随便用，给个 Star 就行。

**GitHub**: https://github.com/ChineseLsh/OreateAi
