from __future__ import annotations

from fastapi import APIRouter

from app.models.schemas import LLMConfig
from app.services.llm import LLMService

router = APIRouter(prefix="/api/llm", tags=["llm"])
service = LLMService()


@router.get("/config")
async def get_config() -> LLMConfig:
    cfg = service.load_config()
    masked = cfg.model_copy()
    if masked.api_key:
        masked.api_key = (
            masked.api_key[:4] + "****" + masked.api_key[-4:]
            if len(masked.api_key) > 8
            else "****"
        )
    return masked


@router.get("/config/raw")
async def get_config_raw() -> LLMConfig:
    """Full config for editing form (local trusted environment)."""
    return service.load_config()


@router.put("/config")
async def save_config(config: LLMConfig) -> LLMConfig:
    current = service.load_config()
    if "****" in (config.api_key or ""):
        config.api_key = current.api_key
    if "****" in (config.translation_api_key or ""):
        config.translation_api_key = current.translation_api_key
    if "****" in (config.vision_api_key or ""):
        config.vision_api_key = current.vision_api_key
    return service.save_config(config)


@router.post("/test")
async def test_connection(config: LLMConfig | None = None):
    if config and config.api_key and "****" in config.api_key:
        current = service.load_config()
        config.api_key = current.api_key
    if config and config.vision_api_key and "****" in config.vision_api_key:
        current = service.load_config()
        config.vision_api_key = current.vision_api_key
    return await service.test_connection(config)
