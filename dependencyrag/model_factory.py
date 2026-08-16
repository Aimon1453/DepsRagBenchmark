"""
Shared model factory for DepsRAG.
Provides a single source of truth for creating LLM model instances.
"""

import os
from typing import Optional
from agno.models.openai import OpenAIChat
from agno.models.openai.like import OpenAILike
from agno.models.azure import AzureOpenAI
from agno.models.google import Gemini
from agno.models.base import Model

# Providers that speak the OpenAI wire format but live behind their own base URL
# (self-hosted vLLM/SGLang, DeepSeek, Moonshot/Kimi, ...). Each reads its own
# env pair first so several can be configured side by side in one .env; the
# OPENAI_COMPAT_* pair is the shared fallback.
OPENAI_COMPATIBLE_PROVIDERS = ("openai_like", "local", "vllm", "deepseek", "kimi")

_PROVIDER_ENV_PREFIX = {
    "vllm": "VLLM",
    "local": "VLLM",
    "deepseek": "DEEPSEEK",
    "kimi": "KIMI",
}

_DEFAULT_BASE_URL = {
    "deepseek": "https://api.deepseek.com/v1",
    "kimi": "https://api.moonshot.cn/v1",
}


def _compat_settings(provider: str, base_url: Optional[str], api_key: Optional[str]):
    """Resolve endpoint and key for an OpenAI-compatible provider."""
    prefix = _PROVIDER_ENV_PREFIX.get(provider)
    if prefix:
        base_url = base_url or os.getenv(f"{prefix}_BASE_URL")
        api_key = api_key or os.getenv(f"{prefix}_API_KEY")
    base_url = base_url or os.getenv("OPENAI_COMPAT_BASE_URL") or _DEFAULT_BASE_URL.get(provider)
    api_key = api_key or os.getenv("OPENAI_COMPAT_API_KEY")
    return base_url, api_key


def create_model(
    model_id: str = "gpt-4o",
    provider: Optional[str] = None,
    base_url: Optional[str] = None,
    api_key: Optional[str] = None,
) -> Model:
    """
    Create appropriate LLM model based on provider or auto-detect from environment.

    Args:
        model_id: Model ID to use (default: gpt-4o)
        provider: Explicit provider ("openai", "azure", "google", or one of
            OPENAI_COMPATIBLE_PROVIDERS) or None for auto-detect
        base_url: Endpoint override for OpenAI-compatible providers; falls back to
            OPENAI_COMPAT_BASE_URL
        api_key: Key for OpenAI-compatible providers; falls back to
            OPENAI_COMPAT_API_KEY. Self-hosted servers usually ignore it, so an
            unset key is not an error for these providers.

    Returns:
        Model: Configured model instance
    """
    # OpenAI-compatible endpoints: OpenAILike defaults api_key to "not-provided",
    # so a keyless self-hosted server does not trip OpenAIChat's auth check.
    if provider in OPENAI_COMPATIBLE_PROVIDERS:
        resolved_url, resolved_key = _compat_settings(provider, base_url, api_key)
        if not resolved_url:
            prefix = _PROVIDER_ENV_PREFIX.get(provider, "OPENAI_COMPAT")
            raise ValueError(
                f"provider '{provider}' needs an endpoint: pass base_url or set {prefix}_BASE_URL"
            )
        return OpenAILike(
            id=model_id,
            base_url=resolved_url,
            api_key=resolved_key or "not-provided",
        )

    # Explicit provider specified
    if provider == "google":
        # Use GOOGLE_MODEL_ID from env if model_id is default
        gemini_model = os.getenv("GOOGLE_MODEL_ID", "gemini-2.0-flash-exp") if model_id == "gpt-4o" else model_id
        return Gemini(id=gemini_model)
    elif provider == "azure":
        deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT") or os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME") or model_id
        return AzureOpenAI(
            id=model_id,
            azure_deployment=deployment,
        )
    elif provider == "openai":
        kwargs = {k: v for k, v in (("base_url", base_url), ("api_key", api_key)) if v}
        return OpenAIChat(id=model_id, **kwargs)

    # Auto-detect based on environment variables
    if os.getenv("AZURE_OPENAI_API_KEY"):
        deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT") or os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME") or model_id
        return AzureOpenAI(
            id=model_id,
            azure_deployment=deployment,
        )
    elif os.getenv("GOOGLE_API_KEY"):
        gemini_model = os.getenv("GOOGLE_MODEL_ID", "gemini-2.0-flash-exp") if model_id == "gpt-4o" else model_id
        return Gemini(id=gemini_model)

    # Default to OpenAI
    return OpenAIChat(id=model_id)
