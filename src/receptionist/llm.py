"""Chat completions with tool calling.

By default the LLM is reached through my llm-gateway project, embedded in-process: Groq gpt-oss-120b with fallback
to gpt-oss-20b, retries, and per-call cost tracking. `HTTPLLM` talks to any OpenAI-compatible endpoint instead, and
`ScriptedLLM` replays fixed answers in tests.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx


@dataclass
class LLMReply:
    ok: bool
    message: dict[str, Any] = field(default_factory=dict)  # {"content": str | None, "tool_calls": [...]}
    model: str = ""
    cost_usd: float = 0.0
    latency_ms: float = 0.0
    error: str = ""


class LLM(Protocol):
    name: str

    async def chat(self, messages: list[dict], tools: list[dict], temperature: float = 0.2) -> LLMReply: ...


def _body(route: str, messages: list[dict], tools: list[dict], temperature: float) -> dict[str, Any]:
    body: dict[str, Any] = {
        "model": route,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": 900,
        "reasoning_effort": "low",
    }
    if tools:
        body["tools"] = tools
        body["tool_choice"] = "auto"
    return body


def _ms(t0: float) -> float:
    return round((time.perf_counter() - t0) * 1000, 1)


class GatewayLLM:
    name = "Groq via llm-gateway"

    def __init__(self, route: str = "smart", max_retry_after_s: float = 8):
        from llmgw.config import GatewayConfig
        from llmgw.gateway import Gateway

        cfg = GatewayConfig.model_validate(
            {
                "providers": {
                    "groq": {
                        "type": "openai",
                        "base_url": "https://api.groq.com/openai/v1",
                        "api_key_env": "GROQ_API_KEY",
                        "timeout_s": 30,
                        "models": {
                            "openai/gpt-oss-120b": {"input_per_1m": 0.15, "output_per_1m": 0.60},
                            "openai/gpt-oss-20b": {"input_per_1m": 0.075, "output_per_1m": 0.30},
                        },
                    }
                },
                "routes": {
                    "smart": ["groq/openai/gpt-oss-120b", "groq/openai/gpt-oss-20b"],
                    "fast": ["groq/openai/gpt-oss-20b", "groq/openai/gpt-oss-120b"],
                    # evaluation: one model per role, no fallback, so results are attributable
                    "eval-agent": ["groq/openai/gpt-oss-120b"],
                    "eval-caller": ["groq/openai/gpt-oss-20b"],
                },
                "default_route": route,
                "database": ":memory:",
                "require_auth": False,
                "retry": {"attempts_per_target": 3, "backoff_s": 0.8, "max_retry_after_s": max_retry_after_s},
                "cache": {"enabled": False},
            }
        )
        self.route = route
        self.gw = Gateway(cfg)

    async def chat(self, messages: list[dict], tools: list[dict], temperature: float = 0.2) -> LLMReply:
        from llmgw.gateway import GatewayError

        t0 = time.perf_counter()
        try:
            out = await self.gw.chat(_body(self.route, messages, tools, temperature), None)
        except GatewayError as e:
            return LLMReply(False, error=f"{e.status}: {e.message}"[:300], latency_ms=_ms(t0))
        msg = (out.body or {}).get("choices", [{}])[0].get("message", {}) or {}
        return LLMReply(
            True,
            {"content": msg.get("content"), "tool_calls": msg.get("tool_calls") or []},
            out.headers.get("x-gateway-target", ""),
            float(out.headers.get("x-gateway-cost-usd", 0) or 0),
            _ms(t0),
        )


class HTTPLLM:
    name = "OpenAI-compatible HTTP"

    def __init__(self, base_url: str, api_key: str, model: str):
        self.base_url, self.api_key, self.model = base_url.rstrip("/"), api_key, model

    async def chat(self, messages: list[dict], tools: list[dict], temperature: float = 0.2) -> LLMReply:
        t0 = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=45) as c:
                r = await c.post(
                    f"{self.base_url}/chat/completions",
                    json=_body(self.model, messages, tools, temperature),
                    headers={"Authorization": f"Bearer {self.api_key}"},
                )
            r.raise_for_status()
        except httpx.HTTPError as e:
            return LLMReply(False, error=str(e)[:300], latency_ms=_ms(t0))
        msg = r.json()["choices"][0]["message"]
        return LLMReply(True, {"content": msg.get("content"), "tool_calls": msg.get("tool_calls") or []}, self.model,
                        latency_ms=_ms(t0))  # fmt: skip


class ScriptedLLM:
    """Replays messages. Each item is a str (final answer) or a list of (tool_name, args) calls."""

    name = "scripted"

    def __init__(self, script: list[Any]):
        self.script = list(script)
        self.seen: list[list[dict]] = []

    async def chat(self, messages: list[dict], tools: list[dict], temperature: float = 0.2) -> LLMReply:
        import json

        self.seen.append([dict(m) for m in messages])
        if not self.script:
            return LLMReply(False, error="script exhausted")
        step = self.script.pop(0)
        if isinstance(step, str):
            return LLMReply(True, {"content": step, "tool_calls": []}, "scripted")
        calls = [
            {
                "id": f"call_{len(self.seen)}_{i}",
                "type": "function",
                "function": {"name": n, "arguments": json.dumps(a)},
            }
            for i, (n, a) in enumerate(step)
        ]
        return LLMReply(True, {"content": None, "tool_calls": calls}, "scripted")


def from_env() -> LLM | None:
    if os.getenv("LLM_BASE_URL"):
        return HTTPLLM(os.environ["LLM_BASE_URL"], os.getenv("LLM_API_KEY", ""), os.getenv("LLM_MODEL", "smart"))
    if os.getenv("GROQ_API_KEY"):
        return GatewayLLM()
    return None
