from __future__ import annotations

import json
from typing import Optional

from openai import AsyncOpenAI

from app.core.config import get_settings
from app.models.schemas import LLMConfig


class LLMService:
    """OpenAI-compatible LLM client. Works with OpenAI / Claude gateway / Qwen / local proxies."""

    # Models that are dedicated translation models and do NOT support the
    # "system" role — they only accept user/assistant messages.
    _TRANSLATION_ONLY_MODELS = {
        "qwen-mt-plus", "qwen-mt-turbo",   # Alibaba translation models
        "alibaba-translate",
    }

    def __init__(self) -> None:
        self.settings = get_settings()
        # Persistent client pool keyed by (api_key, base_url) for HTTP connection
        # reuse (keep-alive).  Avoids a new TCP+TLS handshake per translation call.
        self._clients: dict[tuple[str, str], AsyncOpenAI] = {}

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
        key = (cfg.api_key or "EMPTY", cfg.base_url)
        if key not in self._clients:
            self._clients[key] = AsyncOpenAI(api_key=key[0], base_url=key[1])
        return self._clients[key]

    def _build_messages(
        self, model: str, user_prompt: str, system_prompt: Optional[str], default_system: str,
    ) -> list[dict[str, str]]:
        """Build chat messages, adapting for models that don't support 'system' role.

        Dedicated translation models (e.g. qwen-mt-plus) only accept user/assistant
        roles, so we merge the system prompt into the user message.
        """
        sys_text = system_prompt or default_system
        if model in self._TRANSLATION_ONLY_MODELS:
            # Merge system + user into a single user message
            combined = f"{sys_text}\n\n{user_prompt}" if sys_text else user_prompt
            return [{"role": "user", "content": combined}]
        return [
            {"role": "system", "content": sys_text},
            {"role": "user", "content": user_prompt},
        ]

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
        messages = self._build_messages(cfg.model, user_prompt, system_prompt, cfg.system_prompt)
        resp = await client.chat.completions.create(
            model=cfg.model,
            temperature=0,
            max_tokens=cfg.max_tokens,
            messages=messages,
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
