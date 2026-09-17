from __future__ import annotations

import json
from typing import Optional

from openai import AsyncOpenAI

from app.core.config import get_settings
from app.models.schemas import LLMConfig


class LLMService:
    """OpenAI-compatible LLM client. Works with OpenAI / Claude gateway / Qwen / local proxies."""

    def __init__(self) -> None:
        self.settings = get_settings()

    def load_config(self) -> LLMConfig:
        path = self.settings.config_path
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            return LLMConfig.model_validate(data)
        return LLMConfig(
            provider=self.settings.llm_provider,
            model=self.settings.llm_model,
            api_key=self.settings.llm_api_key,
            base_url=self.settings.llm_base_url,
            max_tokens=self.settings.llm_max_tokens,
            system_prompt=self.settings.system_prompt,
        )

    def save_config(self, config: LLMConfig) -> LLMConfig:
        path = self.settings.config_path
        path.parent.mkdir(parents=True, exist_ok=True)
        # Mask nothing on disk for local MVP; UI should treat key carefully.
        path.write_text(config.model_dump_json(indent=2), encoding="utf-8")
        return config

    def _client(self, config: Optional[LLMConfig] = None) -> AsyncOpenAI:
        cfg = config or self.load_config()
        return AsyncOpenAI(api_key=cfg.api_key or "EMPTY", base_url=cfg.base_url)

    async def chat(
        self,
        user_prompt: str,
        *,
        system_prompt: Optional[str] = None,
        temperature: Optional[float] = None,
        config: Optional[LLMConfig] = None,
    ) -> str:
        cfg = config or self.load_config()
        client = self._client(cfg)
        resp = await client.chat.completions.create(
            model=cfg.model,
            temperature=0,
            max_tokens=cfg.max_tokens,
            messages=[
                {"role": "system", "content": system_prompt or cfg.system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
        choice = resp.choices[0]
        if choice.finish_reason != "stop":
            raise ValueError("模型未完整返回内容，请检查输出长度限制或服务状态后重试")
        text = (choice.message.content or "").strip()
        if not text:
            raise ValueError("模型返回空内容，请重试")
        return text

    async def test_connection(self, config: Optional[LLMConfig] = None) -> dict:
        cfg = config or self.load_config()
        try:
            text = await self.chat(
                "Reply with exactly: OK",
                system_prompt="You are a connection test assistant.",
                temperature=0,
                config=cfg,
            )
            return {"ok": True, "message": text[:200]}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "message": str(exc)}
