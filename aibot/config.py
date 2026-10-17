"""配置加载：YAML + 环境变量展开 + 严格校验。

配置里的字符串支持 ``${VAR}`` 与 ``${VAR:-默认值}``，在启动时展开。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any, get_args, get_origin, get_type_hints

import yaml

_ENV_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


class ConfigError(Exception):
    """配置错误，消息面向用户，可直接打印。"""


@dataclass
class OneBotConfig:
    mode: str = "forward"
    ws_url: str = "ws://127.0.0.1:3001"
    access_token: str = ""
    self_id: str = ""
    reverse_host: str = "127.0.0.1"
    reverse_port: int = 6700
    reverse_path: str = "/onebot/v11/ws"
    reverse_token: str = ""


@dataclass
class WeChatConfig:
    window_name: str = "微信"
    send_key: str = "enter"
    poll_interval: float = 1.0


@dataclass
class LLMConfig:
    base_url: str = "https://api.deepseek.com/v1"
    api_key: str = ""
    model: str = "deepseek-chat"
    temperature: float = 0.8
    max_tokens: int = 600
    timeout_seconds: float = 60.0
    history_turns: int = 8
    max_reply_chars: int = 500
    system_prompt: str = "你是一个简洁、友好的中文聊天助手。"
    extra_headers: dict[str, str] = field(default_factory=dict)


@dataclass
class PolicyConfig:
    private: str = "whitelist"
    group: str = "mention"
    allow_users: list[str] = field(default_factory=list)
    block_users: list[str] = field(default_factory=list)
    allow_groups: list[str] = field(default_factory=list)
    block_groups: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    quiet_hours: list[str] = field(default_factory=list)
    cooldown_seconds: float = 5.0
    max_per_minute: int = 6
    max_per_session_per_hour: int = 30


@dataclass
class ReplyConfig:
    prefix: str = ""
    suffix: str = ""
    group_at_sender: bool = True
    fallback: str = ""
    dry_run: bool = False


@dataclass
class WebConfig:
    enabled: bool = True
    host: str = "127.0.0.1"
    port: int = 8787
    token: str = ""


@dataclass
class StorageConfig:
    db_path: str = "data/bot.db"
    log_path: str = "data/bot.log"


@dataclass
class GreetingItem:
    """一条定时问候。"""

    name: str = "问候"
    time: str = "08:30"
    jitter: int = 120
    friends: list[str] = field(default_factory=list)   # QQ 号
    groups: list[str] = field(default_factory=list)    # 群号
    mode: str = "ai"                                    # ai | fixed
    prompt: str = "写一条自然、不油腻的问候，结合今天的日期，一两句话。"
    template: str = ""
    weather_city: str = ""
    start_date: str = ""
    start_date_msg: str = "认识你的第{}天"
    weekdays: list[int] = field(default_factory=lambda: [1, 2, 3, 4, 5, 6, 7])


@dataclass
class GreetingConfig:
    enabled: bool = False
    items: list[GreetingItem] = field(default_factory=list)


@dataclass
class GroupHelperConfig:
    enabled: bool = True
    weather_enabled: bool = True
    hitokoto_enabled: bool = True
    help_text: str = "群助手可用：\n· 天气 北京\n· 每日一句\n· 帮助\n其他话直接说，我会用 AI 回你。"


@dataclass
class Config:
    platform_type: str = "onebot"
    onebot: OneBotConfig = field(default_factory=OneBotConfig)
    wechat: WeChatConfig = field(default_factory=WeChatConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    policy: PolicyConfig = field(default_factory=PolicyConfig)
    reply: ReplyConfig = field(default_factory=ReplyConfig)
    greeting: GreetingConfig = field(default_factory=GreetingConfig)
    group: GroupHelperConfig = field(default_factory=GroupHelperConfig)
    web: WebConfig = field(default_factory=WebConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    persona: str = "default"          # 对应 personas/<name>.md
    log_level: str = "INFO"

    # 运行期附加信息（不来自 yaml）
    root: Path = field(default=Path("."), repr=False)
    warnings: list[str] = field(default_factory=list, repr=False)

    @classmethod
    def load(cls, path: str | Path) -> "Config":
        path = Path(path).expanduser().resolve()
        if not path.exists():
            raise ConfigError(f"配置文件不存在：{path}")
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            raise ConfigError(f"YAML 解析失败：{exc}") from exc
        if not isinstance(data, dict):
            raise ConfigError("配置文件顶层必须是键值映射")

        data = _expand_env(data)
        data = _flatten_platform(data)

        cfg = _build(Config, data, path.parent)
        cfg.root = path.parent
        _apply_persona(cfg)
        cfg.validate()
        return cfg

    def validate(self) -> None:
        errors: list[str] = []
        if self.platform_type not in {"onebot", "mock", "wechat"}:
            errors.append(f"platform.type 只能是 onebot/mock/wechat，当前为 {self.platform_type!r}")
        if self.onebot.mode not in {"forward", "reverse"}:
            errors.append(f"platform.onebot.mode 只能是 forward/reverse，当前为 {self.onebot.mode!r}")
        if self.policy.private not in {"whitelist", "all", "off"}:
            errors.append(f"policy.private 只能是 whitelist/all/off，当前为 {self.policy.private!r}")
        if self.policy.group not in {"off", "mention", "all"}:
            errors.append(f"policy.group 只能是 off/mention/all，当前为 {self.policy.group!r}")
        if self.platform_type == "onebot" and self.onebot.mode == "forward":
            if not self.onebot.ws_url.startswith(("ws://", "wss://")):
                errors.append("platform.onebot.ws_url 必须以 ws:// 或 wss:// 开头")
        if not self.llm.base_url.strip():
            errors.append("llm.base_url 不能为空")
        if not self.llm.model.strip():
            errors.append("llm.model 不能为空")
        if not (0.0 <= self.llm.temperature <= 2.0):
            errors.append("llm.temperature 必须在 0~2 之间")
        if self.llm.history_turns < 0:
            errors.append("llm.history_turns 不能为负数")
        for item in self.greeting.items:
            if not re.fullmatch(r"\s*\d{1,2}:\d{2}\s*", item.time):
                errors.append(f"greeting 里 {item.name} 的时间 {item.time!r} 不是 HH:MM")
            if item.mode not in {"ai", "fixed"}:
                errors.append(f"greeting 里 {item.name} 的 mode 只能是 ai/fixed")
            for day in item.weekdays:
                if day not in range(1, 8):
                    errors.append(f"greeting 里 {item.name} 的 weekdays 只能是 1~7")
            if not item.friends and not item.groups:
                errors.append(f"greeting 里 {item.name} 没有 friends/groups，发不出去")
        if not (1 <= self.web.port <= 65535):
            errors.append("web.port 必须在 1~65535 之间")
        for expr in self.policy.quiet_hours:
            if _parse_hour_range(expr) is None:
                errors.append(f"policy.quiet_hours 格式错误：{expr!r}（应为 HH:MM-HH:MM）")
        if errors:
            raise ConfigError("配置校验失败：\n  - " + "\n  - ".join(errors))

    def resolve_path(self, value: str) -> Path:
        p = Path(value).expanduser()
        return p if p.is_absolute() else (self.root / p).resolve()

    @property
    def db_path(self) -> Path:
        return self.resolve_path(self.storage.db_path)

    @property
    def log_path(self) -> Path:
        return self.resolve_path(self.storage.log_path)


def _apply_persona(cfg: Config) -> None:
    """如果配置了 persona，就加载 personas/<name>.md 作为系统提示。"""
    name = (cfg.persona or "").strip()
    if not name:
        return
    path = cfg.root / "personas" / f"{name}.md"
    if path.exists():
        cfg.llm.system_prompt = path.read_text(encoding="utf-8").strip()
        cfg.warnings.append(f"已加载人格：personas/{name}.md")
    else:
        cfg.warnings.append(f"找不到人格文件 personas/{name}.md，使用 llm.system_prompt")


def load_dotenv(path: Path | None = None) -> None:
    """极简 .env 加载：不覆盖已存在的环境变量。"""
    env_path = path or Path(".env")
    if not env_path.exists():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]
        os.environ.setdefault(key, val)


# --------------------------------------------------------------------------
# 内部工具
# --------------------------------------------------------------------------

def _expand_env(value: Any) -> Any:
    if isinstance(value, str):
        def repl(m: re.Match[str]) -> str:
            name, default = m.group(1), m.group(2)
            env = os.environ.get(name)
            if env is not None:
                return env
            if default is not None:
                return default
            raise ConfigError(f"环境变量 {name} 未设置（可在 .env 中配置，或写成 ${{{name}:-默认值}}）")
        return _ENV_RE.sub(repl, value)
    if isinstance(value, list):
        return [_expand_env(v) for v in value]
    if isinstance(value, dict):
        return {k: _expand_env(v) for k, v in value.items()}
    return value


def _flatten_platform(data: dict[str, Any]) -> dict[str, Any]:
    """把嵌套的 platform: {type, onebot, wechat} 摊平成 Config 字段。"""
    data = dict(data)
    platform = data.pop("platform", None)
    if isinstance(platform, dict):
        ptype = platform.get("type", "onebot")
        data.setdefault("platform_type", ptype)
        if "onebot" in platform:
            data.setdefault("onebot", platform["onebot"])
        if "wechat" in platform:
            data.setdefault("wechat", platform["wechat"])
        extra = set(platform) - {"type", "onebot", "wechat"}
        if extra:
            data.setdefault("_warnings", []).append(f"platform 下未知字段：{sorted(extra)}")
    return data


def _build(cls: type, data: Any, root: Path) -> Any:
    """按 dataclass 字段递归构造，未知字段记入 warnings。"""
    if not isinstance(data, dict):
        raise ConfigError(f"{cls.__name__} 配置必须是键值映射，实际为 {type(data).__name__}")
    hints = get_type_hints(cls)
    known = {f.name for f in fields(cls)}
    unknown = set(data) - known - {"_warnings"}
    if unknown:
        data = dict(data)
        data.setdefault("_warnings", [])
        data["_warnings"] = list(data["_warnings"]) + [f"{cls.__name__} 下未知字段：{sorted(unknown)}"]

    kwargs: dict[str, Any] = {}
    for f in fields(cls):
        if f.name not in data or f.name in {"root", "warnings"}:
            continue
        raw = data[f.name]
        hint = hints[f.name]
        if is_dataclass(hint):
            kwargs[f.name] = _build(hint, raw, root)
        else:
            kwargs[f.name] = _coerce(raw, hint, f.name)

    obj = cls(**kwargs)
    for w in data.get("_warnings", []):
        obj.warnings.append(str(w))
    return obj


def _coerce(value: Any, hint: Any, name: str) -> Any:
    origin = get_origin(hint)
    if origin is list:
        if value is None:
            return []
        if not isinstance(value, list):
            raise ConfigError(f"{name} 应该是列表，实际为 {type(value).__name__}")
        args = get_args(hint)
        if args and is_dataclass(args[0]):
            return [_build(args[0], v, Path(".")) for v in value if isinstance(v, dict)]
        return [str(v) if not isinstance(v, str) else v for v in value]
    if origin is dict:
        return dict(value or {})
    if hint is bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(value)
    if hint is int:
        try:
            return int(value)
        except (TypeError, ValueError) as exc:
            raise ConfigError(f"{name} 应该是整数，实际为 {value!r}") from exc
    if hint is float:
        try:
            return float(value)
        except (TypeError, ValueError) as exc:
            raise ConfigError(f"{name} 应该是数字，实际为 {value!r}") from exc
    if hint is str:
        if value is None:
            return ""
        return str(value)
    return value


def _parse_hour_range(expr: str) -> tuple[int, int] | None:
    """解析 "23:30-08:00"，返回当天分钟数区间；支持跨午夜。"""
    m = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})\s*", expr)
    if not m:
        return None
    h1, m1, h2, m2 = (int(x) for x in m.groups())
    if h1 > 23 or h2 > 23 or m1 > 59 or m2 > 59:
        return None
    return h1 * 60 + m1, h2 * 60 + m2
