# ThreadAI

Oreate AI 视频生成协议工具 — 自动注册、裂变、签到、批量视频生成。

## 功能

- **自动注册** — 临时邮箱自动注册 Oreate AI 账号，零人工干预
- **裂变注册** — 链式邀请注册，Clash 节点自动轮换 IP
- **每日签到** — 批量登录触发每日签到，积分自动累积
- **视频生成** — 文生视频 / 图生视频，SSE 流式接收，异步批量生成
- **账号池调度** — Cookie 持久化 + emaillogin 降级，自动选号、积分追踪
- **前端面板** — 三页 Tab（视频生成 / 账号管理 / 生成历史），批量提交，实时状态

## 快速开始

```bash
# 安装依赖
pip install httpx cryptography fastapi uvicorn python-multipart

# 启动服务
python -m uvicorn api.server:app --host 0.0.0.0 --port 8900

# 打开浏览器
# http://localhost:8900
```

## 代理配置

编辑 `config.py` 设置代理：

```python
DEFAULT_PROXY = "http://127.0.0.1:7897"  # Clash 代理端口
```

裂变注册支持 Clash 节点自动轮换（需配置 `core/clash.py` 中的 API 端口和 secret）。

## 项目结构

```
threadAi/
├── config.py              # 全局配置
├── main.py                # CLI 入口
├── requirements.txt       # 依赖
├── api/
│   └── server.py          # FastAPI 服务
├── core/
│   ├── client.py          # HTTP 客户端
│   ├── crypto.py          # RSA 加密
│   ├── db.py              # SQLite 持久化
│   ├── pool.py            # 账号池调度
│   ├── clash.py           # Clash 节点切换
│   └── fingerprint.py     # 设备指纹
├── modules/
│   ├── register.py        # 注册流程
│   ├── login.py           # 登录 + 签到
│   ├── fission.py         # 裂变注册
│   ├── video.py           # 视频生成 (SSE)
│   ├── upload.py          # 图片上传 (GCS)
│   └── email_provider.py  # 临时邮箱
├── static/
│   └── index.html         # 前端面板
└── videos/                # 生成的视频
```

## API

| 方法 | 端点 | 说明 |
|------|------|------|
| GET | `/api/stats` | 统计信息 |
| GET | `/api/accounts` | 账号列表 |
| POST | `/api/register` | 注册单号 |
| POST | `/api/fission` | 裂变注册 |
| POST | `/api/checkin` | 全部签到 |
| POST | `/api/accounts/{email}/refresh` | 刷新账号 |
| GET | `/api/videos` | 视频历史 |
| POST | `/api/video` | 生成视频 |
| POST | `/api/upload` | 上传参考图 |
| GET | `/api/task/{id}` | 查询任务状态 |

## 支持模型

| 模型 | 真人 | 最低积分 (5s) | 说明 |
|------|:----:|:----:|------|
| Pixverse V5 | ✓ | 5 | 潮流特效极速生成 |
| Seedance 1.5 Pro | ✓ | 7 | 电影级微表情 |
| Kling 2.6 | ✓ | 15 | 高难动作与高保真配音 |
| Seedance 2.0 Mini | ✗ | 20 | 更快速度，不支持真人 |
| Seedance 2.0 Fast | ✗ | 25 | 旗舰快速渲染，不支持真人 |
| Kling 2.5 | ✓ | 25 | 经典稳定首选 |
| Kling 3.0 | ✓ | 30 | 电影感超长分镜 |
| Kling 3.0 Omni | ✓ | 30 | 角色道具一致性 |
| Wan 2.5 / 2.6 / 2.7 | ✓ | 30 | 写实画面 / 拟真口型 |
| Seedance 2.0 | ✗ | 40 | 旗舰多模态，不支持真人 |
| Kling o1 | ✓ | 40 | 文本指令视频编辑 |
| Veo 3 / 3.1 | ✓ | 100 | 极高精度超写实 |

> 积分随分辨率、时长、音频增加。详细定价通过 `/oreate/aivideo/getmodelconfigv3` 接口动态获取。

## License

MIT
