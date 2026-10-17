# AI-IM-Bot

<img src="icon.png" width="120" alt="AI-IM-Bot" align="right" />

> 让 QQ 替你回消息：接入大模型，自动回复私聊与群聊，带上下文记忆、定时问候和群助手。
> Windows 桌面 GUI + 单文件 exe，NapCat 一键安装，扫码登录。

![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)
![Windows](https://img.shields.io/badge/Platform-Windows%2010%2F11-0078D4?logo=windows)
![License](https://img.shields.io/badge/License-MIT-green)
![AI](https://img.shields.io/badge/AI-OpenAI%20Compatible-35e0b0)

---

## 这是什么

一个跑在 Windows 上的 QQ 自动回复机器人：

- 用 **OneBot / NapCat** 连接官方 QQ 客户端，不破解协议、不改数据库
- 用 **OpenAI 兼容接口**（DeepSeek / OpenAI / 通义 / Kimi / Ollama…）生成回复
- 自带 **图形配置向导**，填 Key、点几下就能跑，也可以打包成单个 exe

> 灵感来自 [EverydayWechat](https://github.com/sfyc23/EverydayWechat) 的「定时问候 + 群助手」设计，
> 但它的 itchat 网页版通道已被腾讯关闭，本项目改用 QQ 协议端实现。

---

## 功能

| 模块 | 说明 |
|---|---|
| 自动回复 | 私聊、群聊；群聊默认 **@ 才回** |
| 触发策略 | 白名单 / 黑名单 / 关键词 / 安静时段 / 冷却 / 每分钟限流 |
| 上下文记忆 | 每个会话独立多轮记忆，SQLite 持久化，可单独清空 |
| 定时问候 | cron + 随机抖动、AI 生成或模板、纪念日天数、天气素材、防重复发送 |
| 群助手 | 群里 @ 后：`帮助` / `天气 北京` / `每日一句`，其他交给 AI |
| Web 控制台 | 消息流、自动回复开关、测试消息、手动发送、记忆管理、日志 |
| 桌面 GUI | 配置向导、NapCat 一键下载、扫码登录、启动/停止、实时日志 |
| 打包 exe | PyInstaller 单文件，双击即用，无需 Python 环境 |
| 自检 | `doctor` 逐项检查依赖、Key、NapCat 连接、模型连通性 |

---

## 界面预览

```
┌───────────────────────────────────────────────┬────────────────────────┐
│  AI-IM-BOT   QQ 自动回复配置向导        ● 运行中 │  运行日志               │
│                                               │  22:31:02 INFO 已连接   │
│  大模型                                       │  OneBot: ws://127.0.0.1│
│  Base URL  https://api.deepseek.com/v1        │  :3001                 │
│  API Key   ********                            │  22:31:05 INFO 回复     │
│  模型名    deepseek-chat                       │  私聊:小明(20001) 在吗  │
│                                               │  22:31:06 INFO 已回复   │
│  机器人                                       │                        │
│  机器人 QQ  123456789                          │                        │
│  白名单     987654321                          │                        │
│                                               │                        │
│  NapCat  NapCat 已安装，3001 端口已连接         │                        │
│  [检测] [一键下载安装]                          │                        │
│  [启动 NapCat] [扫码登录]                       │                        │
└───────────────────────────────────────────────┴────────────────────────┘
```

> 截图可放到 `docs/screenshot.png` 后在 README 顶部引用。

---

## 快速开始

### 方式一：安装程序（推荐）

1. 到仓库的 **Releases** 页面下载 `ai-im-bot-setup.exe`
2. 双击安装：默认装到 `C:\Ai-im-bot`，也可以改路径
3. 安装完成会在桌面生成 `Aibot` 快捷方式，从它启动
4. 填写 API Key / 模型，点「保存配置」
5. 点 **「快速安装」**：自动下载安装器 → 测速选择最快源下载 `NapCat.Shell.zip` → 运行安装器
6. 安装完成点「启动 NapCat」，手机 QQ 扫码登录小号
7. 点「自动配置」写入 3001 端口 WebSocket 配置
8. 点「启动机器人」，日志出现 `已连接 OneBot` 即成功

> 静默安装：`ai-im-bot-setup.exe --silent --dir D:\Ai-im-bot`

### 方式二：源码运行

```powershell
git clone https://github.com/<your-name>/ai-im-bot.git
cd ai-im-bot

# 依赖只有三个
pip install -r requirements.txt

# 复制配置
Copy-Item config.example.yaml config.yaml
Copy-Item .env.example .env
notepad .env            # 填 LLM_API_KEY

# 图形界面
python gui.py

# 或者命令行
python run.py doctor
python run.py run
```

> 路径含中文导致 pip 报错时，用自带的离线引导脚本：
> `python tools\bootstrap_deps.py`，然后 `$env:PYTHONPATH="vendor"`。

---

## 使用流程

```
NapCat 启动（拉起 QQ）
   └─ 扫码登录小号
        └─ NapCat WebUI (127.0.0.1:6099) 里加 WebSocket 服务器
             └─ 端口 3001 / Token 与 config 一致 / 消息格式 array
                  └─ 启动 AI-IM-Bot
                       └─ Web 控制台 http://127.0.0.1:8787
```

第一次务必先这样配，避免乱回：

```yaml
policy:
  private: whitelist
  allow_users: ["你自己的QQ号"]
  group: mention
reply:
  dry_run: true      # 只生成不发送，观察几天再关
```

---

## 配置说明

完整示例见 [`config.example.yaml`](config.example.yaml)。

| 配置 | 说明 |
|---|---|
| `platform.type` | `onebot`（QQ）/ `mock`（本地测试）/ `wechat`（3.9 实验） |
| `policy.private` | `whitelist` / `all` / `off` |
| `policy.group` | `off` / `mention` / `all` |
| `policy.allow_users` | QQ 号字符串列表 |
| `policy.quiet_hours` | 如 `["23:30-08:00"]` |
| `reply.dry_run` | 只生成不发送 |
| `reply.fallback` | 模型失败时的兜底回复 |
| `greeting.items` | 定时问候（时间、抖动、目标、AI/模板） |
| `group` | 群助手开关与帮助文案 |
| `persona` | 人格文件名，对应 `personas/<name>.md` |

密钥放 `.env`，支持 `${VAR}` 与 `${VAR:-默认值}`。

### 人格

`persona: default` 会加载 [`personas/default.md`](personas/default.md) 作为系统提示，内置：

| 人格 | 风格 |
|---|---|
| `default` | 私人助理，口语化、简短 |
| `warm` | 温柔、会关心人 |
| `funny` | 幽默损友，爱开玩笑 |
| `assistant` | 专业、克制 |

想加自己的：在 `personas/` 下新建 `my.md`，重启后在 GUI「人格」下拉里选 `my`。
人格文件是纯文本，写自然语言提示即可。

---

## 项目结构

```
ai-im-bot/
├─ gui.py                  # 桌面配置向导
├─ run.py                  # CLI：run / mock / doctor / send
├─ installer.py            # NapCat 一键安装/启动
├─ napcat_webui.py         # NapCat WebUI 扫码登录客户端
├─ build_exe.py            # 打包单文件 exe
├─ aibot/
│  ├─ config.py            # 配置加载与校验
│  ├─ app.py               # 组装与生命周期
│  ├─ pipeline.py          # 去重 → 策略 → 命令 → AI → 发送
│  ├─ policy.py            # 白/黑名单、@、冷却、限流
│  ├─ llm.py               # OpenAI 兼容客户端
│  ├─ greeting.py          # 定时问候
│  ├─ group_helper.py      # 群助手
│  ├─ store.py             # SQLite
│  ├─ adapters/
│  │  ├─ onebot.py         # OneBot v11 正/反向 WS
│  │  ├─ mock.py
│  │  └─ wechat_uia.py     # 微信 3.9 实验性
│  └─ web/                 # 零依赖 HTTP 控制台
├─ tests/
└─ tools/
```

---

## 测试

```powershell
$env:PYTHONPATH = "vendor"   # 如果用了离线依赖
python -m unittest discover -s tests -v
```

覆盖：策略、去重、上下文、dry_run、模型失败兜底、定时问候（星期/过期/防重复）、群助手命令、OneBot 解析。

---

## 常见问题

**NapCat 连接失败**
WebSocket 服务器没开、端口不是 3001、Token 不一致。先确认 QQ 已登录且 `127.0.0.1:6099` 能打开。

**群聊不回复**
`policy.group` 是 `mention` 时必须真的 @ 机器人；检查群号是否被 `block_groups` 拦截。

**私聊不回复**
默认白名单模式，把对方 QQ 号加进 `policy.allow_users`。

**扫码时拿不到二维码**
GUI 会回退到打开 NapCat WebUI，在那里扫码即可。

**网页版微信**
不受支持。微信 4.x 的 UIA 树为空，itchat 网页版已被腾讯关闭。

---

## 免责声明

- 协议端属于第三方客户端增强，**腾讯不保证容忍**，请用小号、控制频率、不要群发营销。
- AI 可能说错话，先用 `dry_run` 观察；系统提示已约束转账/验证码/链接，但不保证 100%。
- 消息会发送给你配置的模型服务商，敏感聊天请关闭自动回复。
- 禁止用于诈骗、骚扰、批量加好友、绕过平台风控。

## 致谢

- [NapCatQQ](https://github.com/NapNeko/NapCatQQ)：QQ 协议端
- [EverydayWechat](https://github.com/sfyc23/EverydayWechat)：定时问候 / 群助手的设计灵感
- [wttr.in](https://wttr.in/) / [hitokoto](https://hitokoto.cn/)：天气与一言

## License

[MIT](LICENSE)
