"""OpenAI 兼容的 Chat Completions 客户端。

适配 DeepSeek / 通义 / Kimi / 硅基流动 / one-api 中转 / Ollama 等，
只需要 base_url + api_key + model。
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from typing import Any

import httpx

from .config import LLMConfig

log = logging.getLogger("aibot.llm")


class LLMError(RuntimeError):
    """模型调用失败（已重试）。"""


class LLMClient:
    def __init__(self, cfg: LLMConfig) -> None:
        self.cfg = cfg
        self.last_usage: dict[str, Any] = {}
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(cfg.timeout_seconds, connect=15.0),
            limits=httpx.Limits(max_connections=8, max_keepalive_connections=4),
        )

    @property
    def endpoint(self) -> str:
        base = self.cfg.base_url.rstrip("/")
        if base.endswith("/chat/completions"):
            return base
        return f"{base}/chat/completions"

    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        attempts: int = 3,
    ) -> str:
        payload: dict[str, Any] = {
            "model": self.cfg.model,
            "messages": messages,
            "temperature": self.cfg.temperature if temperature is None else temperature,
            "max_tokens": self.cfg.max_tokens if max_tokens is None else max_tokens,
            "stream": False,
        }
        headers = {
            "Authorization": f"Bearer {self.cfg.api_key}",
            "Content-Type": "application/json",
        }
        headers.update(self.cfg.extra_headers or {})

        last_error = ""
        for attempt in range(1, attempts + 1):
            started = time.monotonic()
            try:
                resp = await self._client.post(self.endpoint, json=payload, headers=headers)
            except httpx.TimeoutException:
                last_error = f"请求超时（{self.cfg.timeout_seconds:g}s）"
            except httpx.HTTPError as exc:
                last_error = f"网络错误：{exc.__class__.__name__}: {exc}"
            else:
                if resp.status_code == 200:
                    text = self._extract(resp)
                    self.last_usage = {
                        "usage": (resp.json() or {}).get("usage", {}),
                        "elapsed_ms": int((time.monotonic() - started) * 1000),
                        "model": self.cfg.model,
                    }
                    return text
                body = (resp.text or "")[:300].replace("\n", " ")
                last_error = f"HTTP {resp.status_code}: {body}"
                if 400 <= resp.status_code < 500 and resp.status_code not in (408, 429):
                    raise LLMError(last_error)  # 参数/鉴权错误，重试无意义

            if attempt < attempts:
                backoff = min(8.0, 1.2 * (2 ** (attempt - 1))) + random.uniform(0, 0.4)
                log.warning("模型调用失败（第 %d 次）：%s，%.1fs 后重试", attempt, last_error, backoff)
                await asyncio.sleep(backoff)

        raise LLMError(last_error or "未知错误")

    async def ping(self) -> tuple[bool, str]:
        """连通性自检，返回 (是否成功, 说明)。"""
        if not self.cfg.api_key:
            return False, "未配置 api_key"
        started = time.monotonic()
        try:
            reply = await self.chat(
                [{"role": "user", "content": "只回复两个字：在线"}],
                temperature=0.0,
                max_tokens=16,
                attempts=1,
            )
        except Exception as exc:  # noqa: BLE001 - 自检需要把所有异常转成可读文本
            return False, str(exc)
        elapsed = int((time.monotonic() - started) * 1000)
        return True, f"{self.cfg.model} 响应 {elapsed}ms：{(reply or '').strip()[:40]}"

    async def close(self) -> None:
        await self._client.aclose()

    @staticmethod
    def _extract(resp: httpx.Response) -> str:
        try:
            data = resp.json()
        except ValueError as exc:
            raise LLMError(f"响应不是合法 JSON：{resp.text[:200]}") from exc
        choices = data.get("choices") or []
        if not choices:
            raise LLMError(f"响应缺少 choices：{str(data)[:200]}")
        content = (choices[0].get("message") or {}).get("content")
        if isinstance(content, list):
            content = "".join(
                part.get("text", "") for part in content if isinstance(part, dict)
            )
        if not isinstance(content, str) or not content.strip():
            raise LLMError("模型返回了空内容")
        return content.strip()
